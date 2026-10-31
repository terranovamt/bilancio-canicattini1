from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from bilancio.client import AccountLedger, PortalOption


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    start_year: int
    read_at: datetime
    ledgers: tuple[AccountLedger, ...]


def compact_ledgers(ledgers: Sequence[AccountLedger]) -> tuple[AccountLedger, ...]:
    """Validate portal totals and discard all transaction details before persistence."""
    if not ledgers:
        raise ValueError("Portal returned no accounts")
    result: list[AccountLedger] = []
    values: set[str] = set()
    labels: set[str] = set()
    for ledger in ledgers:
        value = ledger.account.value
        label = ledger.account.label
        if value in values or label.casefold() in labels:
            raise ValueError("Portal returned duplicate accounts")
        values.add(value)
        labels.add(label.casefold())
        amounts = (
            ledger.opening_balance, ledger.total_income, ledger.total_expense, ledger.balance
        )
        if any(not isinstance(amount, Decimal) or not amount.is_finite() for amount in amounts):
            raise ValueError("Portal returned invalid totals")
        if ledger.total_income < 0 or ledger.total_expense < 0:
            raise ValueError("Portal returned negative movement totals")
        if ledger.balance != ledger.opening_balance + ledger.total_income - ledger.total_expense:
            raise ValueError("Portal returned inconsistent totals")
        # Partial entries are acceptable for Details, but a partial compact read is not.
        if not ledger.entries_available and not ledger.complete:
            raise ValueError("Portal returned partial balances")
        result.append(AccountLedger(
            PortalOption(value, label), *amounts, entries=(), total_rows=0,
            complete=True, entries_available=False,
        ))
    return tuple(result)
