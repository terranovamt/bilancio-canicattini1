from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from bilancio.client import (
    AccountLedger, BilancioClientError, LedgerEntry, PortalExpense, PortalOption,
    PortalSubmissionResult, TransactionDirection,
)
from bot.application import create_application, ledger_summary_job, summary_callback, summary_command
from bot.daily import daily_ledger_job, format_daily_report, previous_day_interval
from bot.daily import local_now
from config import Settings
from db.database import Database, VerifiedMovement

ROME = ZoneInfo("Europe/Rome")


def sample_ledger() -> AccountLedger:
    return AccountLedger(
        PortalOption("account-a", "Cassa A"), Decimal("100"), Decimal("0.30"),
        Decimal("0.10"), Decimal("100.20"),
        (LedgerEntry(date(2026, 10, 1), "PRIVATE_DESCRIPTION", Decimal("0.30"), Decimal("0")),),
        total_rows=1, complete=True,
    )


def sample_expense(amount: str = "0.10", direction: str = "uscita", account: str = "A") -> PortalExpense:
    return PortalExpense(
        date(2001, 1, 1), "PRIVATE_DESCRIPTION", Decimal(amount),
        TransactionDirection(direction), PortalOption(f"account-{account}", f"Cassa {account}"),
        None, None, None, "PRIVATE_NOTE",
    )


class CalendarTests(unittest.TestCase):
    def test_previous_calendar_day_at_month_and_year_rollover(self) -> None:
        for moment, expected in (
            (datetime(2027, 1, 1, tzinfo=ROME), date(2026, 12, 31)),
            (datetime(2026, 10, 1, tzinfo=ROME), date(2026, 9, 30)),
            (datetime(2028, 3, 1, tzinfo=ROME), date(2028, 2, 29)),
        ):
            with self.subTest(moment=moment):
                day, start, end = previous_day_interval(moment)
                self.assertEqual(day, expected)
                self.assertEqual(start.hour, 0)
                self.assertEqual(end.date(), moment.date())

    def test_dst_previous_day_is_23_or_25_hours(self) -> None:
        for moment, hours in (
            (datetime(2026, 3, 30, tzinfo=ROME), 23),
            (datetime(2026, 10, 26, tzinfo=ROME), 25),
        ):
            _, start, end = previous_day_interval(moment)
            self.assertEqual(
                end.astimezone(timezone.utc) - start.astimezone(timezone.utc),
                timedelta(hours=hours),
            )

    def test_configured_timezone_not_system_day_drives_rollover(self) -> None:
        utc = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)
        with patch("bot.daily.datetime", wraps=datetime) as clock:
            clock.now.return_value = utc
            rome = local_now("Europe/Rome")
            new_york = local_now("America/New_York")
        self.assertEqual(rome.date(), date(2026, 10, 1))
        self.assertEqual(new_york.date(), date(2026, 9, 30))
        self.assertEqual(previous_day_interval(rome)[0], date(2026, 9, 30))
        self.assertEqual(previous_day_interval(new_york)[0], date(2026, 9, 29))

    def test_report_decimal_totals_global_and_per_account(self) -> None:
        movements = [
            VerifiedMovement("a", "Cassa A", "entrata", Decimal("0.10"), datetime.now(ROME)),
            VerifiedMovement("a", "Cassa A", "entrata", Decimal("0.20"), datetime.now(ROME)),
            VerifiedMovement("b", "Cassa B", "uscita", Decimal("1.11"), datetime.now(ROME)),
        ]
        text = "\n".join(format_daily_report(date(2026, 10, 31), movements))
        self.assertIn("Operazioni: 3", text)
        self.assertIn("Entrate: 2 · 0,30 €", text)
        self.assertIn("Uscite: 1 · 1,11 €", text)
        self.assertIn("Cassa A\nOperazioni: 2", text)
        self.assertIn("Cassa B\nOperazioni: 1", text)
        self.assertIn("non data della ricevuta", text)

    def test_report_splits_large_account_list(self) -> None:
        movements = [
            VerifiedMovement(str(i), f"Cassa {i}", "uscita", Decimal("1"), datetime.now(ROME))
            for i in range(100)
        ]
        messages = format_daily_report(date(2026, 10, 31), movements)
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) <= 3800 for message in messages))


class PersistentDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "bot.sqlite3"
        self.database = Database(self.path)
        self.database.initialize()
        self.moment = datetime(2026, 10, 31, 23, 59, tzinfo=ROME)

    def create(self, key: str) -> None:
        self.database.create_manual_expense(
            expense_id=key, telegram_user_id=12345,
            payload={"merchant": "PRIVATE_DESCRIPTION", "portal_notes": "PRIVATE_NOTE"},
        )

    def record(self, key: str, *, moment: datetime | None = None,
               result: PortalSubmissionResult | None = None, expense: PortalExpense | None = None) -> None:
        with patch.object(Database, "_now", return_value=(moment or self.moment).astimezone(
            timezone.utc).isoformat(timespec="seconds")):
            self.database.record_verified_submission(
                key, expense or sample_expense(), result or PortalSubmissionResult(True),
            )

    def test_snapshot_restart_and_year_isolation_without_details(self) -> None:
        self.database.save_ledger_snapshot(2026, self.moment, [sample_ledger()])
        restarted = Database(self.path)
        restarted.initialize()
        snapshot = restarted.get_ledger_snapshot(2026)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.start_year, 2026)
        self.assertEqual(snapshot.read_at, self.moment)
        self.assertEqual(snapshot.ledgers[0].balance, Decimal("100.20"))
        self.assertFalse(snapshot.ledgers[0].entries_available)
        self.assertEqual(snapshot.ledgers[0].entries, ())
        self.assertIsNone(restarted.get_ledger_snapshot(2025))
        with closing(sqlite3.connect(self.path)) as connection:
            data = connection.execute("SELECT balances_json FROM ledger_snapshots").fetchone()[0]
        self.assertNotIn("PRIVATE", data)

    def test_invalid_read_does_not_replace_snapshot(self) -> None:
        self.database.save_ledger_snapshot(2026, self.moment, [sample_ledger()])
        invalid_sets = [
            [], [sample_ledger(), sample_ledger()],
            [replace(sample_ledger(), balance=Decimal("NaN"))],
            [replace(sample_ledger(), balance=Decimal("0"))],
            [replace(sample_ledger(), total_income=Decimal("-1"))],
            [replace(sample_ledger(), complete=False, entries_available=False)],
        ]
        for ledgers in invalid_sets:
            with self.subTest(ledgers=ledgers):
                with self.assertRaises(ValueError):
                    self.database.save_ledger_snapshot(2026, self.moment + timedelta(days=1), ledgers)
                self.assertEqual(self.database.get_ledger_snapshot(2026).read_at, self.moment)

    def test_older_concurrent_read_does_not_overwrite_newer_snapshot(self) -> None:
        self.database.save_ledger_snapshot(2026, self.moment, [sample_ledger()])
        self.database.save_ledger_snapshot(2026, self.moment - timedelta(hours=1), [sample_ledger()])
        self.assertEqual(self.database.get_ledger_snapshot(2026).read_at, self.moment)

    def test_safe_migration_preserves_legacy_operations_and_does_not_invent_events(self) -> None:
        self.create("legacy")
        self.database.update_expense_status("legacy", "submitted")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("DROP TABLE verified_submissions")
            connection.execute("DROP TABLE ledger_snapshots")
        self.database.initialize()
        self.database.initialize()
        self.assertEqual(self.database.get_expense("legacy").status, "submitted")
        _, start, end = previous_day_interval(datetime(2026, 11, 1, tzinfo=ROME))
        self.assertEqual(self.database.list_verified_movements(start, end), [])

    def test_exclusions_and_first_verification_timestamp_not_payload_update(self) -> None:
        for key in ("real", "trial", "existing", "failed", "received", "queued", "legacy"):
            self.create(key)
        self.record("real")
        self.record("trial", result=PortalSubmissionResult(True, dry_run=True))
        self.record("existing", result=PortalSubmissionResult(True, confirmation_id="existing-entry"))
        self.record("failed")
        self.database.update_expense_status("failed", "failed")
        self.database.update_expense_status("queued", "queued")
        self.database.update_expense_status("legacy", "submitted")
        self.database.update_expense_payload("real", {"merchant": "CHANGED_PRIVATE"})
        self.record("real", moment=self.moment + timedelta(days=1),
                    result=PortalSubmissionResult(True, confirmation_id="existing-entry"))
        _, start, end = previous_day_interval(datetime(2026, 11, 1, tzinfo=ROME))
        movements = self.database.list_verified_movements(start, end)
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0].verified_at, self.moment)
        self.assertEqual(movements[0].amount, Decimal("0.10"))
        self.assertNotIn("PRIVATE", repr(movements))

    def test_half_open_interval_and_dst_fold(self) -> None:
        day, start, end = previous_day_interval(datetime(2026, 10, 26, tzinfo=ROME))
        for key, moment in (
            ("before", start - timedelta(seconds=1)), ("start", start), ("end", end),
            ("fold0", datetime(2026, 10, 25, 2, 30, tzinfo=ROME, fold=0)),
            ("fold1", datetime(2026, 10, 25, 2, 30, tzinfo=ROME, fold=1)),
        ):
            self.create(key)
            self.record(key, moment=moment)
        self.assertEqual(day, date(2026, 10, 25))
        self.assertEqual(len(self.database.list_verified_movements(start, end)), 3)

    def test_unverified_result_rejected_without_status_change(self) -> None:
        self.create("unverified")
        with self.assertRaises(ValueError):
            self.record("unverified", result=PortalSubmissionResult(False))
        self.assertEqual(self.database.get_expense("unverified").status, "received")


class DailyJobTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "bot.sqlite3"
        self.database = Database(self.path)
        self.database.initialize()
        self.settings = Settings.from_env({
            "BOT_TOKEN": "test-token-placeholder", "ALLOWED_USER_IDS": "12345,67890",
            "ADMIN_USER_ID": "12345", "DRY_RUN": "true", "DATA_PATH": self.directory.name,
            "STORAGE_PATH": self.directory.name, "TZ": "Europe/Rome",
        })
        self.moment = datetime(2026, 10, 1, tzinfo=ROME)
        self.client = SimpleNamespace(
            read_ledgers=AsyncMock(return_value=[sample_ledger()]), create_expense=AsyncMock(),
        )
        self.context = SimpleNamespace(
            application=SimpleNamespace(bot_data={
                "settings": self.settings, "database": self.database, "bilancio_client": self.client,
            }),
            bot=SimpleNamespace(send_message=AsyncMock(), edit_message_text=AsyncMock()),
        )

    async def run_daily(self) -> None:
        with patch("bot.daily.local_now", return_value=self.moment):
            await daily_ledger_job(self.context)

    def texts(self) -> str:
        return "\n".join(call.kwargs.get("text", "") for call in self.context.bot.send_message.await_args_list)

    async def test_midnight_refresh_current_scout_year_previous_day_and_admin_only(self) -> None:
        await self.run_daily()
        self.client.read_ledgers.assert_awaited_once_with(
            transaction_date=date(2026, 10, 1), include_entries=False,
        )
        self.client.create_expense.assert_not_awaited()
        self.assertIsNotNone(self.database.get_ledger_snapshot(2026))
        self.assertIsNone(self.database.get_ledger_snapshot(2025))
        text = self.texts()
        for expected in ("✅", "26-27", "30/09/2026", "zero movimenti", "Entrate: 0 · 0,00 €"):
            self.assertIn(expected, text)
        self.assertNotIn("PRIVATE", text)
        for call in self.context.bot.send_message.await_args_list:
            self.assertEqual(call.kwargs["chat_id"], 12345)

    async def test_failure_or_invalid_read_notifies_admin_and_preserves_snapshot(self) -> None:
        self.database.save_ledger_snapshot(2026, self.moment - timedelta(days=1), [sample_ledger()])
        for failure in (BilancioClientError("PRIVATE_ERROR"), ValueError("PRIVATE_ERROR"), None):
            with self.subTest(failure=failure):
                self.context.bot.send_message.reset_mock()
                self.client.read_ledgers.side_effect = failure
                self.client.read_ledgers.return_value = []
                with self.assertLogs("bot.daily", level="WARNING") as logs:
                    await self.run_daily()
                self.assertNotIn("PRIVATE", " ".join(logs.output))
                self.assertIn("❌", self.texts())
                self.assertIn("zero movimenti", self.texts())
                self.assertEqual(
                    self.database.get_ledger_snapshot(2026).read_at,
                    self.moment - timedelta(days=1),
                )
                self.assertTrue(all(call.kwargs["chat_id"] == 12345
                                    for call in self.context.bot.send_message.await_args_list))

    async def test_no_client_still_sends_admin_failure(self) -> None:
        self.context.application.bot_data["bilancio_client"] = None
        await self.run_daily()
        self.assertIn("❌", self.texts())
        self.assertIn("zero movimenti", self.texts())

    async def test_snapshot_db_write_failure_reports_read_success_and_preserves_cache(self) -> None:
        self.database.save_ledger_snapshot(2026, self.moment - timedelta(days=1), [sample_ledger()])
        with patch.object(self.database, "save_ledger_snapshot", side_effect=sqlite3.OperationalError()):
            await self.run_daily()
        self.assertIn("❌", self.texts())
        self.assertIn("✅ Saldi letti dal portale", self.texts())
        self.assertIn("Non riesco a salvare lo snapshot", self.texts())
        self.assertEqual(self.database.get_ledger_snapshot(2026).read_at,
                         self.moment - timedelta(days=1))

    async def test_report_contains_only_previous_day_verified_real_bot_operations(self) -> None:
        for key, result, moment in (
            ("real", PortalSubmissionResult(True), self.moment - timedelta(seconds=1)),
            ("today", PortalSubmissionResult(True), self.moment),
            ("trial", PortalSubmissionResult(True, dry_run=True), self.moment - timedelta(hours=1)),
            ("existing", PortalSubmissionResult(True, confirmation_id="existing-entry"),
             self.moment - timedelta(hours=1)),
        ):
            self.database.create_manual_expense(expense_id=key, telegram_user_id=67890, payload={})
            with patch.object(Database, "_now", return_value=moment.astimezone(
                    timezone.utc).isoformat(timespec="seconds")):
                self.database.record_verified_submission(key, sample_expense(), result)
        await self.run_daily()
        text = self.texts()
        self.assertIn("Operazioni: 1", text)
        self.assertIn("Uscite: 1 · 0,10 €", text)
        self.assertNotIn("67890", text)
        self.assertNotIn("PRIVATE", text)

    async def test_report_db_error_does_not_claim_zero_and_still_notifies(self) -> None:
        with patch.object(self.database, "list_verified_movements", side_effect=sqlite3.OperationalError()):
            await self.run_daily()
        self.assertIn("✅", self.texts())
        self.assertIn("Non riesco a calcolare", self.texts())
        self.assertNotIn("zero movimenti", self.texts())

    async def test_scheduler_local_midnight_with_dst_and_no_client(self) -> None:
        application = create_application(self.settings, self.database)
        jobs = application.job_queue.get_jobs_by_name("portal-balances-daily")
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertIs(job.callback, daily_ledger_job)
        trigger = job.job.trigger
        self.assertEqual(str(trigger.timezone), "Europe/Rome")
        for moment in (datetime(2026, 3, 28, 12, tzinfo=ROME),
                       datetime(2026, 10, 24, 12, tzinfo=ROME)):
            previous = None
            for _ in range(4):
                upcoming = trigger.get_next_fire_time(previous, moment)
                local = upcoming.astimezone(ROME)
                self.assertEqual((local.hour, local.minute), (0, 0))
                previous = upcoming
                moment = upcoming + timedelta(seconds=1)

    async def run_summary(self, year: int = 2026, details: bool = False) -> None:
        self.context.job = SimpleNamespace(data={
            "chat_id": 67890, "message_id": 42, "start_year": year, "details": details,
        })
        with patch("bot.application.local_now", return_value=self.moment):
            await ledger_summary_job(self.context)

    async def test_live_summary_saves_snapshot_for_requested_year(self) -> None:
        await self.run_summary(2025)
        self.assertIsNotNone(self.database.get_ledger_snapshot(2025))
        self.assertIsNone(self.database.get_ledger_snapshot(2026))
        self.client.read_ledgers.assert_awaited_once_with(
            transaction_date=date(2026, 9, 30), include_entries=False,
        )

    async def test_cache_restart_fallback_same_year_only_and_details_explicit(self) -> None:
        self.database.save_ledger_snapshot(2026, self.moment, [sample_ledger()])
        self.context.application.bot_data["database"] = Database(self.path)
        self.client.read_ledgers.side_effect = BilancioClientError("PRIVATE_ERROR")
        for details in (False, True):
            self.context.bot.send_message.reset_mock()
            await self.run_summary(details=details)
            initial = self.context.bot.edit_message_text.await_args.args[0]
            self.assertIn("Non riesco a leggere", initial)
            self.assertIn("Ultimi dati letti dal portale il 01/10/2026 alle 00:00:00", initial)
            self.assertIn("Solo saldi", initial)
            self.assertIn("dettagli non disponibili", initial)
            self.assertIn("100,20 €", self.texts())
            self.assertNotIn("Andamento", self.texts())
            self.assertNotIn("PRIVATE", initial + self.texts())
        self.context.bot.send_message.reset_mock()
        await self.run_summary(2025)
        self.assertNotIn("Ultimi dati", self.context.bot.edit_message_text.await_args.args[0])
        self.context.bot.send_message.assert_not_awaited()

    async def test_invalid_live_read_uses_cache_and_preserves_it(self) -> None:
        self.database.save_ledger_snapshot(2026, self.moment - timedelta(days=1), [sample_ledger()])
        self.client.read_ledgers.return_value = [replace(sample_ledger(), balance=Decimal("NaN"))]
        await self.run_summary()
        self.assertIn("Ultimi dati", self.context.bot.edit_message_text.await_args.args[0])
        self.assertEqual(self.database.get_ledger_snapshot(2026).read_at,
                         self.moment - timedelta(days=1))

    async def test_live_summary_cache_write_failure_does_not_hide_live_balances(self) -> None:
        with patch.object(self.database, "save_ledger_snapshot", side_effect=sqlite3.OperationalError()):
            await self.run_summary()
        self.assertIn("100,20 €", self.context.bot.edit_message_text.await_args.args[0])
        self.assertIn("non salvati nella cache", self.texts())

    async def test_summary_missing_client_can_still_use_persistent_snapshot(self) -> None:
        self.database.save_ledger_snapshot(2026, self.moment, [sample_ledger()])
        self.context.application.bot_data["bilancio_client"] = None
        await self.run_summary()
        self.assertIn("Ultimi dati", self.context.bot.edit_message_text.await_args.args[0])
        self.assertIn("100,20 €", self.texts())

    async def test_summary_date_uses_configured_zone_at_scout_year_rollover(self) -> None:
        progress = SimpleNamespace(chat_id=67890, message_id=42)
        message = SimpleNamespace(reply_text=AsyncMock(return_value=progress))
        self.context.application.job_queue = SimpleNamespace(run_once=Mock())
        with patch("bot.application.local_now", return_value=self.moment):
            await summary_command(SimpleNamespace(message=message), self.context)
        scheduled = self.context.application.job_queue.run_once.call_args.kwargs["data"]
        self.assertEqual(scheduled["start_year"], 2026)
        self.context.application.bot_data["ledger_summary_running"] = False
        query = SimpleNamespace(
            data="summary:2026", message=progress, answer=AsyncMock(), edit_message_text=AsyncMock(),
        )
        with patch("bot.application.local_now", return_value=self.moment):
            await summary_callback(SimpleNamespace(callback_query=query), self.context)
        query.edit_message_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
