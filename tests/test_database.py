import tempfile
import unittest
from pathlib import Path

from db.database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "bot.sqlite3")
        self.database.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_expense(
        self,
        expense_id: str = "expense-1",
        sha256: str = "a" * 64,
        user_id: int = 12345,
    ) -> bool:
        return self.database.create_expense(
            expense_id=expense_id,
            telegram_user_id=user_id,
            sha256=sha256,
            payload={"merchant": "Test Shop", "amount": "12.50"},
            archive_path="/scontrini/2026/10/test.jpg",
        )

    def test_create_expense_persists_record_and_hash(self) -> None:
        self.assertTrue(self.create_expense())

        stored = self.database.get_expense("expense-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.payload["merchant"], "Test Shop")
        self.assertEqual(stored.status, "received")
        self.assertEqual(stored.archive_path, "/scontrini/2026/10/test.jpg")
        self.assertTrue(self.database.contains_file_hash("a" * 64))

    def test_duplicate_hash_is_rejected(self) -> None:
        self.assertTrue(self.create_expense())

        self.assertFalse(
            self.create_expense(expense_id="expense-2", sha256="a" * 64)
        )
        self.assertIsNone(self.database.get_expense("expense-2"))

    def test_invalid_file_hash_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "sha256"):
            self.create_expense(sha256="not-a-hash")

    def test_retry_queue_increments_attempts_and_returns_due_items(self) -> None:
        self.create_expense()
        first = self.database.enqueue_retry(
            "expense-1",
            next_attempt_at="2026-10-26T10:00:00+00:00",
            error_code="portal_timeout",
        )
        second = self.database.enqueue_retry(
            "expense-1",
            next_attempt_at="2026-10-26T12:00:00+00:00",
            error_code="portal_timeout",
        )

        self.assertEqual(first.attempts, 1)
        self.assertEqual(second.attempts, 2)
        self.assertEqual(
            [item.expense_id for item in self.database.get_due_retries(
                "2026-10-26T11:00:00+00:00"
            )],
            [],
        )
        due = self.database.get_due_retries("2026-10-26T13:00:00+00:00")
        self.assertEqual([item.expense_id for item in due], ["expense-1"])
        stored = self.database.get_expense("expense-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, "queued")

    def test_remove_retry_is_idempotent(self) -> None:
        self.create_expense()
        self.database.enqueue_retry(
            "expense-1",
            next_attempt_at="2026-10-26T10:00:00+00:00",
            error_code="portal_timeout",
        )

        self.assertTrue(self.database.remove_retry("expense-1"))
        self.assertFalse(self.database.remove_retry("expense-1"))

    def test_conversation_state_round_trips(self) -> None:
        self.database.save_conversation_state(
            12345, "awaiting_amount", {"expense_id": "expense-1"}
        )

        self.assertEqual(
            self.database.get_conversation_state(12345),
            ("awaiting_amount", {"expense_id": "expense-1"}),
        )
        self.assertTrue(self.database.delete_conversation_state(12345))
        self.assertIsNone(self.database.get_conversation_state(12345))

    def test_recent_expenses_are_scoped_to_user(self) -> None:
        self.create_expense(expense_id="expense-a", sha256="a" * 64, user_id=12345)
        self.create_expense(expense_id="expense-b", sha256="b" * 64, user_id=67890)

        recent = self.database.list_recent_expenses(12345)

        self.assertEqual([expense.expense_id for expense in recent], ["expense-a"])

    def test_invalid_expense_status_is_rejected(self) -> None:
        self.create_expense()

        with self.assertRaisesRegex(ValueError, "status"):
            self.database.update_expense_status("expense-1", "unknown")

    def test_updates_expense_payload(self) -> None:
        self.create_expense()

        self.database.update_expense_payload(
            "expense-1",
            {"merchant": "Updated Shop", "total_amount": "15.00"},
        )

        stored = self.database.get_expense("expense-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.payload["merchant"], "Updated Shop")
        self.assertEqual(stored.payload["total_amount"], "15.00")

    def test_deleting_received_expense_releases_duplicate_hash(self) -> None:
        self.create_expense()

        self.assertTrue(self.database.delete_expense("expense-1"))
        self.assertFalse(self.database.contains_file_hash("a" * 64))
        self.assertFalse(self.database.delete_expense("expense-1"))

    def test_cannot_delete_submitted_expense(self) -> None:
        self.create_expense()
        self.database.update_expense_status("expense-1", "submitted")

        self.assertFalse(self.database.delete_expense("expense-1"))

    def test_accepts_dry_run_status(self) -> None:
        self.create_expense()

        self.database.update_expense_status("expense-1", "dry_run")

        stored = self.database.get_expense("expense-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.status, "dry_run")


if __name__ == "__main__":
    unittest.main()
