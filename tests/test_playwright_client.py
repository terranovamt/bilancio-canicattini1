import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from bilancio.client import (
    BilancioClientError,
    PortalExpense,
    PortalOption,
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

    async def test_live_submission_is_blocked_before_browser_use(self) -> None:
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

            with self.assertRaisesRegex(BilancioClientError, "remains disabled"):
                await client.create_expense(expense, dry_run=False)

    def test_selector_file_uses_observed_fields_and_dynamic_options(self) -> None:
        configuration = yaml.safe_load(
            (PROJECT_ROOT / "bilancio" / "selectors.yaml").read_text(
                encoding="utf-8"
            )
        )

        selectors = configuration["selectors"]
        self.assertEqual(selectors["date"]["name"], "Data Operazione")
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
