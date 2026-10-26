import csv
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from bilancio.client import (
    PortalOption,
    TransactionDirection,
)
from bilancio.csv_import import (
    CsvImportError,
    prepare_portal_expenses,
    read_expense_csv,
    resolve_portal_option,
)


class CsvExpenseParsingTests(unittest.TestCase):
    def _write_csv(self, rows: list[dict[str, str]]) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "expenses.csv"
        fields = (
            "data",
            "nota",
            "tipo",
            "importo",
            "cassa",
            "voce",
            "tipologia",
            "unita",
            "categoria",
        )
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        return path

    def _row(self, **overrides: str) -> dict[str, str]:
        row = {
            "data": "01/10/2026",
            "nota": "Prova senza dati reali",
            "tipo": "uscita",
            "importo": "-12,50",
            "cassa": "Cassa Co.ca",
            "voce": "Voci Scout",
            "tipologia": "A.1",
            "unita": "Cacce LC",
            "categoria": "",
        }
        row.update(overrides)
        return row

    def test_parses_locale_amount_and_optional_category(self) -> None:
        path = self._write_csv(
            [
                self._row(),
                self._row(
                    data="02/10/2026",
                    nota="Entrata di prova",
                    tipo="entrata",
                    importo="15,00",
                ),
            ]
        )

        entries = read_expense_csv(path)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].transaction_date, date(2026, 10, 1))
        self.assertEqual(entries[0].amount, Decimal("12.50"))
        self.assertIs(entries[0].direction, TransactionDirection.EXPENSE)
        self.assertIsNone(entries[0].category_reference)
        self.assertEqual(entries[1].amount, Decimal("15.00"))
        self.assertIs(entries[1].direction, TransactionDirection.INCOME)

    def test_rejects_missing_description(self) -> None:
        path = self._write_csv([self._row(nota="")])

        with self.assertRaisesRegex(CsvImportError, "non ha una descrizione"):
            read_expense_csv(path)

    def test_rejects_amount_sign_inconsistent_with_direction(self) -> None:
        path = self._write_csv([self._row(importo="12,50")])

        with self.assertRaisesRegex(CsvImportError, "coerente"):
            read_expense_csv(path)

    def test_matches_unique_type_code_prefix(self) -> None:
        selected = resolve_portal_option(
            "A.1",
            [
                PortalOption("a1", "A.1 Quota"),
                PortalOption("a10", "A.10 Materiale"),
            ],
            field_name="tipologia",
            row_number=2,
        )

        self.assertEqual(selected.label, "A.1 Quota")

    def test_resolves_unique_code_with_a_description_suffix(self) -> None:
        selected = resolve_portal_option(
            "A.2 Quota attività",
            [
                PortalOption("a2", "A.2 Quote per attività scout"),
                PortalOption("a10", "A.10 Materiale"),
            ],
            field_name="tipologia",
            row_number=2,
        )

        self.assertEqual(selected.label, "A.2 Quote per attività scout")

    def test_rejects_ambiguous_type_code_prefix(self) -> None:
        with self.assertRaisesRegex(CsvImportError, "codice ambiguo"):
            resolve_portal_option(
                "A.1 Quota",
                [
                    PortalOption("a1-1", "A.1 Quota ordinaria"),
                    PortalOption("a1-2", "A.1 Quota straordinaria"),
                ],
                field_name="tipologia",
                row_number=2,
            )


class FakeBilancioClient:
    def __init__(self) -> None:
        self.account_calls = 0
        self.type_calls = 0
        self.unit_calls = 0
        self.category_calls = 0

    async def list_accounts(
        self,
        *,
        transaction_date: date | None = None,
    ) -> Sequence[PortalOption]:
        self.account_calls += 1
        return [PortalOption("account-id", "Cassa Co.ca")]

    async def list_transaction_types(
        self,
        account: PortalOption,
        direction: TransactionDirection,
        *,
        transaction_date: date | None = None,
        accounting_voice: str = "Voci Scout",
    ) -> Sequence[PortalOption]:
        self.type_calls += 1
        return [PortalOption("type-a1", "A.1 Quota")]

    async def list_units(
        self,
        account: PortalOption,
        direction: TransactionDirection,
        transaction_type: PortalOption,
        *,
        transaction_date: date | None = None,
        accounting_voice: str = "Voci Scout",
    ) -> Sequence[PortalOption]:
        self.unit_calls += 1
        return [PortalOption("unit-lc", "Cacce LC")]

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
        self.category_calls += 1
        return [PortalOption("category-id", "Categoria prova")]

    async def create_expense(self, expense, *, dry_run: bool):
        raise AssertionError("The preparation test must not submit expenses")


class CsvExpensePreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_dynamic_portal_choices_without_submission(self) -> None:
        client = FakeBilancioClient()
        from bilancio.csv_import import CsvExpenseRow

        rows = [
            CsvExpenseRow(
                row_number=2,
                transaction_date=date(2026, 10, 1),
                description="Prova senza dati reali",
                signed_amount=Decimal("-12.50"),
                direction=TransactionDirection.EXPENSE,
                account_reference="Cassa Co.ca",
                accounting_voice="Voci Scout",
                transaction_type_reference="A.1",
                unit_reference="Cacce LC",
                category_reference=None,
            ),
            CsvExpenseRow(
                row_number=3,
                transaction_date=date(2026, 10, 2),
                description="Entrata di prova",
                signed_amount=Decimal("15.00"),
                direction=TransactionDirection.INCOME,
                account_reference="Cassa Co.ca",
                accounting_voice="Voci Scout",
                transaction_type_reference="A.1",
                unit_reference="Cacce LC",
                category_reference=None,
            ),
        ]

        expenses = await prepare_portal_expenses(rows, client)

        self.assertEqual(len(expenses), 2)
        self.assertEqual(expenses[0].amount, Decimal("12.50"))
        self.assertEqual(expenses[1].amount, Decimal("15.00"))
        self.assertEqual(expenses[0].account.label, "Cassa Co.ca")
        self.assertIsNone(expenses[0].category)
        self.assertEqual(client.account_calls, 1)
        self.assertEqual(client.type_calls, 2)
        self.assertEqual(client.unit_calls, 2)
        self.assertEqual(client.category_calls, 0)


if __name__ == "__main__":
    unittest.main()
