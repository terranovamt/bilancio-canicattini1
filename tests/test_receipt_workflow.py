import tempfile
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bilancio.client import (
    BilancioClientError,
    PortalOption,
    PortalSubmissionResult,
    TransactionDirection,
    default_unit_for_account,
)
from bot.receipt_workflow import ReceiptWorkflow
from config import Settings
from db.database import Database
from storage.archive import FileArchive
from storage.pending import PendingReceiptStore
from vision.receipt_schema import ReceiptData


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

    async def test_analyzes_photo_and_persists_confirmation_state(self) -> None:
        message, update, context = self.make_photo_update(b"private receipt")

        await self.workflow.receive_receipt(update, context)

        state = self.database.get_conversation_state(12345)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state[0], "awaiting_confirmation")
        expense_id = state[1]["expense_id"]
        expense = self.database.get_expense(str(expense_id))
        self.assertIsNotNone(expense)
        assert expense is not None
        self.assertEqual(expense.payload["merchant"], "Negozio")
        self.assertTrue(Path(str(expense.payload["pending_path"])).is_file())
        self.assertIn("Controlla i dati letti", message.reply_text.await_args.args[0])
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
        self.assertIn("GG/MM/AAAA", message.reply_text.await_args.args[0])

    async def test_rejects_repeated_file_hash(self) -> None:
        message, update, context = self.make_photo_update(b"same receipt")
        await self.workflow.receive_receipt(update, context)
        self.database.delete_conversation_state(12345)
        second_message, second_update, second_context = self.make_photo_update(
            b"same receipt"
        )

        await self.workflow.receive_receipt(second_update, second_context)

        self.assertIn(
            "già stato ricevuto",
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
        _, receipt_update, receipt_context = self.make_photo_update(b"receipt")
        await workflow.receive_receipt(receipt_update, receipt_context)
        confirm_query = self.make_query("receipt:confirm")
        await workflow.handle_callback(
            SimpleNamespace(
                callback_query=confirm_query,
                effective_user=SimpleNamespace(id=12345),
            ),
            SimpleNamespace(),
        )
        account_markup = confirm_query.edit_message_text.await_args.kwargs["reply_markup"]
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
        direction_query = self.make_query("receipt:direction:uscita")
        await workflow.handle_callback(
            SimpleNamespace(
                callback_query=direction_query,
                effective_user=SimpleNamespace(id=12345),
            ),
            SimpleNamespace(),
        )

        type_markup = direction_query.edit_message_text.await_args.kwargs["reply_markup"]
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
            settings=make_settings(),
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
            SimpleNamespace(),
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
            "receipt:direction:uscita",
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

        final_state = self.database.get_conversation_state(user.id)
        self.assertEqual(final_state[0], "awaiting_manual_final_confirmation")
        self.assertEqual(
            final_state[1]["unit"],
            {"value": "Gruppo G1", "label": "Gruppo G1"},
        )
        self.assertIsNone(final_state[1]["category"])

        final_message = SimpleNamespace(reply_text=AsyncMock())
        save_query = self.make_query("receipt:manual_save")
        await workflow.handle_callback(
            SimpleNamespace(
                message=final_message,
                callback_query=save_query,
                effective_user=user,
            ),
            SimpleNamespace(),
        )

        stored = self.database.get_expense(expense_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, "dry_run")
        self.assertIsNone(stored.archive_path)
        self.assertIsNone(self.database.get_conversation_state(user.id))
        self.assertEqual(len(portal.submitted), 1)
        self.assertTrue(portal.submitted[0][1])
        self.assertTrue(portal.submitted[0][0].accounting_voice == "Voci Scout")

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
            SimpleNamespace(),
        )
        for callback_data in (
            "receipt:confirm",
            "receipt:choose:0",
            "receipt:direction:uscita",
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

        final_state = self.database.get_conversation_state(user.id)
        self.assertEqual(final_state[0], "awaiting_manual_final_confirmation")
        self.assertEqual(
            final_state[1]["unit"],
            {"value": "Gruppo G1", "label": "Gruppo G1"},
        )
        self.assertIsNone(final_state[1]["category"])
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
        for callback_data in (
            "receipt:confirm",
            "receipt:choose:0",
            "receipt:direction:uscita",
            "receipt:choose:0",
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
                    SimpleNamespace(),
                )
                expense_id = self.database.get_conversation_state(user.id)[1]["expense_id"]
                await self._tap(workflow, user, "receipt:edit:total_amount")
                await workflow.handle_text(
                    SimpleNamespace(
                        message=SimpleNamespace(text="1,00", reply_text=AsyncMock()),
                        effective_user=user,
                    ),
                    SimpleNamespace(),
                )
                self.assertEqual(
                    self.database.get_conversation_state(user.id)[0],
                    "awaiting_confirmation",
                )
                for callback in (
                    "receipt:confirm", "receipt:choose:0",
                    f"receipt:direction:{direction.value}",
                    "receipt:choose:0",
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
                final_state = self.database.get_conversation_state(user.id)
                self.assertEqual(final_state[0], "awaiting_manual_final_confirmation")
                self.assertEqual(portal.submitted, [])
                message = SimpleNamespace(reply_text=AsyncMock())
                await workflow.handle_callback(
                    SimpleNamespace(
                        callback_query=self.make_query("receipt:manual_save"),
                        effective_message=message, effective_user=user,
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

    async def _start_manual(self, workflow: ReceiptWorkflow, user) -> str:
        await workflow.start_manual_expense(
            SimpleNamespace(
                message=SimpleNamespace(reply_text=AsyncMock()),
                effective_user=user,
            ),
            SimpleNamespace(),
        )
        state = self.database.get_conversation_state(user.id)
        assert state is not None
        return state[1]["expense_id"]

    async def _tap(self, workflow: ReceiptWorkflow, user, data: str, context=None):
        query = self.make_query(data)
        await workflow.handle_callback(
            SimpleNamespace(
                callback_query=query,
                effective_message=query.message,
                effective_user=user,
            ),
            context or SimpleNamespace(),
        )
        return query

    def _manual_portal_workflow(self, portal) -> ReceiptWorkflow:
        portal.list_accounts = AsyncMock(
            return_value=[PortalOption("account-1", "Cassa Co.ca")]
        )
        return ReceiptWorkflow(
            settings=make_settings(),
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

        query = await self._tap(workflow, user, "receipt:direction:uscita")

        texts = [call.args[0] for call in query.edit_message_text.await_args_list]
        self.assertTrue(texts[0].startswith("⏳"))
        self.assertNotIn("reply_markup", query.edit_message_text.await_args_list[0].kwargs)
        self.assertIn("3/3", texts[-1])

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
            "receipt:direction:uscita",
            "receipt:choose:0",
        ):
            await self._tap(workflow, user, data)
        before = self.database.get_conversation_state(user.id)

        for data in ("receipt:direction:uscita", "receipt:choose:0", "receipt:choose:0"):
            query = await self._tap(workflow, user, data)
            query.answer.assert_awaited_once_with("Questa scelta non è più attiva.")
            query.edit_message_text.assert_not_awaited()

        self.assertEqual(self.database.get_conversation_state(user.id), before)
        self.assertEqual(portal.list_transaction_types.await_count, 1)
        self.assertEqual(portal.submitted, [])

    async def test_type_read_failure_cancels_manual_entry_automatically(self) -> None:
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

        self.assertIsNone(self.database.get_conversation_state(user.id))
        self.assertIsNone(self.database.get_expense(expense_id))
        self.assertIn("annullato la prova", query.edit_message_text.await_args.args[0])
        self.assertEqual(portal.submitted, [])
        message = SimpleNamespace(reply_text=AsyncMock())
        await workflow.start_manual_expense(
            SimpleNamespace(message=message, effective_user=user),
            SimpleNamespace(),
        )
        self.assertIn("Spesa manuale", message.reply_text.await_args.args[0])

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
        await self._tap(workflow, user, "receipt:choose:0", context)

        query = await self._tap(workflow, user, "receipt:direction:uscita", context)

        state = self.database.get_conversation_state(user.id)
        self.assertEqual(state[0], "awaiting_confirmation")
        self.assertIn("Conferma per riprovare", query.edit_message_text.await_args.args[0])
        self.assertTrue(any((self.root / "pending").iterdir()))

    async def test_new_manual_test_replaces_stale_manual_test(self) -> None:
        portal = FakeBilancioClient()
        workflow = self._manual_portal_workflow(portal)
        user = SimpleNamespace(id=12345)
        old_expense_id = await self._start_manual(workflow, user)

        message = SimpleNamespace(reply_text=AsyncMock())
        await workflow.start_manual_expense(
            SimpleNamespace(message=message, effective_user=user),
            SimpleNamespace(),
        )

        replies = [call.args[0] for call in message.reply_text.await_args_list]
        self.assertIn("Ho annullato la prova precedente rimasta aperta.", replies)
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

        query = await self._tap(workflow, user, "receipt:direction:uscita")

        markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        labels = [row[0].text for row in markup.inline_keyboard]
        self.assertEqual(
            labels,
            ["Tipo 8", "Tipo 3", "Tipo 0", "Tipo 1", "Tipo 2", "Tipo 4", "➕ Altre tipologie (4)"],
        )
        self.database.transaction_type_usage.assert_called_once_with("Cassa Co.ca", "uscita")

        more = await self._tap(workflow, user, "receipt:more_types")
        more_markup = more.edit_message_text.await_args.kwargs["reply_markup"]
        self.assertEqual(
            [row[0].callback_data for row in more_markup.inline_keyboard],
            ["receipt:choose:5", "receipt:choose:6", "receipt:choose:7", "receipt:choose:9", "receipt:cancel"],
        )

        await self._tap(workflow, user, "receipt:choose:9")
        state = self.database.get_conversation_state(user.id)
        self.assertEqual(state[0], "awaiting_manual_final_confirmation")
        self.assertEqual(state[1]["transaction_type"]["label"], "Tipo 9")

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
