import asyncio
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import yaml
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from bilancio.client import (
    PortalExpense,
    PortalOption,
    PortalSubmissionResult,
    TransactionDirection,
)
from bilancio.playwright_client import (
    PlaywrightBilancioClient,
    _wait_for_authentication_marker,
    _wait_for_visible_locator,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeVisibilityLocator:
    def __init__(self, *, visible: bool, delay: float = 0.01) -> None:
        self.visible = visible
        self.delay = delay

    @property
    def first(self) -> FakeVisibilityLocator:
        return self

    async def wait_for(self, *, state: str, timeout: int) -> None:
        await asyncio.sleep(self.delay)
        if not self.visible:
            raise PlaywrightTimeoutError("Element did not become visible")


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

    async def test_waits_for_delayed_login_form_or_scout_year_menu(self) -> None:
        login_state = await _wait_for_authentication_marker(
            FakeVisibilityLocator(visible=True, delay=0.01),
            FakeVisibilityLocator(visible=False, delay=0.2),
            timeout_ms=1000,
        )
        authenticated_state = await _wait_for_authentication_marker(
            FakeVisibilityLocator(visible=False, delay=0.2),
            FakeVisibilityLocator(visible=True, delay=0.01),
            timeout_ms=1000,
        )

        self.assertIs(login_state, True)
        self.assertIs(authenticated_state, False)

    async def test_waits_for_delayed_scout_year_option(self) -> None:
        visible = await _wait_for_visible_locator(
            FakeVisibilityLocator(visible=True, delay=0.05),
            timeout_ms=1000,
        )

        self.assertTrue(visible)

    async def test_returns_none_when_portal_shows_neither_marker(self) -> None:
        state = await _wait_for_authentication_marker(
            FakeVisibilityLocator(visible=False),
            FakeVisibilityLocator(visible=False),
            timeout_ms=1000,
        )

        self.assertIsNone(state)

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
            r'^(?:Espandi|Comprimi) "\d{2}-\d{2}"$',
        )
        self.assertEqual(
            selectors["scout_year_option"]["text_template"],
            "{year_label}",
        )
        self.assertEqual(
            selectors["scout_year_selected"]["name_template"],
            'Espandi "{year_label}"',
        )
        self.assertTrue(
            selectors["role_switch"]["name_pattern"].startswith(
                "^(?:Espandi|Comprimi)"
            )
        )
        self.assertTrue(
            selectors["treasurer_role_selected"]["name_pattern"].startswith(
                '^Espandi "Tesoriere di gruppo -'
            )
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
