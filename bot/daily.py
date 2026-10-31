from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bilancio.client import BilancioClientError
from bot import messages as texts
from bot.summary import format_euro, scout_year_name, scout_year_start
from config import Settings
from db.database import Database, VerifiedMovement
from db.ledger_snapshot import compact_ledgers

logger = logging.getLogger(__name__)


def local_now(timezone_name: str) -> datetime:
    return datetime.now(timezone.utc).astimezone(ZoneInfo(timezone_name))


def previous_day_interval(moment: datetime) -> tuple[date, datetime, datetime]:
    """Use local calendar midnights, not a rolling 24-hour duration (DST aware)."""
    if moment.tzinfo is None:
        raise ValueError("Daily report requires an aware timestamp")
    day = moment.date() - timedelta(days=1)
    start = datetime.combine(day, time.min, tzinfo=moment.tzinfo)
    end = datetime.combine(moment.date(), time.min, tzinfo=moment.tzinfo)
    return day, start, end


def _totals(movements: Sequence[VerifiedMovement]) -> str:
    incomes = [movement.amount for movement in movements if movement.direction == "entrata"]
    expenses = [movement.amount for movement in movements if movement.direction == "uscita"]
    return texts.DAILY_TOTALS.format(
        count=len(movements), income_count=len(incomes), expense_count=len(expenses),
        income=format_euro(sum(incomes, Decimal("0"))),
        expense=format_euro(sum(expenses, Decimal("0"))),
    )


def format_daily_report(day: date, movements: Sequence[VerifiedMovement]) -> list[str]:
    blocks = [
        texts.DAILY_REPORT_HEADER.format(day=day),
        texts.DAILY_GLOBAL + "\n" + _totals(movements),
    ]
    if not movements:
        blocks.append(texts.DAILY_ZERO_MOVEMENTS)
    accounts: dict[str, list[VerifiedMovement]] = {}
    for movement in movements:
        accounts.setdefault(movement.account_value, []).append(movement)
    for key in sorted(accounts):
        group = accounts[key]
        blocks.append(texts.DAILY_ACCOUNT.format(account=group[0].account_label) + "\n" + _totals(group))
    result: list[str] = []
    current = ""
    for block in blocks:
        if current and len(current) + len(block) + 2 > 3800:
            result.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    if current:
        result.append(current)
    return result


async def daily_ledger_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    database: Database = context.application.bot_data["database"]
    moment = local_now(settings.timezone)
    start_year = scout_year_start(moment.date())
    day, start, end = previous_day_interval(moment)
    try:
        client = context.application.bot_data.get("bilancio_client")
        if client is None:
            raise BilancioClientError("Portal client unavailable")
        ledgers = compact_ledgers(await client.read_ledgers(
            transaction_date=moment.date(), include_entries=False,
        ))
        read_at = local_now(settings.timezone)
    except (BilancioClientError, ValueError, TypeError, OSError) as error:
        logger.warning("Daily ledger refresh failed: %s", type(error).__name__)
        status = texts.DAILY_READ_FAILED.format(year=scout_year_name(start_year))
    else:
        try:
            await asyncio.to_thread(database.save_ledger_snapshot, start_year, read_at, ledgers)
            status_template = texts.DAILY_READ_SUCCESS
        except (sqlite3.Error, OSError, ValueError) as error:
            logger.error("Could not persist daily ledger snapshot: %s", type(error).__name__)
            status_template = texts.DAILY_READ_CACHE_FAILED
        status = status_template.format(
            year=scout_year_name(start_year), day=read_at, clock=read_at,
            zone=settings.timezone,
        )
    try:
        movements = await asyncio.to_thread(database.list_verified_movements, start, end)
        report = format_daily_report(day, movements)
    except (sqlite3.Error, ValueError) as error:
        logger.error("Daily movement report failed: %s", type(error).__name__)
        report = [texts.DAILY_REPORT_FAILED]
    try:
        # Deliberately never use allowed_user_ids or a group/chat from a receipt.
        for text in [status, *report]:
            await context.bot.send_message(chat_id=settings.admin_user_id, text=text)
    except TelegramError:
        logger.error("Could not send daily admin report")
