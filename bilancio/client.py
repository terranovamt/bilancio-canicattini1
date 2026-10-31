from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Protocol


class TransactionDirection(str, Enum):
    INCOME = "entrata"
    EXPENSE = "uscita"


ACCOUNTING_VOICE_OPTIONS = ("Voci Scout", "Voci Ministero")


def scout_year_label(transaction_date: date) -> str:
    start_year = transaction_date.year if transaction_date.month >= 10 else transaction_date.year - 1
    return f"{start_year % 100:02d}-{(start_year + 1) % 100:02d}"


class BilancioClientError(RuntimeError):
    """Raised when the budget portal cannot complete an operation."""

    def __init__(
        self,
        message: str,
        *,
        diagnostic_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostic_path = diagnostic_path


class PortalOptionsUnavailable(BilancioClientError):
    """Raised when the portal does not expose options for the current form."""


@dataclass(frozen=True, slots=True)
class PortalOption:
    value: str
    label: str

    def __post_init__(self) -> None:
        if not self.value.strip() or not self.label.strip():
            raise ValueError("Portal option value and label must not be empty")


_DEFAULT_ACCOUNT_UNITS: dict[str, PortalOption] = {
    "cassa co.ca": PortalOption("Gruppo G1", "Gruppo G1"),
    "conto co.ca": PortalOption("Gruppo G1", "Gruppo G1"),
    "cassa lc": PortalOption("L1 - ROCCIA DELLA PACE", "L1 - ROCCIA DELLA PACE"),
    "cassa eg": PortalOption("O1 - CASSARINO - CIARCIA", "O1 - CASSARINO - CIARCIA"),
    "cassa rs": PortalOption("T1 - FALCHI PELLEGRINI", "T1 - FALCHI PELLEGRINI"),
}


def default_unit_for_account(account: PortalOption) -> PortalOption | None:
    return _DEFAULT_ACCOUNT_UNITS.get(account.label.strip().casefold())


@dataclass(frozen=True, slots=True)
class PortalExpense:
    transaction_date: date
    description: str
    amount: Decimal
    direction: TransactionDirection
    account: PortalOption
    transaction_type: PortalOption | None
    unit: PortalOption | None
    category: PortalOption | None
    notes: str | None
    accounting_voice: str = "Voci Scout"

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("description must not be empty")
        if not self.amount.is_finite() or self.amount <= 0:
            raise ValueError("amount must be a finite positive value")
        if self.accounting_voice not in ACCOUNTING_VOICE_OPTIONS:
            raise ValueError("accounting_voice must be a supported AGESCI voice")


@dataclass(frozen=True, slots=True)
class PortalSubmissionResult:
    verified: bool
    confirmation_id: str | None = None
    dry_run: bool = False
    unit_left_empty: bool = False


OPENING_BALANCE_DESCRIPTION = "Saldo Iniziale Calcolato"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    transaction_date: date
    description: str
    income: Decimal
    expense: Decimal
    transaction_type: str = ""
    unit: str = ""

    @property
    def is_opening_balance(self) -> bool:
        return self.description == OPENING_BALANCE_DESCRIPTION


@dataclass(frozen=True, slots=True)
class AccountLedger:
    """Read-only snapshot of one account ledger for a scout year."""

    account: PortalOption
    opening_balance: Decimal
    total_income: Decimal
    total_expense: Decimal
    balance: Decimal
    entries: tuple[LedgerEntry, ...]
    total_rows: int
    complete: bool
    entries_available: bool = True


class BilancioClient(Protocol):
    async def clear_options_cache(self) -> None:
        """Invalidate cached portal choices without submitting operations."""

    async def health_check(self) -> None:
        """Log in if needed and open the form without saving an expense."""

    async def list_accounts(
        self,
        *,
        transaction_date: date | None = None,
    ) -> Sequence[PortalOption]:
        """Return account choices currently available to the logged-in user."""

    async def list_transaction_types(
        self,
        account: PortalOption,
        direction: TransactionDirection,
        *,
        transaction_date: date | None = None,
        accounting_voice: str = "Voci Scout",
    ) -> Sequence[PortalOption]:
        """Return types available for the selected account and direction."""

    async def list_units(
        self,
        account: PortalOption,
        direction: TransactionDirection,
        transaction_type: PortalOption,
        *,
        transaction_date: date | None = None,
        accounting_voice: str = "Voci Scout",
    ) -> Sequence[PortalOption]:
        """Return units available for the current form selections."""

    async def list_categories(
        self,
        account: PortalOption,
        direction: TransactionDirection,
        transaction_type: PortalOption,
        unit: PortalOption,
        *,
        transaction_date: date | None = None,
        accounting_voice: str = "Voci Scout",
    ) -> Sequence[PortalOption]:
        """Return categories available for the current form selections."""

    async def read_ledgers(
        self,
        *,
        transaction_date: date,
        include_entries: bool = True,
    ) -> Sequence[AccountLedger]:
        """Read balances and operations of every account without changing data."""

    async def create_expense(
        self,
        expense: PortalExpense,
        *,
        dry_run: bool,
    ) -> PortalSubmissionResult:
        """Submit and verify an expense; never save when dry_run is true."""
