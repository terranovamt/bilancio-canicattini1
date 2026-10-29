import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from bilancio.client import (
    BilancioClientError,
    PortalSubmissionResult,
    TransactionDirection,
)
from bot.retry_service import ExpenseRetryService
from config import Settings
from db.database import Database
from storage.archive import FileArchive
from storage.pending import PendingReceiptStore


class FakeRetryPortal:
    def __init__(self, *, failure: bool = False) -> None:
        self.failure = failure
        self.received_expenses = []

    async def create_expense(self, expense, *, dry_run: bool):
        self.received_expenses.append((expense, dry_run))
        if self.failure:
            raise BilancioClientError("portal is unavailable")
        return PortalSubmissionResult(verified=True, dry_run=dry_run)


class FakeBot:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, *, chat_id: int, text: str):
        self.messages.append((chat_id, text))


def make_settings() -> Settings:
    return Settings.from_env(
        {
            "BOT_TOKEN": "test-token-placeholder",
            "ALLOWED_USER_IDS": "12345",
            "ADMIN_USER_ID": "12345",
        }
    )


class ExpenseRetryServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = Database(self.root / "bot.sqlite3")
        self.database.initialize()
        self.source = self.root / "pending.jpg"
        self.source.write_bytes(b"receipt")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_processes_due_expense_and_notifies_user(self) -> None:
        self.create_queued_expense()
        portal = FakeRetryPortal()
        bot = FakeBot()
        service = ExpenseRetryService(
            settings=make_settings(),
            database=self.database,
            bilancio_client=portal,
            archive=FileArchive(self.root / "archive"),
            pending_store=PendingReceiptStore(self.root / "pending"),
        )

        result = await service.process_due(bot)

        stored = self.database.get_expense("expense-1")
        self.assertEqual(result.succeeded, 1)
        self.assertEqual(result.trials, 1)
        self.assertEqual(stored.status, "dry_run")
        self.assertIsNotNone(stored.archive_path)
        self.assertTrue(Path(stored.archive_path).is_file())
        self.assertEqual(portal.received_expenses[0][0].direction, TransactionDirection.EXPENSE)
        self.assertTrue(portal.received_expenses[0][1])
        self.assertEqual(bot.messages[0][0], 12345)
        self.assertIn("🧪 Nuovo tentativo in prova", bot.messages[0][1])

    async def test_processes_queued_expense_without_a_unit(self) -> None:
        self.create_queued_expense(with_unit=False)
        portal = FakeRetryPortal()
        service = ExpenseRetryService(
            settings=make_settings(),
            database=self.database,
            bilancio_client=portal,
            archive=FileArchive(self.root / "archive"),
            pending_store=PendingReceiptStore(self.root / "pending"),
        )

        result = await service.process_due(FakeBot())

        self.assertEqual(result.succeeded, 1)
        self.assertIsNone(portal.received_expenses[0][0].unit)

    async def test_failed_attempt_is_rescheduled_with_backoff(self) -> None:
        self.create_queued_expense()
        portal = FakeRetryPortal(failure=True)
        service = ExpenseRetryService(
            settings=make_settings(),
            database=self.database,
            bilancio_client=portal,
            archive=FileArchive(self.root / "archive"),
            pending_store=PendingReceiptStore(self.root / "pending"),
        )

        result = await service.process_due(FakeBot())

        retry = self.database.get_due_retries(
            "2999-01-01T00:00:00+00:00"
        )[0]
        self.assertEqual(result.pending, 1)
        self.assertEqual(retry.attempts, 2)
        self.assertEqual(retry.last_error_code, "portal_retry_failed")

    async def test_exhausted_attempts_are_marked_failed_and_notified(self) -> None:
        self.create_queued_expense()
        for _ in range(2):
            self.database.enqueue_retry(
                "expense-1",
                next_attempt_at="2000-01-01T00:00:00+00:00",
                error_code="portal_retry_failed",
            )
        portal = FakeRetryPortal(failure=True)
        bot = FakeBot()
        service = ExpenseRetryService(
            settings=make_settings(),
            database=self.database,
            bilancio_client=portal,
            archive=FileArchive(self.root / "archive"),
            pending_store=PendingReceiptStore(self.root / "pending"),
        )

        result = await service.process_due(bot)

        stored = self.database.get_expense("expense-1")
        self.assertEqual(result.failed, 1)
        self.assertEqual(stored.status, "failed")
        self.assertEqual(self.database.get_due_retries("2999-01-01T00:00:00+00:00"), [])
        self.assertEqual(len(bot.messages), 2)
        self.assertIn("«Negozio, 12,50 €»", bot.messages[0][1])
        self.assertTrue(bot.messages[0][1].startswith("⚠️"))

    async def test_real_retry_success_identifies_the_expense(self) -> None:
        self.create_queued_expense()
        bot = FakeBot()
        service = ExpenseRetryService(
            settings=replace(make_settings(), dry_run=False),
            database=self.database,
            bilancio_client=FakeRetryPortal(),
            archive=FileArchive(self.root / "archive"),
            pending_store=PendingReceiptStore(self.root / "pending"),
        )
        result = await service.process_due(bot)
        self.assertEqual((result.succeeded, result.trials), (1, 0))
        self.assertEqual(bot.messages[0][1], "✅ Salvata: Negozio, 12,50 € (26/10).")

    def create_queued_expense(self, *, with_unit: bool = True) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.database.create_expense(
            expense_id="expense-1",
            telegram_user_id=12345,
            sha256="a" * 64,
            payload={
                "date": "2026-10-26",
                "merchant": "Negozio",
                "total_amount": "12.50",
                "direction": "uscita",
                "account": {"value": "a1", "label": "Cassa"},
                "transaction_type": {"value": "t1", "label": "Tipo"},
                "unit": {"value": "u1", "label": "Unità"} if with_unit else None,
                "category": None,
                "portal_notes": None,
                "pending_path": str(self.source),
            },
        )
        self.database.enqueue_retry(
            "expense-1",
            next_attempt_at=now,
            error_code="initial_failure",
        )


if __name__ == "__main__":
    unittest.main()
