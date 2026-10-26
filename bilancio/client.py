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


@dataclass(frozen=True, slots=True)
class PortalExpense:
    transaction_date: date
    description: str
    amount: Decimal
    direction: TransactionDirection
    account: PortalOption
    transaction_type: PortalOption
    unit: PortalOption
    category: PortalOption | None
    notes: str | None

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("description must not be empty")
        if not self.amount.is_finite() or self.amount <= 0:
            raise ValueError("amount must be a finite positive value")


@dataclass(frozen=True, slots=True)
class PortalSubmissionResult:
    verified: bool
    confirmation_id: str | None = None
    dry_run: bool = False


class BilancioClient(Protocol):
    async def health_check(self) -> None:
        """Log in if needed and open the form without saving an expense."""

    async def list_accounts(self) -> Sequence[PortalOption]:
        """Return account choices currently available to the logged-in user."""

    async def list_transaction_types(
        self,
        account: PortalOption,
        direction: TransactionDirection,
    ) -> Sequence[PortalOption]:
        """Return types available for the selected account and direction."""

    async def list_units(
        self,
        account: PortalOption,
        direction: TransactionDirection,
        transaction_type: PortalOption,
    ) -> Sequence[PortalOption]:
        """Return units available for the current form selections."""

    async def list_categories(
        self,
        account: PortalOption,
        direction: TransactionDirection,
        transaction_type: PortalOption,
        unit: PortalOption,
    ) -> Sequence[PortalOption]:
        """Return categories available for the current form selections."""

    async def create_expense(
        self,
        expense: PortalExpense,
        *,
        dry_run: bool,
    ) -> PortalSubmissionResult:
        """Submit and verify an expense; never save when dry_run is true."""
