import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bilancio.client import PortalOption, PortalSubmissionResult, TransactionDirection
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

        voice_markup = direction_query.edit_message_text.await_args.kwargs["reply_markup"]
        self.assertEqual(voice_markup.inline_keyboard[0][0].text, "Voci Scout")
        self.assertEqual(
            self.database.get_conversation_state(12345)[0],
            "awaiting_accounting_voice",
        )

        voice_query = self.make_query("receipt:voice:Voci Scout")
        await workflow.handle_callback(
            SimpleNamespace(
                callback_query=voice_query,
                effective_user=SimpleNamespace(id=12345),
            ),
            SimpleNamespace(),
        )

        type_markup = voice_query.edit_message_text.await_args.kwargs["reply_markup"]
        self.assertEqual(type_markup.inline_keyboard[0][0].text, "Tipo uscita")
        self.assertEqual(portal.requested_dates[0], date(2026, 10, 26))
        self.assertEqual(portal.requested_dates[1], date(2026, 10, 26))

    async def test_manual_expense_skips_receipt_ai_and_archive(self) -> None:
        portal = FakeBilancioClient()
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

        entry_message = SimpleNamespace(
            text="26/10/2026 | Prova Telegram | 1,00",
            reply_text=AsyncMock(),
        )
        await workflow.handle_text(
            SimpleNamespace(message=entry_message, effective_user=user),
            SimpleNamespace(),
        )
        for callback_data in (
            "receipt:confirm",
            "receipt:choose:0",
            "receipt:direction:uscita",
            "receipt:voice:Voci Scout",
            "receipt:choose:0",
            "receipt:choose:0",
            "receipt:choose:0",
        ):
            await workflow.handle_callback(
                SimpleNamespace(
                    callback_query=self.make_query(callback_data),
                    effective_user=user,
                ),
                SimpleNamespace(),
            )

        notes_message = SimpleNamespace(text="-", reply_text=AsyncMock())
        await workflow.handle_text(
            SimpleNamespace(message=notes_message, effective_user=user),
            SimpleNamespace(),
        )
        final_state = self.database.get_conversation_state(user.id)
        self.assertEqual(final_state[0], "awaiting_manual_final_confirmation")

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

    async def test_confirmed_receipt_is_archived_and_submitted_in_dry_run(self) -> None:
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
        for callback_data in (
            "receipt:confirm",
            "receipt:choose:0",
            "receipt:direction:uscita",
            "receipt:voice:Voci Scout",
            "receipt:choose:0",
            "receipt:choose:0",
            "receipt:choose:0",
        ):
            await workflow.handle_callback(
                SimpleNamespace(
                    callback_query=self.make_query(callback_data),
                    effective_user=SimpleNamespace(id=12345),
                ),
                SimpleNamespace(),
            )

        message = SimpleNamespace(text="-", reply_text=AsyncMock())
        await workflow.handle_text(
            SimpleNamespace(
                message=message,
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
        self.assertIn("non ha salvato", message.reply_text.await_args.args[0])

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
        return SimpleNamespace(
            data=data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )


if __name__ == "__main__":
    unittest.main()
