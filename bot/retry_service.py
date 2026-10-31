from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

from telegram.error import TelegramError

from bilancio.client import (
    BilancioClient,
    BilancioClientError,
    PortalExpense,
    PortalOption,
    TransactionDirection,
)
from bot import messages as texts
from config import Settings
from db.database import Database, RetryEntry, StoredExpense
from storage.archive import FileArchive
from storage.pending import PendingReceiptStore, has_receipt_file

logger = logging.getLogger(__name__)
MAX_QUEUE_ATTEMPTS = 3
_BACKOFF_MINUTES = (5, 15, 60)


class RetryBot(Protocol):
    async def send_message(self, *, chat_id: int, text: str) -> object:
        """Send a user-facing message."""


@dataclass(frozen=True, slots=True)
class RetrySummary:
    succeeded: int
    pending: int
    failed: int
    trials: int = 0


class ExpenseRetryService:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        bilancio_client: BilancioClient,
        archive: FileArchive,
        pending_store: PendingReceiptStore,
    ) -> None:
        self._settings = settings
        self._database = database
        self._bilancio = bilancio_client
        self._archive = archive
        self._pending_store = pending_store
        self._lock = asyncio.Lock()

    async def process_due(
        self,
        bot: RetryBot,
        *,
        telegram_user_id: int | None = None,
    ) -> RetrySummary:
        async with self._lock:
            before = datetime.now(timezone.utc).isoformat(timespec="seconds")
            entries = await asyncio.to_thread(
                self._database.get_due_retries,
                before,
                telegram_user_id=telegram_user_id,
            )
            succeeded = pending = failed = trials = 0
            for entry in entries:
                outcome = await self._process_one(entry, bot)
                if outcome in {"succeeded", "trial"}:
                    succeeded += 1
                    if outcome == "trial":
                        trials += 1
                elif outcome == "pending":
                    pending += 1
                else:
                    failed += 1
            return RetrySummary(succeeded, pending, failed, trials)

    async def _process_one(
        self,
        entry: RetryEntry,
        bot: RetryBot,
    ) -> str:
        stored = await asyncio.to_thread(
            self._database.get_expense,
            entry.expense_id,
        )
        if stored is None or stored.status != "queued":
            await asyncio.to_thread(
                self._database.remove_retry,
                entry.expense_id,
            )
            pending_path = stored.payload.get("pending_path")
            if isinstance(pending_path, str):
                try:
                    await asyncio.to_thread(self._pending_store.delete, pending_path)
                except (OSError, ValueError):
                    logger.warning("Could not remove temporary receipt after retry")
            return "failed"

        try:
            portal_expense = self._portal_expense(stored)
            if (
                portal_expense.direction == TransactionDirection.EXPENSE
                and not await asyncio.to_thread(
                    has_receipt_file, stored.payload, stored.archive_path
                )
            ):
                logger.warning("Queued expense rejected: receipt file is missing")
                await self._mark_failed(entry.expense_id)
                await self._notify_user(
                    bot, stored.telegram_user_id, texts.EXPENSE_RECEIPT_REQUIRED
                )
                return "failed"
            if (
                stored.archive_path is None
                and stored.payload.get("manual_entry") is not True
            ):
                archived = await asyncio.to_thread(
                    self._archive.archive,
                    self._pending_path(stored),
                    transaction_date=portal_expense.transaction_date,
                    merchant=portal_expense.description,
                    account=portal_expense.account.label,
                    amount=portal_expense.amount,
                    save_corrected=True,
                )
                if archived.correction_failed:
                    await self._notify_admin(bot, texts.CORRECTED_COPY_FAILED)
                await asyncio.to_thread(
                    self._database.update_expense_status,
                    entry.expense_id,
                    "queued",
                    str(archived.path),
                )

            if "dry_run" not in stored.payload:
                logger.warning("Queued expense has no stored mode; using test mode")
            dry_run = stored.payload.get("dry_run", True)
            if not isinstance(dry_run, bool):
                raise ValueError("Expense mode must be a boolean")
            result = await self._bilancio.create_expense(
                portal_expense,
                dry_run=dry_run,
            )
            if not result.verified:
                raise BilancioClientError("Portal result could not be verified")
            if dry_run and not result.dry_run:
                await self._mark_failed(entry.expense_id)
                await self._notify_admin(
                    bot,
                    texts.ADMIN_TRIAL_UNCONFIRMED,
                )
                return "failed"
        except (
            BilancioClientError,
            KeyError,
            OSError,
            ValueError,
            sqlite3.Error,
        ) as error:
            logger.error(
                "Queued expense retry failed: %s",
                type(error).__name__,
            )
            if entry.attempts >= MAX_QUEUE_ATTEMPTS:
                await self._mark_failed(entry.expense_id)
                await self._notify_user(
                    bot,
                    stored.telegram_user_id,
                    texts.RETRY_EXHAUSTED.format(
                        description=texts.safe_text(stored.payload.get("merchant")),
                        amount=texts.amount_text(stored.payload),
                    ),
                )
                await self._notify_admin(
                    bot,
                    texts.ADMIN_RETRY_EXHAUSTED,
                )
                return "failed"

            delay = _BACKOFF_MINUTES[min(entry.attempts - 1, len(_BACKOFF_MINUTES) - 1)]
            next_attempt = (
                datetime.now(timezone.utc) + timedelta(minutes=delay)
            ).isoformat(timespec="seconds")
            await asyncio.to_thread(
                self._database.enqueue_retry,
                entry.expense_id,
                next_attempt_at=next_attempt,
                error_code="portal_retry_failed",
            )
            return "pending"

        await asyncio.to_thread(
            self._database.record_verified_submission,
            entry.expense_id,
            portal_expense,
            result,
        )
        await asyncio.to_thread(
            self._database.remove_retry,
            entry.expense_id,
        )
        await self._notify_user(
            bot,
            stored.telegram_user_id,
            (
                texts.RETRY_TRIAL
                if result.dry_run
                else texts.RETRY_SAVED.format(
                    description=texts.safe_text(stored.payload.get("merchant")),
                    amount=texts.amount_text(stored.payload),
                    day=texts.date_text(stored.payload.get("date"), short=True),
                )
            ),
        )
        return "trial" if result.dry_run else "succeeded"

    async def retry_user_now(
        self,
        user_id: int,
        bot: RetryBot,
    ) -> RetrySummary:
        await asyncio.to_thread(
            self._database.request_retry_for_user,
            user_id,
        )
        return await self.process_due(bot, telegram_user_id=user_id)

    async def _mark_failed(self, expense_id: str) -> None:
        await asyncio.to_thread(
            self._database.update_expense_status,
            expense_id,
            "failed",
        )
        await asyncio.to_thread(
            self._database.remove_retry,
            expense_id,
        )

    async def _notify_user(
        self,
        bot: RetryBot,
        user_id: int,
        text: str,
    ) -> None:
        try:
            await bot.send_message(chat_id=user_id, text=text)
        except TelegramError:
            logger.error("Could not notify Telegram user about retry result")

    async def _notify_admin(self, bot: RetryBot, text: str) -> None:
        try:
            await bot.send_message(
                chat_id=self._settings.admin_user_id,
                text=text,
            )
        except TelegramError:
            logger.error("Could not send Telegram admin retry notification")

    @staticmethod
    def _portal_expense(stored: StoredExpense) -> PortalExpense:
        payload = stored.payload
        fields = {
            key: payload[key]
            for key in (
                "date",
                "merchant",
                "total_amount",
                "direction",
            )
            if key in payload
        }
        if not all(key in fields for key in ("date", "merchant", "total_amount", "direction")):
            raise ValueError("Queued receipt data is incomplete")
        if not isinstance(fields["date"], str):
            raise ValueError("Queued receipt date is invalid")
        account = ExpenseRetryService._stored_option(payload, "account")
        transaction_type = ExpenseRetryService._stored_option(
            payload,
            "transaction_type",
        )
        unit_value = payload.get("unit")
        if unit_value is None:
            unit = None
        elif isinstance(unit_value, dict):
            unit = PortalOption(**unit_value)
        else:
            raise ValueError("Queued portal unit is invalid")
        category_value = payload.get("category")
        category = (
            PortalOption(**category_value)
            if isinstance(category_value, dict)
            else None
        )
        direction_value = fields["direction"]
        if not isinstance(direction_value, str):
            raise ValueError("Queued transaction direction is invalid")
        merchant = fields["merchant"]
        try:
            amount = Decimal(str(fields["total_amount"]))
        except InvalidOperation as error:
            raise ValueError("Queued receipt amount is invalid") from error
        if not isinstance(merchant, str):
            raise ValueError("Queued merchant is invalid")
        notes = payload.get("portal_notes")
        return PortalExpense(
            transaction_date=datetime.fromisoformat(fields["date"]).date(),
            description=merchant,
            amount=amount,
            direction=TransactionDirection(direction_value),
            account=account,
            transaction_type=transaction_type,
            unit=unit,
            category=category,
            notes=notes if isinstance(notes, str) else None,
            accounting_voice=str(
                payload.get("accounting_voice", "Voci Scout")
            ),
        )

    @staticmethod
    def _stored_option(payload: dict[str, object], key: str) -> PortalOption:
        value = payload.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"Queued portal selection is missing: {key}")
        return PortalOption(**value)

    @staticmethod
    def _pending_path(stored: StoredExpense) -> Path:
        path = stored.payload.get("pending_path")
        if not isinstance(path, str):
            raise ValueError("Queued receipt file is missing")
        return Path(path)
