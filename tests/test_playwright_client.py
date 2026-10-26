import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import yaml

from bilancio.client import (
    PortalExpense,
    PortalOption,
    PortalSubmissionResult,
    TransactionDirection,
)
from bilancio.playwright_client import PlaywrightBilancioClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PlaywrightBilancioClientTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, root: Path) -> PlaywrightBilancioClient:
        return PlaywrightBilancioClient(
            base_url="https://bilancio.agesci.it/login",
            username="user-placeholder",
            password="password-placeholder",
            selectors_path=PROJECT_ROOT / "bilancio" / "selectors.yaml",
            session_state_path=root / "session.json",
            debug_path=root / "debug",
        )

    async def test_live_submission_uses_a_single_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            expense = PortalExpense(
                transaction_date=date(2026, 10, 26),
                description="Test receipt",
                amount=Decimal("1.00"),
                direction=TransactionDirection.EXPENSE,
                account=PortalOption("account", "Cassa di prova"),
                transaction_type=PortalOption("type", "Tipologia di prova"),
                unit=PortalOption("unit", "Unità di prova"),
                category=None,
                notes=None,
            )

            client._with_page = AsyncMock(
                return_value=PortalSubmissionResult(verified=True, dry_run=False)
            )

            result = await client.create_expense(expense, dry_run=False)

            self.assertTrue(result.verified)
            self.assertFalse(result.dry_run)
            client._with_page.assert_awaited_once()
            self.assertEqual(
                client._with_page.await_args.kwargs["max_attempts"],
                1,
            )

    def test_selector_file_uses_observed_fields_and_dynamic_options(self) -> None:
        configuration = yaml.safe_load(
            (PROJECT_ROOT / "bilancio" / "selectors.yaml").read_text(
                encoding="utf-8"
            )
        )

        selectors = configuration["selectors"]
        self.assertEqual(selectors["date"]["name"], "Data Operazione")
        self.assertEqual(
            selectors["scout_year_switch"]["name_pattern"],
            r'^Espandi "\d{2}-\d{2}"$',
        )
        self.assertEqual(
            selectors["accounting_voice"]["name_template"],
            "{accounting_voice}",
        )
        self.assertEqual(selectors["unit"]["name"], "Unità")
        self.assertEqual(
            selectors["transaction_type"]["name_template"],
            "Tipologia {direction_label}",
        )
        self.assertEqual(
            selectors["amount"]["name_pattern"],
            r"^Importo\b",
        )
        self.assertIn("Cassa", configuration["account_name_pattern"])
        self.assertIn("Conto", configuration["account_name_pattern"])


if __name__ == "__main__":
    unittest.main()
