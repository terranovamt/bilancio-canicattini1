from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from bilancio.client import AccountLedger, PortalExpense, PortalOption, PortalSubmissionResult
from db.ledger_snapshot import LedgerSnapshot, compact_ledgers
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_EXPENSE_STATUSES = {
    "received",
    "confirmed",
    "queued",
    "submitted",
    "dry_run",
    "failed",
    "cancelled",
}


@dataclass(frozen=True, slots=True)
class StoredExpense:
    expense_id: str
    telegram_user_id: int
    payload: dict[str, object]
    status: str
    archive_path: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class RetryEntry:
    expense_id: str
    attempts: int
    next_attempt_at: str
    last_error_code: str | None


@dataclass(frozen=True, slots=True)
class VerifiedMovement:
    account_value: str
    account_label: str
    direction: str
    amount: Decimal
    verified_at: datetime


class Database:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS expenses (
                    expense_id TEXT PRIMARY KEY,
                    telegram_user_id INTEGER NOT NULL CHECK (telegram_user_id > 0),
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'received', 'confirmed', 'queued',
                            'submitted', 'dry_run', 'failed', 'cancelled'
                        )
                    ),
                    archive_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS file_hashes (
                    sha256 TEXT PRIMARY KEY,
                    expense_id TEXT NOT NULL UNIQUE
                        REFERENCES expenses(expense_id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS retry_queue (
                    expense_id TEXT PRIMARY KEY
                        REFERENCES expenses(expense_id) ON DELETE CASCADE,
                    attempts INTEGER NOT NULL CHECK (attempts > 0),
                    next_attempt_at TEXT NOT NULL,
                    last_error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversation_states (
                    telegram_user_id INTEGER PRIMARY KEY
                        CHECK (telegram_user_id > 0),
                    state TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bot_mode (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    test_mode INTEGER NOT NULL CHECK (test_mode IN (0, 1))
                );

                CREATE INDEX IF NOT EXISTS retry_queue_due_idx
                    ON retry_queue(next_attempt_at);
                CREATE INDEX IF NOT EXISTS expenses_user_created_idx
                    ON expenses(telegram_user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS ledger_snapshots (
                    start_year INTEGER PRIMARY KEY,
                    read_at TEXT NOT NULL,
                    balances_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS verified_submissions (
                    expense_id TEXT PRIMARY KEY
                        REFERENCES expenses(expense_id) ON DELETE CASCADE,
                    verified_at TEXT NOT NULL,
                    account_value TEXT NOT NULL,
                    account_label TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK (direction IN ('entrata', 'uscita')),
                    amount TEXT NOT NULL,
                    dry_run INTEGER NOT NULL CHECK (dry_run IN (0, 1)),
                    already_present INTEGER NOT NULL CHECK (already_present IN (0, 1))
                );
                CREATE INDEX IF NOT EXISTS verified_submissions_time_idx
                    ON verified_submissions(verified_at);
                """
            )

    def save_ledger_snapshot(
        self, start_year: int, read_at: datetime, ledgers: Sequence[AccountLedger],
    ) -> None:
        compact = compact_ledgers(ledgers)
        if read_at.tzinfo is None or read_at.utcoffset() is None:
            raise ValueError("Snapshot timestamp must be timezone-aware")
        balances = [
            {
                "value": ledger.account.value, "label": ledger.account.label,
                "opening": str(ledger.opening_balance), "income": str(ledger.total_income),
                "expense": str(ledger.total_expense), "balance": str(ledger.balance),
            }
            for ledger in compact
        ]
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO ledger_snapshots (start_year, read_at, balances_json)
                VALUES (?, ?, ?)
                ON CONFLICT(start_year) DO UPDATE SET
                    read_at = excluded.read_at, balances_json = excluded.balances_json
                WHERE excluded.read_at >= ledger_snapshots.read_at
                """,
                (start_year, read_at.astimezone(timezone.utc).isoformat(), json.dumps(balances)),
            )

    def get_ledger_snapshot(self, start_year: int) -> LedgerSnapshot | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT read_at, balances_json FROM ledger_snapshots WHERE start_year = ?",
                (start_year,),
            ).fetchone()
        if row is None:
            return None
        read_at = datetime.fromisoformat(row["read_at"])
        if read_at.tzinfo is None:
            raise ValueError("Stored snapshot timestamp must be timezone-aware")
        balances = json.loads(row["balances_json"])
        ledgers = [
            AccountLedger(
                PortalOption(item["value"], item["label"]), Decimal(item["opening"]),
                Decimal(item["income"]), Decimal(item["expense"]), Decimal(item["balance"]),
                entries=(), total_rows=0, complete=True, entries_available=False,
            )
            for item in balances
        ]
        return LedgerSnapshot(start_year, read_at, compact_ledgers(ledgers))

    def record_verified_submission(
        self, expense_id: str, expense: PortalExpense, result: PortalSubmissionResult,
    ) -> None:
        """Atomically store the first verification event and the final operation status."""
        if not result.verified:
            raise ValueError("Submission must be verified")
        now = self._now()
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE expenses SET status = ?, updated_at = ? WHERE expense_id = ?",
                ("dry_run" if result.dry_run else "submitted", now, expense_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("Expense not found")
            connection.execute(
                """
                INSERT INTO verified_submissions (
                    expense_id, verified_at, account_value, account_label,
                    direction, amount, dry_run, already_present
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(expense_id) DO NOTHING
                """,
                (expense_id, now, expense.account.value, expense.account.label,
                 expense.direction.value, str(expense.amount), int(result.dry_run),
                 int(result.confirmation_id == "existing-entry")),
            )

    def list_verified_movements(self, start: datetime, end: datetime) -> list[VerifiedMovement]:
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("Movement interval must be aware and increasing")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT v.account_value, v.account_label, v.direction, v.amount, v.verified_at
                FROM verified_submissions v JOIN expenses e ON e.expense_id = v.expense_id
                WHERE e.status = 'submitted' AND v.dry_run = 0 AND v.already_present = 0
                  AND v.verified_at >= ? AND v.verified_at < ?
                ORDER BY v.verified_at, v.expense_id
                """,
                (start.astimezone(timezone.utc).isoformat(timespec="seconds"),
                 end.astimezone(timezone.utc).isoformat(timespec="seconds")),
            ).fetchall()
        return [
            VerifiedMovement(
                row["account_value"], row["account_label"], row["direction"],
                Decimal(row["amount"]), datetime.fromisoformat(row["verified_at"]),
            )
            for row in rows
        ]

    def get_test_mode(self, *, default: bool) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT test_mode FROM bot_mode WHERE singleton = 1"
            ).fetchone()
        return default if row is None else bool(row[0])

    def set_test_mode(self, enabled: bool) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO bot_mode (singleton, test_mode) VALUES (1, ?) "
                "ON CONFLICT(singleton) DO UPDATE SET test_mode = excluded.test_mode",
                (int(enabled),),
            )

    def contains_file_hash(self, sha256: str) -> bool:
        self._validate_hash(sha256)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM file_hashes WHERE sha256 = ?",
                (sha256,),
            ).fetchone()
        return row is not None

    def create_expense(
        self,
        *,
        expense_id: str,
        telegram_user_id: int,
        sha256: str,
        payload: Mapping[str, object],
        archive_path: str | None = None,
    ) -> bool:
        if not expense_id.strip():
            raise ValueError("expense_id must not be empty")
        if telegram_user_id <= 0:
            raise ValueError("telegram_user_id must be positive")
        self._validate_hash(sha256)

        now = self._now()
        payload_json = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT 1 FROM file_hashes WHERE sha256 = ?",
                (sha256,),
            ).fetchone()
            if duplicate is not None:
                connection.rollback()
                return False

            connection.execute(
                """
                INSERT INTO expenses (
                    expense_id, telegram_user_id, payload_json, status,
                    archive_path, created_at, updated_at
                ) VALUES (?, ?, ?, 'received', ?, ?, ?)
                """,
                (
                    expense_id,
                    telegram_user_id,
                    payload_json,
                    archive_path,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO file_hashes (sha256, expense_id, created_at)
                VALUES (?, ?, ?)
                """,
                (sha256, expense_id, now),
            )
        return True

    def create_manual_expense(
        self,
        *,
        expense_id: str,
        telegram_user_id: int,
        payload: Mapping[str, object],
    ) -> None:
        if not expense_id.strip():
            raise ValueError("expense_id must not be empty")
        if telegram_user_id <= 0:
            raise ValueError("telegram_user_id must be positive")

        now = self._now()
        payload_json = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO expenses (
                    expense_id, telegram_user_id, payload_json, status,
                    archive_path, created_at, updated_at
                ) VALUES (?, ?, ?, 'received', NULL, ?, ?)
                """,
                (expense_id, telegram_user_id, payload_json, now, now),
            )

    def get_expense(self, expense_id: str) -> StoredExpense | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT expense_id, telegram_user_id, payload_json, status,
                       archive_path, created_at, updated_at
                FROM expenses
                WHERE expense_id = ?
                """,
                (expense_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise ValueError("Stored expense payload must be a JSON object")
        return StoredExpense(
            expense_id=row["expense_id"],
            telegram_user_id=row["telegram_user_id"],
            payload=payload,
            status=row["status"],
            archive_path=row["archive_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_recent_expenses(
        self, telegram_user_id: int, limit: int = 10
    ) -> list[StoredExpense]:
        if telegram_user_id <= 0:
            raise ValueError("telegram_user_id must be positive")
        if limit <= 0:
            raise ValueError("limit must be positive")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT expense_id, telegram_user_id, payload_json, status,
                       archive_path, created_at, updated_at
                FROM expenses
                WHERE telegram_user_id = ?
                ORDER BY created_at DESC, expense_id DESC
                LIMIT ?
                """,
                (telegram_user_id, limit),
            ).fetchall()
        return [self._stored_expense(row) for row in rows]

    def list_retryable_expenses(
        self,
        telegram_user_id: int,
        limit: int = 20,
    ) -> list[StoredExpense]:
        if telegram_user_id <= 0:
            raise ValueError("telegram_user_id must be positive")
        if limit <= 0:
            raise ValueError("limit must be positive")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT expense_id, telegram_user_id, payload_json, status,
                       archive_path, created_at, updated_at
                FROM expenses
                WHERE telegram_user_id = ? AND status IN ('queued', 'failed')
                ORDER BY updated_at DESC, expense_id DESC
                LIMIT ?
                """,
                (telegram_user_id, limit),
            ).fetchall()
        return [self._stored_expense(row) for row in rows]

    def request_retry_for_user(self, telegram_user_id: int) -> int:
        if telegram_user_id <= 0:
            raise ValueError("telegram_user_id must be positive")
        now = self._now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT expense_id
                FROM expenses
                WHERE telegram_user_id = ? AND status IN ('queued', 'failed')
                """,
                (telegram_user_id,),
            ).fetchall()
            for row in rows:
                expense_id = row["expense_id"]
                connection.execute(
                    """
                    INSERT INTO retry_queue (
                        expense_id, attempts, next_attempt_at, last_error_code,
                        created_at, updated_at
                    ) VALUES (?, 1, ?, 'manual_retry', ?, ?)
                    ON CONFLICT(expense_id) DO UPDATE SET
                        attempts = 1,
                        next_attempt_at = excluded.next_attempt_at,
                        last_error_code = excluded.last_error_code,
                        updated_at = excluded.updated_at
                    """,
                    (expense_id, now, now, now),
                )
                connection.execute(
                    """
                    UPDATE expenses
                    SET status = 'queued', updated_at = ?
                    WHERE expense_id = ?
                    """,
                    (now, expense_id),
                )
        return len(rows)

    def update_expense_status(
        self,
        expense_id: str,
        status: str,
        archive_path: str | None = None,
    ) -> None:
        if status not in _EXPENSE_STATUSES:
            raise ValueError("Unsupported expense status")
        now = self._now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE expenses
                SET status = ?,
                    archive_path = COALESCE(?, archive_path),
                    updated_at = ?
                WHERE expense_id = ?
                """,
                (status, archive_path, now, expense_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Expense not found: {expense_id}")

    def update_expense_payload(
        self,
        expense_id: str,
        payload: Mapping[str, object],
    ) -> None:
        payload_json = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)
        now = self._now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE expenses
                SET payload_json = ?, updated_at = ?
                WHERE expense_id = ?
                """,
                (payload_json, now, expense_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Expense not found: {expense_id}")

    def delete_expense(self, expense_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                DELETE FROM expenses
                WHERE expense_id = ? AND status IN ('received', 'cancelled')
                """,
                (expense_id,),
            )
        return cursor.rowcount > 0

    def enqueue_retry(
        self,
        expense_id: str,
        *,
        next_attempt_at: str,
        error_code: str,
    ) -> RetryEntry:
        if not next_attempt_at.strip():
            raise ValueError("next_attempt_at must not be empty")
        if not error_code.strip():
            raise ValueError("error_code must not be empty")
        now = self._now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO retry_queue (
                    expense_id, attempts, next_attempt_at, last_error_code,
                    created_at, updated_at
                ) VALUES (?, 1, ?, ?, ?, ?)
                ON CONFLICT(expense_id) DO UPDATE SET
                    attempts = retry_queue.attempts + 1,
                    next_attempt_at = excluded.next_attempt_at,
                    last_error_code = excluded.last_error_code,
                    updated_at = excluded.updated_at
                """,
                (expense_id, next_attempt_at, error_code, now, now),
            )
            connection.execute(
                """
                UPDATE expenses
                SET status = 'queued', updated_at = ?
                WHERE expense_id = ?
                """,
                (now, expense_id),
            )
            row = connection.execute(
                """
                SELECT expense_id, attempts, next_attempt_at, last_error_code
                FROM retry_queue
                WHERE expense_id = ?
                """,
                (expense_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Retry queue entry was not persisted")
        return RetryEntry(
            expense_id=row["expense_id"],
            attempts=row["attempts"],
            next_attempt_at=row["next_attempt_at"],
            last_error_code=row["last_error_code"],
        )

    def get_due_retries(
        self,
        before: str,
        limit: int = 20,
        *,
        telegram_user_id: int | None = None,
    ) -> list[RetryEntry]:
        if not before.strip():
            raise ValueError("before must not be empty")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if telegram_user_id is not None and telegram_user_id <= 0:
            raise ValueError("telegram_user_id must be positive")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT r.expense_id, r.attempts, r.next_attempt_at,
                       r.last_error_code
                FROM retry_queue AS r
                JOIN expenses AS e ON e.expense_id = r.expense_id
                WHERE r.next_attempt_at <= ?
                  AND (? IS NULL OR e.telegram_user_id = ?)
                ORDER BY r.next_attempt_at, r.expense_id
                LIMIT ?
                """,
                (before, telegram_user_id, telegram_user_id, limit),
            ).fetchall()
        return [
            RetryEntry(
                expense_id=row["expense_id"],
                attempts=row["attempts"],
                next_attempt_at=row["next_attempt_at"],
                last_error_code=row["last_error_code"],
            )
            for row in rows
        ]

    def remove_retry(self, expense_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM retry_queue WHERE expense_id = ?",
                (expense_id,),
            )
        return cursor.rowcount > 0

    def save_conversation_state(
        self,
        telegram_user_id: int,
        state: str,
        data: Mapping[str, object],
    ) -> None:
        if telegram_user_id <= 0:
            raise ValueError("telegram_user_id must be positive")
        if not state.strip():
            raise ValueError("state must not be empty")
        now = self._now()
        data_json = json.dumps(dict(data), ensure_ascii=False, sort_keys=True)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO conversation_states (
                    telegram_user_id, state, data_json, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    state = excluded.state,
                    data_json = excluded.data_json,
                    updated_at = excluded.updated_at
                """,
                (telegram_user_id, state, data_json, now),
            )

    def get_conversation_state(
        self, telegram_user_id: int
    ) -> tuple[str, dict[str, object]] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT state, data_json
                FROM conversation_states
                WHERE telegram_user_id = ?
                """,
                (telegram_user_id,),
            ).fetchone()
        if row is None:
            return None
        data = json.loads(row["data_json"])
        if not isinstance(data, dict):
            raise ValueError("Stored conversation data must be a JSON object")
        return row["state"], data

    def delete_conversation_state(self, telegram_user_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM conversation_states WHERE telegram_user_id = ?",
                (telegram_user_id,),
            )
        return cursor.rowcount > 0

    def transaction_type_usage(
        self,
        account_label: str,
        direction: str,
    ) -> dict[str, int]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM expenses
                WHERE status IN ('submitted', 'dry_run')
                """
            ).fetchall()
        usage: dict[str, int] = {}
        for row in rows:
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, dict):
                continue
            account = payload.get("account")
            transaction_type = payload.get("transaction_type")
            if (
                not isinstance(account, dict)
                or not isinstance(transaction_type, dict)
                or account.get("label") != account_label
                or payload.get("direction") != direction
            ):
                continue
            label = transaction_type.get("label")
            if isinstance(label, str):
                usage[label] = usage.get(label, 0) + 1
        return usage

    def clear_history(self) -> dict[str, int]:
        """Delete every local expense, retry and conversation state."""
        with self._connection() as connection:
            counts = {
                "expenses": connection.execute(
                    "SELECT COUNT(*) FROM expenses"
                ).fetchone()[0],
                "retries": connection.execute(
                    "SELECT COUNT(*) FROM retry_queue"
                ).fetchone()[0],
            }
            connection.execute("DELETE FROM retry_queue")
            connection.execute("DELETE FROM conversation_states")
            connection.execute("DELETE FROM file_hashes")
            connection.execute("DELETE FROM expenses")
        return counts

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _validate_hash(sha256: str) -> None:
        if not _HASH_PATTERN.fullmatch(sha256):
            raise ValueError("sha256 must be a 64-character lowercase hexadecimal digest")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _stored_expense(row: sqlite3.Row) -> StoredExpense:
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise ValueError("Stored expense payload must be a JSON object")
        return StoredExpense(
            expense_id=row["expense_id"],
            telegram_user_id=row["telegram_user_id"],
            payload=payload,
            status=row["status"],
            archive_path=row["archive_path"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
