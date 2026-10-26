import unittest
from datetime import date
from decimal import Decimal

from bilancio.client import (
    PortalExpense,
    PortalOption,
    TransactionDirection,
)


class BilancioClientModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.account = PortalOption("cassa-co-ca", "Cassa Co.ca")
        self.transaction_type = PortalOption("type-a1", "A.1 Quota")
        self.unit = PortalOption("unit-lc", "Cacce LC")

    def test_builds_valid_portal_expense(self) -> None:
        expense = PortalExpense(
            transaction_date=date(2026, 10, 26),
            description="Receipt",
            amount=Decimal("12.50"),
            direction=TransactionDirection.EXPENSE,
            account=self.account,
            transaction_type=self.transaction_type,
            unit=self.unit,
            category=None,
            notes=None,
        )

        self.assertEqual(expense.account.label, "Cassa Co.ca")
        self.assertEqual(expense.direction.value, "uscita")

    def test_rejects_empty_portal_option(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            PortalOption("", "Cassa Co.ca")

    def test_rejects_empty_description(self) -> None:
        with self.assertRaisesRegex(ValueError, "description"):
            PortalExpense(
                transaction_date=date(2026, 10, 26),
                description=" ",
                amount=Decimal("12.50"),
                direction=TransactionDirection.INCOME,
                account=self.account,
                transaction_type=self.transaction_type,
                unit=self.unit,
                category=None,
                notes=None,
            )

    def test_rejects_non_positive_amount(self) -> None:
        with self.assertRaisesRegex(ValueError, "amount"):
            PortalExpense(
                transaction_date=date(2026, 10, 26),
                description="Receipt",
                amount=Decimal("0"),
                direction=TransactionDirection.EXPENSE,
                account=self.account,
                transaction_type=self.transaction_type,
                unit=self.unit,
                category=None,
                notes=None,
            )


if __name__ == "__main__":
    unittest.main()
