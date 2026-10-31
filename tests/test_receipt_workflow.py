import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram.error import BadRequest, NetworkError, TimedOut

from bilancio.client import (
    BilancioClientError,
    PortalOption,
    PortalSubmissionResult,
    TransactionDirection,
    default_unit_for_account,
)
from bot.receipt_workflow import ReceiptWorkflow, parse_italian_date
from bot import messages as texts
from config import Settings
from db.database import Database
from storage.archive import FileArchive
from storage.pending import PendingReceiptStore
from vision.receipt_schema import ReceiptData
from vision.receipt_schema import ReceiptParseError
from vision.gemini_client import (
    AnalysisPhase,
    AnalysisProgress,
    LocalVisionError,
    VisionFailureCode,
)
from vision.preprocess import ImagePreprocessingError


class FakeAnalyzer:
    def __init__(self, receipt: ReceiptData) -> None:
        self.receipt = receipt

    async def analyze(self, file_bytes: bytes, file_name: str) -> ReceiptData:
        self.received_bytes = file_bytes
        self.received_name = file_name
        return self.receipt


class FakeBilancioClient:
    def __init__(self) -> None:
        self.submitted = []
        self.requested_dates = []

    async def list_accounts(self, *, transaction_date=None):
        self.requested_dates.append(transaction_date)
        return [PortalOption("account-1", "Cassa dinamica")]

    async def list_transaction_types(
        self,
        account: PortalOption,
        direction: TransactionDirection,
        *,
        transaction_date=None,
        accounting_voice="Voci Scout",
    ):
        self.requested_dates.append(transaction_date)
        return [PortalOption("type-1", f"Tipo {direction.value}")]

    async def list_units(
        self,
        account: PortalOption,
        direction: TransactionDirection,
        transaction_type: PortalOption,
        *,
        transaction_date=None,
        accounting_voice="Voci Scout",
    ):
        self.requested_dates.append(transaction_date)
        return [PortalOption("unit-1", "Unità visibile")]

    async def list_categories(
        self,
        account: PortalOption,
        direction: TransactionDirection,
        transaction_type: PortalOption,
        unit: PortalOption,
        *,
        transaction_date=None,
        accounting_voice="Voci Scout",
    ):
        self.requested_dates.append(transaction_date)
        return [PortalOption("category-1", "Categoria visibile")]

    async def create_expense(
        self,
        expense,
        *,
        dry_run: bool,
    ) -> PortalSubmissionResult:
        self.submitted.append((expense, dry_run))
        return PortalSubmissionResult(verified=True, dry_run=dry_run)


def make_settings() -> Settings:
    return Settings.from_env(
        {
            "BOT_TOKEN": "test-token-placeholder",
            "ALLOWED_USER_IDS": "12345",
            "ADMIN_USER_ID": "12345",
            "STORAGE_PATH": "/scontrini",
            "DRY_RUN": "true",
        }
    )


class ReceiptWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = Database(self.root / "bot.sqlite3")
        self.database.initialize()
        self.analyzer = FakeAnalyzer(
            ReceiptData(
                date=date(2026, 10, 26),
                merchant="Negozio",
                total_amount=Decimal("12.50"),
                currency="EUR",
                confidence=0.95,
            )
        )
        self.workflow = ReceiptWorkflow(
            settings=make_settings(),
            database=self.database,
            analyzer=self.analyzer,
            pending_store=PendingReceiptStore(self.root / "pending"),
            archive=FileArchive(self.root / "archive"),
            bilancio_client=None,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_progress_updates_original_message_without_exposing_partial_data(self) -> None:
        async def analyze_with_progress(file_bytes, file_name, *, on_progress):
            await asyncio.sleep(0.025)
            on_progress(AnalysisProgress(AnalysisPhase.READING_IMAGE))
            await asyncio.sleep(0.025)
            on_progress(AnalysisProgress(AnalysisPhase.WRITING, 42))
            await asyncio.sleep(0.025)
            return self.analyzer.receipt

        self.workflow._analyzer = SimpleNamespace(analyze_with_progress=analyze_with_progress)
        progress = SimpleNamespace(edit_text=AsyncMock())
        with patch("bot.receipt_workflow._ANALYSIS_PROGRESS_INTERVAL_SECONDS", 0.01):
            result = await self.workflow._analyze_receipt(b"image", "test.png", progress)
        rendered = [call.args[0] for call in progress.edit_text.await_args_list]
        self.assertTrue(any("Non ho ancora ricevuto dati" in text for text in rendered))
        self.assertTrue(any("OCR locale in corso" in text for text in rendered))
        self.assertTrue(any("42 caratteri finora" in text for text in rendered))
        self.assertEqual(rendered[-1], texts.ANALYSIS_FINISHED)
        self.assertFalse(any("Negozio" in text for text in rendered))
        self.assertEqual(result, self.analyzer.receipt)
        count = progress.edit_text.await_count
        await asyncio.sleep(0.025)
        self.assertEqual(progress.edit_text.await_count, count)

    async def test_progress_notification_failure_does_not_abort_analysis(self) -> None:
        async def analyze(file_bytes, file_name):
            await asyncio.sleep(0.035)
            return self.analyzer.receipt

        self.workflow._analyzer = SimpleNamespace(analyze=analyze)
        progress = SimpleNamespace(edit_text=AsyncMock(side_effect=NetworkError("offline")))
        with (
            patch("bot.receipt_workflow._ANALYSIS_PROGRESS_INTERVAL_SECONDS", 0.01),
            self.assertLogs("bot.receipt_workflow", level="WARNING"),
        ):
            result = await self.workflow._analyze_receipt(b"image", "test.png", progress)
        self.assertEqual(result, self.analyzer.receipt)

    async def test_timeout_stops_progress_monitor_and_preserves_manual_recovery(self) -> None:
        async def analyze_with_progress(file_bytes, file_name, *, on_progress):
            on_progress(AnalysisProgress(AnalysisPhase.WRITING, 1))
            await asyncio.Event().wait()

        self.workflow._analyzer = SimpleNamespace(analyze_with_progress=analyze_with_progress)
        self.workflow._settings = replace(make_settings(), ai_timeout_seconds=1)
        message, update, context = self.make_photo_update(b"progress-timeout")
        progress = SimpleNamespace(edit_text=AsyncMock())
        message.reply_text.return_value = progress
        with patch("bot.receipt_workflow._ANALYSIS_PROGRESS_INTERVAL_SECONDS", 0.01):
            await self.workflow.receive_receipt(update, context)
        self.assertIn(texts.MANUAL_MERCHANT, message.reply_text.await_args.args[0])
        state = self.database.get_conversation_state(12345)
        self.assertEqual(state[0], "awaiting_receipt_data")
        payload = self.database.get_expense(state[1]["expense_id"]).payload
        self.assertTrue(Path(payload["pending_path"]).is_file())
        count = progress.edit_text.await_count
        await asyncio.sleep(0.03)
        self.assertEqual(progress.edit_text.await_count, count)

    async def test_analyzes_photo_and_persists_confirmation_state(self) -> None:
        message, update, context = self.make_photo_update(b"private receipt")

        await self.workflow.receive_receipt(update, context)

        state = self.database.get_conversation_state(12345)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state[0], "awaiting_receipt_data")
        expense_id = state[1]["expense_id"]
        expense = self.database.get_expense(str(expense_id))
        self.assertIsNotNone(expense)
        assert expense is not None
        self.assertEqual(expense.payload["merchant"], "Negozio")
        self.assertTrue(Path(str(expense.payload["pending_path"])).is_file())
        self.assertEqual(state[1]["input_field"], "merchant")
        self.assertIn("Valore letto: Negozio", message.reply_text.await_args.args[0])
        self.assertIsNotNone(message.reply_text.await_args.kwargs["reply_markup"])

    async def test_requests_missing_date_before_confirmation(self) -> None:
        self.analyzer.receipt = ReceiptData(
            date=None,
            merchant="Negozio",
            total_amount=Decimal("12.50"),
            confidence=0.95,
        )
        message, update, context = self.make_photo_update(b"private receipt")

        await self.workflow.receive_receipt(update, context)

        state = self.database.get_conversation_state(12345)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state[0], "awaiting_receipt_data")
        self.assertIn(texts.MANUAL_MERCHANT, message.reply_text.await_args.args[0])

    async def test_persists_manual_recovery_before_analysis_and_bounds_total_wait(self) -> None:
        async def stalled_analysis(file_bytes: bytes, file_name: str) -> ReceiptData:
            state = self.database.get_conversation_state(12345)
            self.assertIsNotNone(state)
            self.assertEqual(state[0], "awaiting_receipt_data")
            stored = self.database.get_expense(state[1]["expense_id"])
            self.assertTrue(Path(stored.payload["pending_path"]).is_file())
            self.assertIsNone(stored.payload["date"])
            await asyncio.Event().wait()
            return self.analyzer.receipt

        self.workflow._settings = replace(
            make_settings(), ai_timeout_seconds=1
        )
        self.workflow._analyzer = SimpleNamespace(analyze=stalled_analysis)
        message, update, context = self.make_photo_update(b"stalled-receipt")
        await asyncio.wait_for(self.workflow.receive_receipt(update, context), timeout=3)

        reply = message.reply_text.await_args_list[-2].args[0]
        self.assertIn("Inseriamo i dati a mano", reply)
        self.assertIn("Codice: timeout", reply)
        self.assertIn(texts.MANUAL_MERCHANT, message.reply_text.await_args.args[0])
        state = self.database.get_conversation_state(12345)
        stored = self.database.get_expense(state[1]["expense_id"])
        self.assertTrue(Path(stored.payload["pending_path"]).is_file())

    async def test_photo_skips_direction_and_requests_expense_types(self) -> None:
        portal = FakeBilancioClient()
        portal.list_transaction_types = AsyncMock(
            return_value=[PortalOption("type-1", "Tipo uscita")]
        )
        self.workflow._bilancio = portal
        _, update, context = self.make_photo_update(b"automatic-direction")
        await self.workflow.receive_receipt(update, context)
        user = update.effective_user
        await self._tap(self.workflow, user, "receipt:confirm")
        query = await self._tap(self.workflow, user, "receipt:choose:0")
        state, data = self.database.get_conversation_state(user.id)
        self.assertEqual(state, "awaiting_transaction_type")
        self.assertEqual(data["direction"], "uscita")
        self.assertEqual(
            portal.list_transaction_types.await_args.args[1],
            TransactionDirection.EXPENSE,
        )
        self.assertNotIn(
            texts.DIRECTION_PROMPT,
            [call.args[0] for call in query.edit_message_text.await_args_list],
        )

    async def test_telegram_transfer_failure_does_not_start_analysis_or_store_files(self) -> None:
        for stage in ("get_file", "download", "progress_message"):
            for error_type in (TimedOut, NetworkError):
                with self.subTest(stage=stage, error=error_type.__name__):
                    message, update, context = self.make_photo_update(b"receipt")
                    if stage == "get_file":
                        context.bot.get_file.side_effect = error_type("private detail")
                    elif stage == "download":
                        context.bot.get_file.return_value.download_as_bytearray.side_effect = (
                            error_type("private detail")
                        )
                    else:
                        message.reply_text.side_effect = [
                            error_type("private detail"), None
                        ]
                    with self.assertLogs("bot.receipt_workflow", level="WARNING") as logs:
                        await self.workflow.receive_receipt(update, context)

                    self.assertIn(stage, logs.output[0])
                    self.assertNotIn("private detail", "\n".join(logs.output))
                    self.assertEqual(
                        message.reply_text.await_args.args[0],
                        texts.RECEIPT_TRANSFER_FAILED,
                    )
                    self.assertFalse(hasattr(self.analyzer, "received_bytes"))
                    self.assertIsNone(self.database.get_conversation_state(12345))
                    self.assertFalse(list((self.root / "pending").glob("*")))

        message, update, context = self.make_photo_update(b"receipt")
        await self.workflow.receive_receipt(update, context)
        self.assertIsNotNone(self.database.get_conversation_state(12345))
        context.bot.get_file.assert_awaited_once_with(
            "telegram-file-id", connect_timeout=30, read_timeout=120
        )
        context.bot.get_file.return_value.download_as_bytearray.assert_awaited_once_with(
            connect_timeout=30, read_timeout=120
        )

    async def test_logs_failure_to_notify_transfer_error_without_private_details(self) -> None:
        message, update, context = self.make_photo_update(b"receipt")
        context.bot.get_file.side_effect = TimedOut("private detail")
        message.reply_text.side_effect = TimedOut("private notification")

        with self.assertLogs("bot.receipt_workflow", level="WARNING") as logs:
            await self.workflow.receive_receipt(update, context)

        self.assertEqual(len(logs.output), 2)
        self.assertIn("Could not notify receipt transfer failure", logs.output[1])
        self.assertNotIn("private", "\n".join(logs.output))
        self.assertFalse(hasattr(self.analyzer, "received_bytes"))

    async def test_rejects_repeated_file_hash(self) -> None:
        message, update, context = self.make_photo_update(b"same receipt")
        await self.workflow.receive_receipt(update, context)
        self.database.delete_conversation_state(12345)
        second_message, second_update, second_context = self.make_photo_update(
            b"same receipt"
        )

        await self.workflow.receive_receipt(second_update, second_context)

        self.assertIn(
            "già ricevuto",
            second_message.reply_text.await_args.args[0],
        )

    async def test_portal_choices_are_loaded_dynamically(self) -> None:
        portal = FakeBilancioClient()
        workflow = ReceiptWorkflow(
            settings=make_settings(),
            database=self.database,
            analyzer=self.analyzer,
            pending_store=PendingReceiptStore(self.root / "pending"),
            archive=FileArchive(self.root / "archive"),
            bilancio_client=portal,
        )
        message, receipt_update, receipt_context = self.make_photo_update(b"receipt")
        await workflow.receive_receipt(receipt_update, receipt_context)
        for _ in range(3):
            proposal = await self._tap(workflow, receipt_update.effective_user, "receipt:accept")
        account_markup = proposal.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(
            account_markup.inline_keyboard[0][0].text,
            "Cassa dinamica",
        )

        account_query = self.make_query("receipt:choose:0")
        await workflow.handle_callback(
            SimpleNamespace(
                callback_query=account_query,
                effective_user=SimpleNamespace(id=12345),
            ),
            SimpleNamespace(),
        )
        type_markup = account_query.edit_message_text.await_args.kwargs["reply_markup"]
        self.assertEqual(type_markup.inline_keyboard[0][0].text, "Tipo uscita")
        self.assertEqual(
            self.database.get_conversation_state(12345)[0],
            "awaiting_transaction_type",
        )
        self.assertEqual(portal.requested_dates[0], date(2026, 10, 26))
        self.assertEqual(portal.requested_dates[1], date(2026, 10, 26))
        self.assertEqual(
            self.database.get_conversation_state(12345)[1]["accounting_voice"],
            "Voci Scout",
        )

    async def test_manual_expense_skips_receipt_ai_and_archive(self) -> None:
        portal = FakeBilancioClient()
        portal.list_accounts = AsyncMock(
            return_value=[PortalOption("account-1", "Cassa Co.ca")]
        )
        workflow = ReceiptWorkflow(
            settings=replace(make_settings(), dry_run=False),
            database=self.database,
            analyzer=None,
            pending_store=PendingReceiptStore(self.root / "pending"),
            archive=FileArchive(self.root / "archive"),
            bilancio_client=portal,
        )
        user = SimpleNamespace(id=12345)
        initial_message = SimpleNamespace(reply_text=AsyncMock())
        await workflow.start_manual_expense(
            SimpleNamespace(message=initial_message, effective_user=user),
            SimpleNamespace(), collect_data=False,
        )
        state = self.database.get_conversation_state(user.id)
        self.assertIsNotNone(state)
        assert state is not None
        expense_id = state[1]["expense_id"]
        self.assertEqual(state[0], "awaiting_confirmation")
        defaults = self.database.get_expense(expense_id).payload
        self.assertEqual(defaults["total_amount"], "0.10")
        self.assertEqual(defaults["merchant"], "Prova Telegram")
        intro = initial_message.reply_text.await_args
        self.assertIn("0,10", intro.args[0])
        buttons = [
            button.callback_data
            for row in intro.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("receipt:edit:total_amount", buttons)
        self.assertNotIn("receipt:edit:suggested_category", buttons)

        for callback_data in (
            "receipt:confirm",
            "receipt:choose:0",
            "receipt:choose:0",
            "receipt:no_notes",
        ):
            query = self.make_query(callback_data)
            await workflow.handle_callback(
                SimpleNamespace(
                    callback_query=query,
                    effective_message=query.message,
                    effective_user=user,
                ),
                SimpleNamespace(),
            )

        stored = self.database.get_expense(expense_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, "submitted")
        self.assertIsNone(stored.archive_path)
        self.assertIsNone(self.database.get_conversation_state(user.id))
        self.assertEqual(len(portal.submitted), 1)
        self.assertTrue(make_settings().dry_run)
        self.assertFalse(portal.submitted[0][1])
        self.assertTrue(portal.submitted[0][0].accounting_voice == "Voci Scout")
        result_text = query.edit_message_text.await_args.args[0]
        query.message.reply_text.assert_not_awaited()
        self.assertTrue(result_text.startswith("✅ Fatto! Spesa salvata e verificata"))
        for expected in (
            "Prova Telegram",
            "0,10 € (entrata)",
            "🏦 Cassa Co.ca",
            "🏷️ Tipo entrata",
            "👥 Gruppo G1",
        ):
            self.assertIn(expected, result_text)

    async def test_result_reports_unit_left_empty_by_portal(self) -> None:
        portal = FakeBilancioClient()
        portal.create_expense = AsyncMock(
            return_value=PortalSubmissionResult(verified=True, unit_left_empty=True)
        )
        workflow = self._manual_portal_workflow(portal)
        user = SimpleNamespace(id=12345)
        await self._start_manual(workflow, user)
        for data in (
            "receipt:confirm",
            "receipt:choose:0",
            "receipt:direction:entrata",
            "receipt:choose:0",
            "receipt:no_notes",
        ):
            query = await self._tap(workflow, user, data)
        result_text = query.edit_message_text.await_args.args[0]
        self.assertIn("(entrata)", result_text)
        self.assertNotIn("👥", result_text)

    async def test_failed_manual_entry_is_not_retried_automatically(self) -> None:
        portal = FakeBilancioClient()
        portal.create_expense = AsyncMock(side_effect=BilancioClientError("Not verified"))
        workflow = self._manual_portal_workflow(portal)
        user = SimpleNamespace(id=12345)
        expense_id = await self._start_manual(workflow, user)
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        for data in (
            "receipt:confirm",
            "receipt:choose:0",
            "receipt:direction:entrata",
            "receipt:choose:0",
            "receipt:no_notes",
        ):
            query = await self._tap(workflow, user, data, context)

        self.assertFalse(portal.create_expense.await_args.kwargs["dry_run"])
        self.assertEqual(self.database.get_expense(expense_id).status, "failed")
        self.assertEqual(
            [
                expense.expense_id
                for expense in self.database.list_retryable_expenses(user.id)
                if expense.status == "queued"
            ],
            [],
        )
        self.assertIsNone(self.database.get_conversation_state(user.id))
        result_text = query.edit_message_text.await_args.args[0]
        query.message.reply_text.assert_not_awaited()
        self.assertIn("non riproverò da solo", result_text)
        self.assertIn("Prova Telegram", result_text)

    async def test_receipts_still_follow_dry_run_setting(self) -> None:
        portal = FakeBilancioClient()
        workflow = ReceiptWorkflow(
            settings=make_settings(),
            database=self.database,
            analyzer=self.analyzer,
            pending_store=PendingReceiptStore(self.root / "pending"),
            archive=FileArchive(self.root / "archive"),
            bilancio_client=portal,
        )
        _, receipt_update, receipt_context = self.make_photo_update(b"receipt-dry")
        await workflow.receive_receipt(receipt_update, receipt_context)
        for data in (
            "receipt:confirm",
            "receipt:choose:0",
            "receipt:choose:0",
            "receipt:no_notes",
        ):
            await self._tap(workflow, SimpleNamespace(id=12345), data)
        self.assertTrue(portal.submitted[0][1])

    async def test_receipt_outcomes_are_explicit_and_never_expose_diagnostics(self) -> None:
        for kind in ("saved", "trial", "existing", "unverified", "unsafe_trial"):
            with self.subTest(kind=kind):
                portal = FakeBilancioClient()
                if kind == "unverified":
                    portal.create_expense = AsyncMock(side_effect=BilancioClientError(
                        "Internal error", diagnostic_path=str(self.root / "private-debug.html"),
                    ))
                else:
                    portal.create_expense = AsyncMock(return_value=PortalSubmissionResult(
                        verified=True, dry_run=kind == "trial",
                        confirmation_id="existing-entry" if kind == "existing" else None,
                    ))
                workflow = ReceiptWorkflow(
                    settings=replace(
                        make_settings(), dry_run=kind in {"trial", "unsafe_trial"},
                        admin_user_id=67890, data_path=self.root / "private-data",
                    ),
                    database=self.database, analyzer=self.analyzer,
                    pending_store=PendingReceiptStore(self.root / "pending"),
                    archive=FileArchive(self.root / "archive"), bilancio_client=portal,
                )
                _, update, context = self.make_photo_update(kind.encode())
                context.bot.send_message = AsyncMock()
                await workflow.receive_receipt(update, context)
                for data in (
                    "receipt:confirm", "receipt:choose:0",
                    "receipt:choose:0",
                    "receipt:no_notes",
                ):
                    query = await self._tap(workflow, SimpleNamespace(id=12345), data, context)
                text = query.edit_message_text.await_args.args[0]
                expected = {
                    "saved": "✅ Fatto! Spesa salvata e verificata",
                    "trial": "🧪 Prova riuscita!",
                    "existing": "ℹ️ Questa spesa era già nel portale",
                    "unverified": "⚠️ Non sono sicuro",
                    "unsafe_trial": "⚠️ Il portale non ha confermato",
                }[kind]
                self.assertTrue(text.startswith(expected), text)
                now = datetime.now(timezone.utc)
                movements = self.database.list_verified_movements(
                    now - timedelta(days=1), now + timedelta(days=1),
                )
                # Only the first ("saved") subcase is a new, verified real registration.
                self.assertEqual(len(movements), 1)
                self.assertEqual(movements[0].amount, Decimal("12.50"))
                for forbidden in ("NAS", "private-data", "private-debug", str(self.root), "Voci"):
                    self.assertNotIn(forbidden, text)
                if kind == "unverified":
                    notification = context.bot.send_message.await_args
                    self.assertEqual(notification.kwargs["chat_id"], 67890)
                    self.assertIn("private-data", notification.kwargs["text"])
                    self.assertIn("private-debug.html", notification.kwargs["text"])

    async def test_legacy_unit_prompt_skips_removed_portal_options(self) -> None:
        portal = FakeBilancioClient()
        portal.list_accounts = AsyncMock(
            return_value=[PortalOption("account-1", "Cassa Co.ca")]
        )
        portal.list_categories = AsyncMock(
            side_effect=BilancioClientError("Category options are unavailable")
        )
        workflow = ReceiptWorkflow(
            settings=make_settings(),
            database=self.database,
            analyzer=None,
            pending_store=PendingReceiptStore(self.root / "pending"),
            archive=FileArchive(self.root / "archive"),
            bilancio_client=portal,
        )
        user = SimpleNamespace(id=12345)
        await workflow.start_manual_expense(
            SimpleNamespace(
                message=SimpleNamespace(reply_text=AsyncMock()),
                effective_user=user,
            ),
            SimpleNamespace(), collect_data=False,
        )
        for callback_data in (
            "receipt:confirm",
            "receipt:choose:0",
        ):
            query = self.make_query(callback_data)
            await workflow.handle_callback(
                SimpleNamespace(
                    callback_query=query,
                    effective_message=query.message,
                    effective_user=user,
                ),
                SimpleNamespace(),
            )

        state = self.database.get_conversation_state(user.id)
        assert state is not None
        state_data = state[1]
        state_data["transaction_type"] = state_data["options"][0]
        state_data["options"] = [
            {"value": "legacy-unit", "label": "Unità precedente"}
        ]
        self.database.save_conversation_state(
            user.id,
            "awaiting_unit",
            state_data,
        )

        query = self.make_query("receipt:choose:0")
        await workflow.handle_callback(
            SimpleNamespace(
                callback_query=query,
                effective_message=query.message,
                effective_user=user,
            ),
            SimpleNamespace(),
        )
        await self._tap(workflow, user, "receipt:no_notes")

        self.assertIsNone(self.database.get_conversation_state(user.id))
        self.assertEqual(
            portal.submitted[0][0].unit, PortalOption("Gruppo G1", "Gruppo G1")
        )
        self.assertIsNone(portal.submitted[0][0].category)
        portal.list_categories.assert_not_awaited()

    async def test_confirmed_receipt_is_archived_and_submitted_in_dry_run(self) -> None:
        portal = FakeBilancioClient()
        portal.list_accounts = AsyncMock(
            return_value=[PortalOption("account-1", "Cassa Co.ca")]
        )
        workflow = ReceiptWorkflow(
            settings=make_settings(),
            database=self.database,
            analyzer=self.analyzer,
            pending_store=PendingReceiptStore(self.root / "pending"),
            archive=FileArchive(self.root / "archive"),
            bilancio_client=portal,
        )
        _, receipt_update, receipt_context = self.make_photo_update(b"receipt")
        await workflow.receive_receipt(receipt_update, receipt_context)
        for _ in range(3):
            await self._tap(workflow, receipt_update.effective_user, "receipt:accept")
        for callback_data in (
            "receipt:confirm",
            "receipt:choose:0",
            "receipt:choose:0",
            "receipt:no_notes",
        ):
            query = self.make_query(callback_data)
            await workflow.handle_callback(
                SimpleNamespace(
                    callback_query=query,
                    effective_message=query.message,
                    effective_user=SimpleNamespace(id=12345),
                ),
                SimpleNamespace(),
            )

        state = self.database.get_conversation_state(12345)
        self.assertIsNone(state)
        expense_id = portal.submitted[0][0]
        self.assertEqual(expense_id.direction, TransactionDirection.EXPENSE)
        self.assertTrue(portal.submitted[0][1])
        stored = self.database.get_expense(
            self.database.list_recent_expenses(12345)[0].expense_id
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, "dry_run")
        self.assertTrue(Path(stored.archive_path).is_file())
        self.assertEqual(
            portal.submitted[0][0].unit,
            PortalOption("Gruppo G1", "Gruppo G1"),
        )

    async def test_flexible_italian_dates(self) -> None:
        for raw, expected in (
            ("29/10/2026", date(2026, 10, 29)), ("29-10-2026", date(2026, 10, 29)),
            ("2-3-26", date(2026, 3, 2)), ("2/3/2026", date(2026, 3, 2)),
            ("02.03.26", date(2026, 3, 2)), (" 2 - 3 - 26 ", date(2026, 3, 2)),
            ("2026-03-02", date(2026, 3, 2)),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(parse_italian_date(raw), expected)
        for raw in ("31-02-2026", "13-13-26", "2026", "2-3", "2-3-226", "oggi"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_italian_date(raw)

    async def test_text_fields_follow_order_and_preserve_all_messages(self) -> None:
        portal = FakeBilancioClient()
        workflow = self._manual_portal_workflow(portal)
        user = SimpleNamespace(id=12345)
        sent_ids = iter(range(100, 200))

        def new_message():
            message = SimpleNamespace(chat_id=12345, message_id=next(sent_ids))
            message.reply_text = AsyncMock(side_effect=lambda *a, **k: new_message())
            return message

        start = new_message()
        context = SimpleNamespace(bot=SimpleNamespace(delete_message=AsyncMock()))
        await workflow.start_manual_expense(
            SimpleNamespace(message=start, effective_user=user), context,
            collect_data=True, direction=TransactionDirection.INCOME,
        )
        form_text = start.reply_text.await_args.args[0]
        self.assertIn(texts.MANUAL_MERCHANT, form_text)
        state, data = self.database.get_conversation_state(12345)
        self.assertEqual(data["form_message_id"], 101)

        typed = new_message()
        typed.text = "Quota"
        await workflow.handle_text(SimpleNamespace(message=typed, effective_user=user), context)
        self.assertIn(texts.MANUAL_DATE, typed.reply_text.await_args.args[0])

        typed = new_message()
        typed.text = "31-02-26"
        await workflow.handle_text(SimpleNamespace(message=typed, effective_user=user), context)
        self.assertIn(texts.MANUAL_FORM_INVALID, typed.reply_text.await_args.args[0])
        self.assertEqual(self.database.get_conversation_state(user.id)[1]["input_field"], "date")

        typed = new_message()
        typed.text = "2-3-26"
        await workflow.handle_text(SimpleNamespace(message=typed, effective_user=user), context)
        form_text = typed.reply_text.await_args.args[0]
        self.assertIn(texts.MANUAL_AMOUNT, form_text)
        payload = self.database.get_expense(data["expense_id"]).payload
        self.assertEqual(payload["date"], "2026-03-02")

        for value in ("-1", "10"):
            typed = new_message()
            typed.text = value
            await workflow.handle_text(SimpleNamespace(message=typed, effective_user=user), context)
        self.assertEqual(self.database.get_conversation_state(12345)[0], "awaiting_account")
        self.assertEqual(portal.submitted, [])
        context.bot.delete_message.assert_not_awaited()

    async def test_manual_one_euro_income_and_expense_with_fake_live_client(self) -> None:
        for direction in TransactionDirection:
            with self.subTest(direction=direction):
                portal = FakeBilancioClient()
                portal.list_accounts = AsyncMock(
                    return_value=[PortalOption("account", "Cassa Co.ca")]
                )
                workflow = ReceiptWorkflow(
                    settings=replace(make_settings(), dry_run=False),
                    database=self.database, analyzer=None,
                    pending_store=PendingReceiptStore(self.root / "pending"),
                    archive=FileArchive(self.root / "archive"),
                    bilancio_client=portal,
                )
                user = SimpleNamespace(id=12345)
                await workflow.start_manual_expense(
                    SimpleNamespace(
                        message=SimpleNamespace(reply_text=AsyncMock()),
                        effective_user=user,
                    ),
                    SimpleNamespace(), collect_data=True, direction=None,
                )
                expense_id = self.database.get_conversation_state(user.id)[1]["expense_id"]
                for value in ("Esempio manuale", "29/10/2026", "1,00"):
                    await workflow.handle_text(
                        SimpleNamespace(
                            message=SimpleNamespace(text=value, reply_text=AsyncMock()),
                            effective_user=user,
                        ),
                        SimpleNamespace(),
                    )
                self.assertEqual(
                    self.database.get_conversation_state(user.id)[0],
                    "awaiting_account",
                )
                for callback in (
                    "receipt:choose:0",
                    f"receipt:direction:{direction.value}",
                    "receipt:choose:0",
                    "receipt:no_notes",
                ):
                    query = self.make_query(callback)
                    await workflow.handle_callback(
                        SimpleNamespace(
                            callback_query=query,
                            effective_message=query.message,
                            effective_user=user,
                        ),
                        SimpleNamespace(),
                    )
                self.assertEqual(len(portal.submitted), 1)
                expense, dry_run = portal.submitted[0]
                self.assertFalse(dry_run)
                self.assertEqual(expense.amount, Decimal("1.00"))
                self.assertEqual(expense.direction, direction)
                self.assertEqual(expense.account.label, "Cassa Co.ca")
                self.assertEqual(
                    expense.unit,
                    PortalOption("Gruppo G1", "Gruppo G1"),
                )
                self.assertIsNone(expense.category)
                self.assertEqual(self.database.get_expense(expense_id).status, "submitted")
                self.assertIsNone(self.database.get_conversation_state(user.id))

    async def test_manual_trial_never_bypasses_mode_and_remains_trial_after_switch(self) -> None:
        portal = FakeBilancioClient()
        workflow = self._manual_portal_workflow(portal)
        self.database.set_test_mode(True)
        user = SimpleNamespace(id=12345)
        message = SimpleNamespace(reply_text=AsyncMock())
        await workflow.start_manual_expense(
            SimpleNamespace(message=message, effective_user=user), SimpleNamespace(),
            collect_data=True, direction=None,
        )
        expense_id = self.database.get_conversation_state(user.id)[1]["expense_id"]
        self.assertIn(texts.MODE_TRIAL, message.reply_text.await_args.args[0])
        self.database.set_test_mode(False)
        for value in ("Esempio", "29/10/2026", "1,00"):
            message = SimpleNamespace(text=value, reply_text=AsyncMock())
            await workflow.handle_text(
                SimpleNamespace(message=message, effective_user=user), SimpleNamespace(),
            )
        self.assertEqual(self.database.get_conversation_state(user.id)[0], "awaiting_account")
        markup = message.reply_text.await_args.kwargs["reply_markup"]
        self.assertNotIn("receipt:confirm", [
            button.callback_data for row in markup.inline_keyboard for button in row
        ])
        for data in (
            "receipt:choose:0", "receipt:direction:uscita", "receipt:choose:0", "receipt:no_notes",
        ):
            query = await self._tap(workflow, user, data)
        self.assertEqual(len(portal.submitted), 1)
        self.assertTrue(portal.submitted[0][1])
        self.assertEqual(self.database.get_expense(expense_id).status, "dry_run")
        self.assertIn(texts.TRIAL_SAVED, query.edit_message_text.await_args.args[0])

    async def test_manual_default_always_asks_for_data_instead_of_saving_example_values(self) -> None:
        portal = FakeBilancioClient()
        workflow = self._manual_portal_workflow(portal)
        message = SimpleNamespace(reply_text=AsyncMock())
        await workflow.start_manual_expense(
            SimpleNamespace(message=message, effective_user=SimpleNamespace(id=12345)),
            SimpleNamespace(),
        )
        state, data = self.database.get_conversation_state(12345)
        self.assertEqual(state, "awaiting_receipt_data")
        payload = self.database.get_expense(data["expense_id"]).payload
        self.assertIsNone(payload["total_amount"])
        self.assertIsNone(payload["merchant"])
        self.assertFalse(payload["dry_run"])
        self.assertEqual(portal.submitted, [])

    async def test_photo_saves_really_without_confirmation_and_can_be_corrected(self) -> None:
        portal = FakeBilancioClient()
        workflow = ReceiptWorkflow(
            settings=replace(make_settings(), dry_run=False), database=self.database,
            analyzer=self.analyzer, pending_store=PendingReceiptStore(self.root / "pending"),
            archive=FileArchive(self.root / "archive"), bilancio_client=portal,
        )
        message, update, context = self.make_photo_update(b"real-default")
        await workflow.receive_receipt(update, context)
        state = self.database.get_conversation_state(12345)
        self.assertEqual(state[0], "awaiting_receipt_data")
        expense_id = state[1]["expense_id"]
        self.assertEqual(portal.submitted, [])
        self.assertIn(texts.MODE_REAL, message.reply_text.await_args.args[0])
        user = update.effective_user
        for _ in range(2):
            await self._tap(workflow, user, "receipt:accept")
        edited = SimpleNamespace(text="9,50", reply_text=AsyncMock())
        await workflow.handle_text(SimpleNamespace(message=edited, effective_user=user), context)
        self.assertEqual(self.database.get_conversation_state(12345)[0], "awaiting_account")
        self.database.set_test_mode(True)
        for data in ("receipt:choose:0", "receipt:choose:0", "receipt:no_notes"):
            await self._tap(workflow, user, data)
        self.assertEqual(len(portal.submitted), 1)
        self.assertFalse(portal.submitted[0][1])
        self.assertEqual(portal.submitted[0][0].amount, Decimal("9.50"))
        stored = self.database.get_expense(expense_id)
        self.assertEqual(stored.status, "submitted")
        self.assertTrue(Path(stored.archive_path).is_file())
        stale = await self._tap(workflow, user, "receipt:choose:0")
        stale.answer.assert_awaited_once_with(texts.STALE_BUTTON)
        self.assertEqual(len(portal.submitted), 1)

    async def _start_manual(self, workflow: ReceiptWorkflow, user) -> str:
        await workflow.start_manual_expense(
            SimpleNamespace(
                message=SimpleNamespace(reply_text=AsyncMock()),
                effective_user=user,
            ),
            SimpleNamespace(), collect_data=False, direction=None,
        )
        state = self.database.get_conversation_state(user.id)
        assert state is not None
        return state[1]["expense_id"]

    async def test_old_calendar_and_keypad_callbacks_are_rejected(self) -> None:
        portal = FakeBilancioClient()
        workflow = self._manual_portal_workflow(portal)
        user = SimpleNamespace(id=12345)
        await workflow.start_manual_expense(
            SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()), effective_user=user),
            SimpleNamespace(), collect_data=True, direction=TransactionDirection.INCOME,
        )
        expense_id = self.database.get_conversation_state(12345)[1]["expense_id"]

        noop = await self._tap(workflow, user, "receipt:noop")
        noop.answer.assert_awaited_once()
        noop.edit_message_text.assert_not_awaited()

        before = self.database.get_conversation_state(user.id)
        for action in ("today", "cal:2026-10", "day:2026-10-29", "key:1", "key:ok"):
            query = await self._tap(workflow, user, "receipt:" + action)
            query.answer.assert_awaited_once_with(texts.STALE_BUTTON)
            query.edit_message_text.assert_not_awaited()
            self.assertEqual(self.database.get_conversation_state(user.id), before)

    async def _tap(self, workflow: ReceiptWorkflow, user, data: str, context=None):
        if data == "receipt:confirm":
            saved = self.database.get_conversation_state(user.id)
            if saved and saved[0] == "awaiting_receipt_data":
                for _ in range(3):
                    await self._tap(workflow, user, "receipt:accept", context)
        query = self.make_query(data)
        await workflow.handle_callback(
            SimpleNamespace(
                callback_query=query,
                effective_message=query.message,
                effective_user=user,
            ),
            context or SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock())),
        )
        return query

    def _manual_portal_workflow(self, portal) -> ReceiptWorkflow:
        portal.list_accounts = AsyncMock(
            return_value=[PortalOption("account-1", "Cassa Co.ca")]
        )
        return ReceiptWorkflow(
            settings=replace(make_settings(), dry_run=False),
            database=self.database,
            analyzer=None,
            pending_store=PendingReceiptStore(self.root / "pending"),
            archive=FileArchive(self.root / "archive"),
            bilancio_client=portal,
        )

    async def test_direction_shows_loading_before_reading_types(self) -> None:
        portal = FakeBilancioClient()
        workflow = self._manual_portal_workflow(portal)
        user = SimpleNamespace(id=12345)
        await self._start_manual(workflow, user)
        await self._tap(workflow, user, "receipt:confirm")
        await self._tap(workflow, user, "receipt:choose:0")

        query = await self._tap(workflow, user, "receipt:direction:entrata")

        texts = [call.args[0] for call in query.edit_message_text.await_args_list]
        self.assertTrue(texts[0].startswith("⏳"))
        self.assertNotIn("reply_markup", query.edit_message_text.await_args_list[0].kwargs)
        self.assertIn("3 di 3", texts[-1])

    async def test_repeated_taps_on_old_buttons_do_not_change_operation(self) -> None:
        portal = FakeBilancioClient()
        portal.list_transaction_types = AsyncMock(
            return_value=[PortalOption("type-1", "Tipo uscita")]
        )
        workflow = self._manual_portal_workflow(portal)
        user = SimpleNamespace(id=12345)
        await self._start_manual(workflow, user)
        for data in (
            "receipt:confirm",
            "receipt:choose:0",
            "receipt:direction:entrata",
            "receipt:choose:0",
            "receipt:no_notes",
        ):
            await self._tap(workflow, user, data)
        before = self.database.get_conversation_state(user.id)

        for data in ("receipt:direction:uscita", "receipt:choose:0", "receipt:choose:0"):
            query = await self._tap(workflow, user, data)
            query.answer.assert_awaited_once_with(
                "Questo pulsante non è più attivo. Se vuoi, mandami di nuovo lo scontrino."
            )
            query.edit_message_text.assert_not_awaited()

        self.assertEqual(self.database.get_conversation_state(user.id), before)
        self.assertEqual(portal.list_transaction_types.await_count, 1)
        self.assertEqual(len(portal.submitted), 1)

    async def test_detail_is_requested_and_typing_it_saves_immediately(self) -> None:
        portal = FakeBilancioClient()
        workflow = self._manual_portal_workflow(portal)
        user = SimpleNamespace(id=12345)
        await self._start_manual(workflow, user)
        for data in (
            "receipt:confirm",
            "receipt:choose:0",
            "receipt:direction:entrata",
            "receipt:choose:0",
        ):
            query = await self._tap(workflow, user, data)
        self.assertEqual(self.database.get_conversation_state(user.id)[0], "awaiting_notes")
        self.assertEqual(portal.submitted, [])
        prompt = query.edit_message_text.await_args
        self.assertIn("🗒️ Vuoi aggiungere una NOTA?", prompt.args[0])
        self.assertIn("Prova Telegram", prompt.args[0])
        buttons = [
            button.callback_data
            for row in prompt.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertEqual(buttons, ["receipt:no_notes", "receipt:cancel"])

        too_long = SimpleNamespace(text="x" * 501, reply_text=AsyncMock())
        await workflow.handle_text(
            SimpleNamespace(message=too_long, effective_user=user), SimpleNamespace()
        )
        self.assertIn("troppo lunga", too_long.reply_text.await_args.args[0])
        self.assertEqual(portal.submitted, [])

        progress = SimpleNamespace(edit_text=AsyncMock())
        detail = SimpleNamespace(
            text="  Pizza uscita sabato  ", reply_text=AsyncMock(return_value=progress)
        )
        state, state_data = self.database.get_conversation_state(user.id)
        state_data.update(summary_chat_id=12345, summary_message_id=77)
        self.database.save_conversation_state(user.id, state, state_data)
        bot = SimpleNamespace(edit_message_text=AsyncMock())
        await workflow.handle_text(
            SimpleNamespace(message=detail, effective_user=user), SimpleNamespace(bot=bot)
        )
        self.assertEqual(len(portal.submitted), 1)
        self.assertEqual(portal.submitted[0][0].notes, "Pizza uscita sabato")
        self.assertIsNone(self.database.get_conversation_state(user.id))
        detail.reply_text.assert_not_awaited()
        self.assertEqual(bot.edit_message_text.await_count, 2)
        for call in bot.edit_message_text.await_args_list:
            self.assertEqual(call.kwargs["message_id"], 77)
            self.assertIsNone(call.kwargs["reply_markup"])
        self.assertIn(texts.INSERTING, bot.edit_message_text.await_args_list[0].kwargs["text"])
        result = bot.edit_message_text.await_args.kwargs["text"]
        self.assertIn("🗒️ Pizza uscita sabato", result)

    async def test_note_text_and_callback_edit_persisted_summary_even_after_reopen(self) -> None:
        for note_as_text in (True, False):
            with self.subTest(note_as_text=note_as_text):
                portal = FakeBilancioClient()
                workflow = self._manual_portal_workflow(portal)
                workflow._settings = make_settings()
                user = SimpleNamespace(id=12345)
                expense_id = await self._start_manual(workflow, user)
                for data in ("receipt:confirm", "receipt:choose:0", "receipt:direction:entrata"):
                    await self._tap(workflow, user, data)
                query = self.make_query("receipt:choose:0")
                query.message.chat_id, query.message.message_id = 12345, 77
                context = SimpleNamespace(bot=SimpleNamespace(
                    edit_message_text=AsyncMock(), send_message=AsyncMock(), delete_message=AsyncMock()
                ))
                await workflow.handle_callback(SimpleNamespace(
                    callback_query=query, effective_message=query.message, effective_user=user
                ), context)
                self.assertEqual(self.database.get_conversation_state(user.id)[1]["summary_message_id"], 77)
                workflow._database = Database(self.root / "bot.sqlite3")
                message = SimpleNamespace(text="Nota esempio", reply_text=AsyncMock())
                if note_as_text:
                    await workflow.handle_text(SimpleNamespace(message=message, effective_user=user), context)
                else:
                    query = self.make_query("receipt:no_notes")
                    query.message.chat_id, query.message.message_id = 12345, 77
                    await workflow.handle_callback(SimpleNamespace(
                        callback_query=query, effective_message=query.message, effective_user=user
                    ), context)
                    query.message.reply_text.assert_not_awaited()
                message.reply_text.assert_not_awaited()
                calls = context.bot.edit_message_text.await_args_list
                self.assertEqual(len(calls), 2)
                for call in calls:
                    self.assertEqual((call.kwargs["chat_id"], call.kwargs["message_id"]), (12345, 77))
                    self.assertIsNone(call.kwargs["reply_markup"])
                    self.assertIn("Prova Telegram", call.kwargs["text"])
                self.assertIn(texts.INSERTING, calls[0].kwargs["text"])
                self.assertIn(texts.TRIAL_SAVED, calls[1].kwargs["text"])
                self.assertEqual(portal.submitted[0][0].notes, "Nota esempio" if note_as_text else None)
                self.assertTrue(portal.submitted[0][1])
                context.bot.delete_message.assert_not_awaited()
                stored = self.database.get_expense(expense_id)
                self.assertEqual(stored.payload["summary_message_id"], 77)
                self.assertIn(texts.TRIAL_SAVED, stored.payload["telegram_summary_text"])

    async def test_summary_edit_errors_preserve_note_and_do_not_resubmit(self) -> None:
        for error, fallback_fails in (
            (BadRequest("Message is not modified"), False),
            (BadRequest("Message cannot be edited"), False),
            (NetworkError("offline"), True),
        ):
            with self.subTest(error=type(error).__name__, fallback_fails=fallback_fails):
                portal = FakeBilancioClient()
                workflow = self._manual_portal_workflow(portal)
                workflow._settings = make_settings()
                user = SimpleNamespace(id=12345)
                expense_id = await self._start_manual(workflow, user)
                for action in ("receipt:confirm", "receipt:choose:0", "receipt:direction:entrata", "receipt:choose:0"):
                    await self._tap(workflow, user, action)
                state, data = self.database.get_conversation_state(user.id)
                data.update(summary_chat_id=12345, summary_message_id=77)
                self.database.save_conversation_state(user.id, state, data)
                bot = SimpleNamespace(edit_message_text=AsyncMock(side_effect=[error, None]))
                message = SimpleNamespace(text="Nota da conservare", reply_text=AsyncMock(
                    return_value=SimpleNamespace(chat_id=12345, message_id=88),
                    side_effect=NetworkError("offline") if fallback_fails else None,
                ))
                await workflow.handle_text(SimpleNamespace(message=message, effective_user=user), SimpleNamespace(bot=bot))
                self.assertEqual(len(portal.submitted), 1)
                self.assertEqual(portal.submitted[0][0].notes, "Nota da conservare")
                stored = self.database.get_expense(expense_id)
                self.assertEqual(stored.payload["portal_notes"], "Nota da conservare")
                self.assertIn(texts.TRIAL_SAVED, stored.payload["telegram_summary_text"])
                if isinstance(error, BadRequest) and "not modified" in str(error):
                    message.reply_text.assert_not_awaited()
                elif not fallback_fails:
                    message.reply_text.assert_awaited_once()
                    self.assertEqual(bot.edit_message_text.await_args.kwargs["message_id"], 88)

    async def test_total_telegram_delivery_failure_keeps_verified_result_and_note(self) -> None:
        portal = FakeBilancioClient()
        workflow = self._manual_portal_workflow(portal)
        workflow._settings = make_settings()
        user = SimpleNamespace(id=12345)
        expense_id = await self._start_manual(workflow, user)
        for action in ("receipt:confirm", "receipt:choose:0", "receipt:direction:entrata", "receipt:choose:0"):
            await self._tap(workflow, user, action)
        state, data = self.database.get_conversation_state(user.id)
        data.update(summary_chat_id=12345, summary_message_id=77)
        self.database.save_conversation_state(user.id, state, data)
        bot = SimpleNamespace(edit_message_text=AsyncMock(side_effect=NetworkError("private")))
        message = SimpleNamespace(text="Nota privata", reply_text=AsyncMock(side_effect=NetworkError("private")))
        with self.assertLogs("bot.receipt_workflow", level="WARNING") as logs:
            await workflow.handle_text(SimpleNamespace(message=message, effective_user=user), SimpleNamespace(bot=bot))
        self.assertNotIn("private", "\n".join(logs.output))
        self.assertNotIn("Nota privata", "\n".join(logs.output))
        self.assertEqual(len(portal.submitted), 1)
        stored = self.database.get_expense(expense_id)
        self.assertEqual(stored.status, "dry_run")
        self.assertEqual(stored.payload["portal_notes"], "Nota privata")
        self.assertIn(texts.TRIAL_SAVED, stored.payload["telegram_summary_text"])
        self.assertEqual(stored.payload["summary_message_id"], 77)
        self.assertIsNone(self.database.get_conversation_state(user.id))

    async def test_note_prompt_fallback_persists_replacement_reference(self) -> None:
        portal = FakeBilancioClient()
        workflow = self._manual_portal_workflow(portal)
        workflow._settings = make_settings()
        user = SimpleNamespace(id=12345)
        expense_id = await self._start_manual(workflow, user)
        for action in ("receipt:confirm", "receipt:choose:0", "receipt:direction:entrata"):
            await self._tap(workflow, user, action)
        query = self.make_query("receipt:choose:0")
        query.message.chat_id, query.message.message_id = 12345, 77
        query.edit_message_text.side_effect = BadRequest("Message cannot be edited")
        query.message.reply_text.return_value = SimpleNamespace(chat_id=12345, message_id=88)
        await workflow.handle_callback(SimpleNamespace(
            callback_query=query, effective_message=query.message, effective_user=user
        ), SimpleNamespace())
        self.assertEqual(self.database.get_conversation_state(user.id)[1]["summary_message_id"], 88)
        self.assertEqual(self.database.get_expense(expense_id).payload["summary_message_id"], 88)
        stale = self.make_query("receipt:no_notes")
        stale.message.message_id = 77
        await workflow.handle_callback(SimpleNamespace(
            callback_query=stale, effective_message=stale.message, effective_user=user
        ), SimpleNamespace())
        stale.answer.assert_awaited_once_with(texts.STALE_BUTTON)
        self.assertEqual(portal.submitted, [])
        await workflow.cancel_current(user.id)

    async def test_analysis_proposals_require_each_field_and_allow_text_correction(self) -> None:
        portal = FakeBilancioClient()
        self.workflow._bilancio = portal
        message, update, context = self.make_photo_update(b"synthetic-proposals")
        await self.workflow.receive_receipt(update, context)
        user = update.effective_user
        state, data = self.database.get_conversation_state(user.id)
        expense_id = data["expense_id"]
        self.assertEqual(data["input_field"], "merchant")
        early = self.make_query("receipt:confirm")
        await self.workflow.handle_callback(SimpleNamespace(
            callback_query=early, effective_message=early.message, effective_user=user
        ), context)
        early.answer.assert_awaited_once_with(texts.STALE_BUTTON)
        self.assertEqual(portal.requested_dates, [])
        for value, expected_field in (("Descrizione corretta", "date"), ("2 3 26", "total_amount")):
            typed = SimpleNamespace(text=value, reply_text=AsyncMock())
            await self.workflow.handle_text(SimpleNamespace(message=typed, effective_user=user), context)
            self.assertEqual(self.database.get_conversation_state(user.id)[1]["input_field"], expected_field)
        await self._tap(self.workflow, user, "receipt:accept", context)
        self.assertEqual(self.database.get_conversation_state(user.id)[0], "awaiting_account")
        payload = self.database.get_expense(expense_id).payload
        self.assertEqual(payload["merchant"], "Descrizione corretta")
        self.assertEqual(payload["date"], "2026-03-02")
        self.assertEqual(Decimal(payload["total_amount"]), Decimal("12.50"))
        self.assertEqual(portal.submitted, [])

    async def test_skipping_the_detail_saves_without_notes(self) -> None:
        portal = FakeBilancioClient()
        workflow = self._manual_portal_workflow(portal)
        user = SimpleNamespace(id=12345)
        await self._start_manual(workflow, user)
        for data in (
            "receipt:confirm",
            "receipt:choose:0",
            "receipt:direction:entrata",
            "receipt:choose:0",
            "receipt:no_notes",
        ):
            query = await self._tap(workflow, user, data)
        self.assertEqual(len(portal.submitted), 1)
        self.assertIsNone(portal.submitted[0][0].notes)
        self.assertNotIn("🗒️", query.edit_message_text.await_args.args[0])
        stale = await self._tap(workflow, user, "receipt:no_notes")
        stale.answer.assert_awaited_once_with(texts.STALE_BUTTON)
        self.assertEqual(len(portal.submitted), 1)

    async def test_type_read_failure_keeps_manual_entry_for_retry(self) -> None:
        portal = FakeBilancioClient()
        portal.list_transaction_types = AsyncMock(
            side_effect=BilancioClientError("Unavailable")
        )
        workflow = self._manual_portal_workflow(portal)
        user = SimpleNamespace(id=12345)
        expense_id = await self._start_manual(workflow, user)
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        await self._tap(workflow, user, "receipt:confirm")
        await self._tap(workflow, user, "receipt:choose:0")

        query = await self._tap(workflow, user, "receipt:direction:entrata", context)

        self.assertEqual(self.database.get_conversation_state(user.id)[0], "awaiting_confirmation")
        self.assertIsNotNone(self.database.get_expense(expense_id))
        self.assertIn("ho conservato i dati", query.edit_message_text.await_args.args[0])
        self.assertEqual(portal.submitted, [])
        portal.list_transaction_types.side_effect = None
        portal.list_transaction_types.return_value = [PortalOption("type", "Tipo")]
        for data in ("receipt:confirm", "receipt:choose:0", "receipt:direction:entrata"):
            await self._tap(workflow, user, data, context)
        self.assertEqual(
            self.database.get_conversation_state(user.id)[0], "awaiting_transaction_type"
        )

    async def test_receipt_type_read_failure_keeps_receipt_for_retry(self) -> None:
        portal = FakeBilancioClient()
        portal.list_accounts = AsyncMock(
            return_value=[PortalOption("account-1", "Cassa Co.ca")]
        )
        portal.list_transaction_types = AsyncMock(
            side_effect=BilancioClientError("Unavailable")
        )
        workflow = ReceiptWorkflow(
            settings=make_settings(),
            database=self.database,
            analyzer=self.analyzer,
            pending_store=PendingReceiptStore(self.root / "pending"),
            archive=FileArchive(self.root / "archive"),
            bilancio_client=portal,
        )
        _, update, context = self.make_photo_update(b"retry receipt")
        await workflow.receive_receipt(update, context)
        user = update.effective_user
        context.bot.send_message = AsyncMock()
        await self._tap(workflow, user, "receipt:confirm", context)
        query = await self._tap(workflow, user, "receipt:choose:0", context)

        state = self.database.get_conversation_state(user.id)
        self.assertEqual(state[0], "awaiting_confirmation")
        self.assertIn("«Sì, è giusto» per riprovare", query.edit_message_text.await_args.args[0])
        self.assertTrue(any((self.root / "pending").iterdir()))

    async def test_new_manual_test_replaces_stale_manual_test(self) -> None:
        portal = FakeBilancioClient()
        workflow = self._manual_portal_workflow(portal)
        user = SimpleNamespace(id=12345)
        old_expense_id = await self._start_manual(workflow, user)

        message = SimpleNamespace(reply_text=AsyncMock())
        await workflow.start_manual_expense(
            SimpleNamespace(message=message, effective_user=user),
            SimpleNamespace(), collect_data=False,
        )

        replies = [call.args[0] for call in message.reply_text.await_args_list]
        self.assertIn(texts.MANUAL_PREVIOUS_CANCELLED, replies)
        self.assertIsNone(self.database.get_expense(old_expense_id))
        state = self.database.get_conversation_state(user.id)
        self.assertEqual(state[0], "awaiting_confirmation")
        self.assertNotEqual(state[1]["expense_id"], old_expense_id)

    async def test_types_show_most_used_first_and_others_on_request(self) -> None:
        portal = FakeBilancioClient()
        options = [PortalOption(f"type-{index}", f"Tipo {index}") for index in range(10)]
        portal.list_transaction_types = AsyncMock(return_value=options)
        workflow = self._manual_portal_workflow(portal)
        self.database.transaction_type_usage = Mock(return_value={"Tipo 8": 5, "Tipo 3": 2})
        user = SimpleNamespace(id=12345)
        await self._start_manual(workflow, user)
        await self._tap(workflow, user, "receipt:confirm")
        await self._tap(workflow, user, "receipt:choose:0")

        query = await self._tap(workflow, user, "receipt:direction:entrata")

        markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        labels = [row[0].text for row in markup.inline_keyboard]
        self.assertEqual(
            labels,
            ["Tipo 8", "Tipo 3", "Tipo 0", "Tipo 1", "Tipo 2", "Tipo 4", "➕ Mostra altre (4)"],
        )
        self.database.transaction_type_usage.assert_called_once_with("Cassa Co.ca", "entrata")

        more = await self._tap(workflow, user, "receipt:more_types")
        more_markup = more.edit_message_text.await_args.kwargs["reply_markup"]
        self.assertEqual(
            [row[0].callback_data for row in more_markup.inline_keyboard],
            ["receipt:choose:5", "receipt:choose:6", "receipt:choose:7", "receipt:choose:9",
             "receipt:cancel"],
        )

        await self._tap(workflow, user, "receipt:choose:9")
        await self._tap(workflow, user, "receipt:no_notes")
        self.assertIsNone(self.database.get_conversation_state(user.id))
        self.assertEqual(portal.submitted[0][0].transaction_type.label, "Tipo 9")

    async def test_failed_ai_retains_file_and_accepts_all_fields_manually(self) -> None:
        for error_type in (LocalVisionError, ReceiptParseError, ImagePreprocessingError, TimeoutError):
            with self.subTest(error=error_type.__name__):
                analyzer = SimpleNamespace(analyze=AsyncMock(side_effect=error_type("private")))
                portal = FakeBilancioClient()
                workflow = ReceiptWorkflow(
                    settings=make_settings(), database=self.database, analyzer=analyzer,
                    pending_store=PendingReceiptStore(self.root / "pending"),
                    archive=FileArchive(self.root / "archive"), bilancio_client=portal,
                )
                message, update, context = self.make_photo_update(error_type.__name__.encode())
                context.bot.send_message = AsyncMock()
                await workflow.receive_receipt(update, context)
                self.assertEqual(message.reply_text.await_count, 3)
                self.assertIn("Inseriamo i dati a mano", message.reply_text.await_args_list[-2].args[0])
                context.bot.send_message.assert_not_awaited()
                state = self.database.get_conversation_state(12345)
                self.assertEqual(state[0], "awaiting_receipt_data")
                expense_id = state[1]["expense_id"]
                stored = self.database.get_expense(expense_id)
                self.assertTrue(Path(stored.payload["pending_path"]).is_file())
                self.assertIsNone(stored.archive_path)
                user = SimpleNamespace(id=12345)
                for value in ("Esempio manuale", "29/10/2026", "12,50"):
                    await workflow.handle_text(
                        SimpleNamespace(
                            message=SimpleNamespace(text=value, reply_text=AsyncMock()),
                            effective_user=user,
                        ), context,
                    )
                self.assertEqual(self.database.get_conversation_state(12345)[0], "awaiting_account")
                self.assertEqual(portal.submitted, [])
                for data in (
                    "receipt:confirm", "receipt:choose:0",
                    "receipt:choose:0",
                    "receipt:no_notes",
                ):
                    await self._tap(workflow, user, data, context)
                stored = self.database.get_expense(expense_id)
                self.assertEqual(stored.status, "dry_run")
                self.assertTrue(Path(stored.archive_path).is_file())
                self.assertEqual(portal.submitted[0][0].amount, Decimal("12.50"))
                self.assertTrue(portal.submitted[0][1])

    async def test_missing_analyzer_keeps_receipt_for_manual_input_and_cancel_cleans_it(self) -> None:
        self.workflow._analyzer = None
        message, update, context = self.make_photo_update(b"receipt-no-ai")
        await self.workflow.receive_receipt(update, context)
        state = self.database.get_conversation_state(12345)
        stored = self.database.get_expense(state[1]["expense_id"])
        pending = Path(stored.payload["pending_path"])
        self.assertTrue(pending.is_file())
        await self.workflow.cancel_current(12345)
        self.assertFalse(pending.exists())
        self.assertIsNone(self.database.get_conversation_state(12345))

    async def test_ai_diagnostic_only_reaches_admin_without_duplicate_or_private_data(self) -> None:
        for sender_is_admin in (True, False):
            with self.subTest(sender_is_admin=sender_is_admin):
                admin_id = 12345 if sender_is_admin else 67890
                analyzer = SimpleNamespace(analyze=AsyncMock(side_effect=LocalVisionError(
                    "private raw response", code=VisionFailureCode.MEMORY, status_code=500
                )))
                workflow = ReceiptWorkflow(
                    settings=replace(make_settings(), admin_user_id=admin_id),
                    database=self.database, analyzer=analyzer,
                    pending_store=PendingReceiptStore(self.root / "pending"),
                    archive=FileArchive(self.root / "archive"), bilancio_client=None,
                )
                message, update, context = self.make_photo_update(str(admin_id).encode())
                context.bot.send_message = AsyncMock()
                with self.assertLogs("bot.receipt_workflow", level="WARNING") as logs:
                    await workflow.receive_receipt(update, context)
                text = message.reply_text.await_args_list[-2].args[0]
                self.assertEqual(message.reply_text.await_count, 3)
                if sender_is_admin:
                    context.bot.send_message.assert_not_awaited()
                    self.assertIn("Codice: memory", text)
                    self.assertIn("HTTP 500", text)
                    self.assertIn("insufficienti", text)
                else:
                    self.assertNotIn("Diagnosi AI", text)
                    self.assertNotIn("memory", text)
                    context.bot.send_message.assert_awaited_once()
                    notification = context.bot.send_message.await_args.kwargs
                    self.assertEqual(notification["chat_id"], admin_id)
                    self.assertIn("Codice: memory", notification["text"])
                    self.assertNotIn("private", notification["text"])
                self.assertNotIn("private", text)
                self.assertNotIn("private", "\n".join(logs.output))
                await workflow.cancel_current(12345)

    async def test_manual_income_collects_fields_for_any_authorized_user(self) -> None:
        portal = FakeBilancioClient()
        workflow = self._manual_portal_workflow(portal)
        message = SimpleNamespace(reply_text=AsyncMock())
        await workflow.start_manual_expense(
            SimpleNamespace(message=message, effective_user=SimpleNamespace(id=999)),
            SimpleNamespace(), collect_data=True, direction=None,
        )
        self.assertEqual(message.reply_text.await_args.args[0], texts.ADMIN_ONLY)
        self.assertIsNone(self.database.get_conversation_state(999))
        await workflow.start_manual_expense(
            SimpleNamespace(message=message, effective_user=SimpleNamespace(id=999)),
            SimpleNamespace(), collect_data=True,
        )
        self.assertEqual(self.database.get_conversation_state(999)[0], "awaiting_receipt_data")
        other = SimpleNamespace(id=999)
        for value in ("Quota socio", "29/10/2026", "5"):
            await workflow.handle_text(
                SimpleNamespace(message=SimpleNamespace(text=value, reply_text=AsyncMock()),
                                effective_user=other),
                SimpleNamespace(),
            )
        for data in ("receipt:choose:0", "receipt:choose:0", "receipt:no_notes"):
            await self._tap(workflow, other, data)
        self.assertEqual(len(portal.submitted), 1)
        self.assertEqual(portal.submitted[0][0].direction, TransactionDirection.INCOME)
        portal.submitted.clear()
        await workflow.start_manual_expense(
            SimpleNamespace(message=message, effective_user=SimpleNamespace(id=12345)),
            SimpleNamespace(), collect_data=True,
        )
        state = self.database.get_conversation_state(12345)
        self.assertEqual(state[0], "awaiting_receipt_data")
        self.assertIsNone(self.database.get_expense(state[1]["expense_id"]).payload["total_amount"])
        self.assertIn(texts.MODE_REAL, message.reply_text.await_args.args[0])
        for value in ("Quota", "29/10/2026", "10"):
            await workflow.handle_text(
                SimpleNamespace(message=SimpleNamespace(text=value, reply_text=AsyncMock()),
                                effective_user=SimpleNamespace(id=12345)),
                SimpleNamespace(),
            )
        self.assertEqual(self.database.get_conversation_state(12345)[0], "awaiting_account")
        user = SimpleNamespace(id=12345)
        query = await self._tap(workflow, user, "receipt:choose:0")
        state = self.database.get_conversation_state(user.id)
        self.assertEqual(state[0], "awaiting_transaction_type")
        self.assertEqual(state[1]["direction"], "entrata")
        self.assertNotIn(
            texts.DIRECTION_PROMPT,
            [call.args[0] for call in query.edit_message_text.await_args_list],
        )
        await self._tap(workflow, user, "receipt:choose:0")
        await self._tap(workflow, user, "receipt:no_notes")
        self.assertEqual(len(portal.submitted), 1)
        self.assertEqual(portal.submitted[0][0].direction, TransactionDirection.INCOME)
        self.assertFalse(portal.submitted[0][1])

    async def test_non_manual_unattached_expense_is_blocked_at_direction_and_submission(self) -> None:
        portal = FakeBilancioClient()
        workflow = self._manual_portal_workflow(portal)
        user = SimpleNamespace(id=12345)
        expense_id = await self._start_manual(workflow, user)
        payload = self.database.get_expense(expense_id).payload
        payload.pop("manual_entry")
        self.database.update_expense_payload(expense_id, payload)
        await self._tap(workflow, user, "receipt:confirm")
        query = await self._tap(workflow, user, "receipt:choose:0")
        self.assertIn(texts.EXPENSE_RECEIPT_REQUIRED, query.edit_message_text.await_args.args[0])
        state = self.database.get_conversation_state(12345)
        state[1]["direction"] = "uscita"
        state[1]["transaction_type"] = {"value": "type", "label": "Tipo"}
        query = self.make_query("receipt:choose:0")
        await workflow._submit_expense(
            SimpleNamespace(message=query.message, callback_query=query),
            SimpleNamespace(), user.id, expense_id, payload, state[1], "-",
        )
        self.assertIn(texts.EXPENSE_RECEIPT_REQUIRED, query.edit_message_text.await_args.args[0])
        self.assertEqual(portal.submitted, [])
        self.assertEqual(self.database.get_expense(expense_id).status, "failed")

    def test_account_units_use_the_configured_defaults(self) -> None:
        expected_units = {
            "Cassa Co.ca": "Gruppo G1",
            "Conto Co.ca": "Gruppo G1",
            "Cassa LC": "L1 - ROCCIA DELLA PACE",
            "Cassa EG": "O1 - CASSARINO - CIARCIA",
            "Cassa RS": "T1 - FALCHI PELLEGRINI",
        }
        for account_name, unit_name in expected_units.items():
            with self.subTest(account=account_name):
                self.assertEqual(
                    default_unit_for_account(PortalOption(account_name, account_name)),
                    PortalOption(unit_name, unit_name),
                )
        self.assertIsNone(
            default_unit_for_account(PortalOption("unknown", "Cassa di prova"))
        )

    def test_parses_user_entered_european_amount_and_date(self) -> None:
        updated = ReceiptWorkflow._apply_user_value(
            {"total_amount": None},
            "total_amount",
            "1.234,56",
        )
        updated = ReceiptWorkflow._apply_user_value(
            updated,
            "date",
            "26/10/2026",
        )

        self.assertEqual(updated["total_amount"], "1234.56")
        self.assertEqual(updated["date"], "2026-10-26")

    @staticmethod
    def make_photo_update(file_bytes: bytes):
        message = SimpleNamespace(
            photo=[SimpleNamespace(file_id="telegram-file-id", file_size=len(file_bytes))],
            document=None,
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=12345),
        )
        telegram_file = SimpleNamespace(
            download_as_bytearray=AsyncMock(return_value=bytearray(file_bytes))
        )
        context = SimpleNamespace(
            bot=SimpleNamespace(get_file=AsyncMock(return_value=telegram_file))
        )
        return message, update, context

    @staticmethod
    def make_query(data: str):
        message = SimpleNamespace(reply_text=AsyncMock())
        return SimpleNamespace(
            data=data,
            message=message,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )


if __name__ == "__main__":
    unittest.main()
