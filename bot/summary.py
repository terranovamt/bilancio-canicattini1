from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from bilancio.client import AccountLedger, LedgerEntry

MONTH_NAMES = (
    "Gennaio",
    "Febbraio",
    "Marzo",
    "Aprile",
    "Maggio",
    "Giugno",
    "Luglio",
    "Agosto",
    "Settembre",
    "Ottobre",
    "Novembre",
    "Dicembre",
)
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
    sign = "-" if amount < 0 else ("+" if signed and amount > 0 else "")
    digits = f"{abs(amount):,.2f}".replace(",", " ").replace(".", ",")
    return f"{sign}{digits.replace(' ', '.')} €"


def _short(value: str, limit: int = 40) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


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
            f"• {MONTH_NAMES[month - 1]} {year}: entrate {format_euro(income)} · "
            f"uscite {format_euro(expense)} → saldo {format_euro(balance)}"
        )
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return lines


def _format_account(ledger: AccountLedger) -> str:
    lines = [
        f"🏦 {ledger.account.label}",
        f"💰 Saldo attuale: {format_euro(ledger.balance)}",
        (
            f"Saldo iniziale {format_euro(ledger.opening_balance)} · "
            f"entrate {format_euro(ledger.total_income)} · "
            f"uscite {format_euro(ledger.total_expense)}"
        ),
    ]
    movements = sorted(
        (entry for entry in ledger.entries if not entry.is_opening_balance),
        key=lambda entry: entry.transaction_date,
    )
    if not ledger.complete:
        lines.append(
            f"⚠️ Il portale mostra {len(ledger.entries)} righe su "
            f"{ledger.total_rows}: andamento e ultime operazioni sono parziali."
        )
    if not movements:
        lines.append("Nessuna operazione dall’inizio dell’anno.")
        return "\n".join(lines)
    lines.append("📈 Andamento mensile")
    lines.extend(_monthly_trend(ledger, movements))
    lines.append("🧾 Ultime operazioni")
    for entry in reversed(movements[-_LATEST_OPERATIONS:]):
        amount = entry.income - entry.expense
        lines.append(
            f"• {entry.transaction_date:%d/%m} {_short(entry.description)}: "
            f"{format_euro(amount, signed=True)}"
        )
    return "\n".join(lines)


def format_ledger_summary(
    ledgers: Sequence[AccountLedger],
    start_year: int,
) -> list[str]:
    """Return Telegram-sized messages with balances, trend and latest operations."""
    header = (
        f"📊 Riepilogo bilancio {scout_year_name(start_year)}\n"
        "Dati letti ora dal portale AGESCI."
    )
    if not ledgers:
        return [f"{header}\n\nNessuna cassa trovata nel portale."]
    messages: list[str] = []
    current = header
    for ledger in ledgers:
        block = _format_account(ledger)
        if len(current) + len(block) + 2 > _MAX_MESSAGE_LENGTH:
            messages.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}"
    messages.append(current)
    return messages
