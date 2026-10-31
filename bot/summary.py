from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from bilancio.client import AccountLedger, LedgerEntry
from bot import messages as texts

MONTH_NAMES = texts.MONTH_NAMES
_MAX_MESSAGE_LENGTH = 3800
_LATEST_OPERATIONS = 3


def scout_year_start(day: date) -> int:
    return day.year if day.month >= 10 else day.year - 1


def scout_year_name(start_year: int) -> str:
    return f"{start_year % 100:02d}-{(start_year + 1) % 100:02d}"


def reference_date_for(start_year: int, today: date) -> date:
    """Return a date inside the scout year, used to select it in the portal."""
    if start_year == scout_year_start(today):
        return today
    return date(start_year + 1, 10, 1) - timedelta(days=1)


def format_euro(amount: Decimal, *, signed: bool = False) -> str:
    return texts.format_euro(amount, signed=signed)


def _short(value: str, limit: int = 40) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else texts.ELLIPSIS.format(text=text[: limit - 1])


def _monthly_trend(ledger: AccountLedger, movements: list[LedgerEntry]) -> list[str]:
    totals: dict[tuple[int, int], tuple[Decimal, Decimal]] = {}
    for entry in movements:
        key = (entry.transaction_date.year, entry.transaction_date.month)
        income, expense = totals.get(key, (Decimal("0"), Decimal("0")))
        totals[key] = (income + entry.income, expense + entry.expense)
    year, month = min(totals)
    last = max(totals)
    balance = ledger.opening_balance
    lines: list[str] = []
    while (year, month) <= last:
        income, expense = totals.get((year, month), (Decimal("0"), Decimal("0")))
        balance += income - expense
        lines.append(
            texts.MONTHLY_ROW.format(
                month=MONTH_NAMES[month - 1],
                year=year,
                income=format_euro(income),
                expense=format_euro(expense),
                balance=format_euro(balance),
            )
        )
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return lines


def _format_account(ledger: AccountLedger, *, details: bool) -> str:
    lines = [
        texts.ACCOUNT_HEADER.format(account=ledger.account.label),
        texts.ACCOUNT_BALANCE.format(balance=format_euro(ledger.balance)),
        texts.ACCOUNT_TOTALS.format(
            income=format_euro(ledger.total_income), expense=format_euro(ledger.total_expense)
        ),
    ]
    if details or not ledger.entries_available:
        lines.append(texts.OPENING_BALANCE.format(amount=format_euro(ledger.opening_balance)))
    if not ledger.entries_available:
        return "\n".join(lines)
    movements = sorted(
        (entry for entry in ledger.entries if not entry.is_opening_balance),
        key=lambda entry: entry.transaction_date,
    )
    if not ledger.complete:
        lines.append(
            texts.PARTIAL_LEDGER.format(visible=len(ledger.entries), total=ledger.total_rows)
        )
    if not movements:
        lines.append(texts.NO_MOVEMENTS)
        return "\n".join(lines)
    if details:
        lines.append(texts.MONTHLY_HEADER)
        lines.extend(_monthly_trend(ledger, movements))
        lines.append(texts.OPERATIONS_HEADER)
        for entry in reversed(movements[-_LATEST_OPERATIONS:]):
            lines.append(
                texts.LEDGER_ROW.format(
                    day=entry.transaction_date,
                    description=_short(entry.description),
                    amount=format_euro(entry.income - entry.expense, signed=True),
                )
            )
    else:
        entry = movements[-1]
        lines.append(
            texts.LAST_MOVEMENT.format(
                day=entry.transaction_date,
                description=_short(entry.description),
                amount=format_euro(entry.income - entry.expense, signed=True),
            )
        )
    return "\n".join(lines)


def format_ledger_summary(
    ledgers: Sequence[AccountLedger],
    start_year: int,
    *,
    details: bool = False,
) -> list[str]:
    """Return compact balances, or the requested monthly and operation details."""
    header = texts.SUMMARY_HEADER.format(year=scout_year_name(start_year))
    if not ledgers:
        return [texts.NO_ACCOUNTS.format(header=header)]
    messages: list[str] = []
    current = header
    for ledger in ledgers:
        block = _format_account(ledger, details=details)
        if len(current) + len(block) + 2 > _MAX_MESSAGE_LENGTH:
            messages.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}"
    messages.append(current)
    return messages
