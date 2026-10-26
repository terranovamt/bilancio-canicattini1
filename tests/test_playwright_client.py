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
    _normalize_amount_input,
    _normalize_ledger_header,
    _parse_ledger_page_range,
    _wait_for_authentication_marker,
    _wait_for_input_value,
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


class FakeDelayedInputLocator:
    def __init__(self, *, value: str, delay: float) -> None:
        self.value = value
        self.delay = delay
        self.started_at: float | None = None

    async def input_value(self) -> str:
        loop = asyncio.get_running_loop()
        if self.started_at is None:
            self.started_at = loop.time()
        if loop.time() - self.started_at >= self.delay:
            return self.value
        return "pending"


class FakeScoutYearSwitch:
    def __init__(self) -> None:
        self.expanded = "true"
        self.click_count = 0

    @property
    def first(self) -> FakeScoutYearSwitch:
        return self

    async def inner_text(self) -> str:
        return "26-27 arrow_drop_down"

    async def get_attribute(self, name: str) -> str | None:
        if name == "aria-expanded":
            return self.expanded
        return None

    async def count(self) -> int:
        return 1

    def nth(self, index: int) -> FakeScoutYearSwitch:
        if index != 0:
            raise IndexError(index)
        return self

    async def is_visible(self) -> bool:
        return True

    async def click(self) -> None:
        self.click_count += 1
        self.expanded = "false"


class FakeSelectedScoutYear:
    def __init__(self, year_switch: FakeScoutYearSwitch) -> None:
        self.year_switch = year_switch

    @property
    def first(self) -> FakeSelectedScoutYear:
        return self

    async def wait_for(self, *, state: str, timeout: int) -> None:
        if state == "visible" and self.year_switch.expanded != "false":
            raise PlaywrightTimeoutError("Scout year menu is still open")


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

    async def test_closes_selected_scout_year_menu_before_continuing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            year_switch = FakeScoutYearSwitch()
            selected_year = FakeSelectedScoutYear(year_switch)

            def get_locator(_page: object, key: str, **_values: str) -> object:
                if key == "scout_year_switch":
                    return year_switch
                if key == "scout_year_selected":
                    return selected_year
                raise AssertionError(f"Unexpected selector: {key}")

            client._locator = get_locator

            await client._select_scout_year(object(), "26-27")

            self.assertEqual(year_switch.click_count, 1)
            self.assertEqual(year_switch.expanded, "false")

    async def test_waits_for_delayed_form_value(self) -> None:
        matches = await _wait_for_input_value(
            FakeDelayedInputLocator(value="1.00", delay=0.05),
            expected="1.00",
            timeout_ms=1000,
        )

        self.assertTrue(matches)

    async def test_accepts_localized_amount_value(self) -> None:
        matches = await _wait_for_input_value(
            FakeDelayedInputLocator(value="123456", delay=0.01),
            expected="1234.56",
            timeout_ms=1000,
            normalize=_normalize_amount_input,
        )

        self.assertTrue(matches)

    def test_normalizes_localized_and_grouped_amount_values(self) -> None:
        self.assertEqual(_normalize_amount_input("123456"), "1234.56")
        self.assertEqual(_normalize_amount_input("1,00"), "1.00")
        self.assertEqual(_normalize_amount_input("1.234,56"), "1234.56")
        self.assertEqual(_normalize_amount_input("1,234.56"), "1234.56")

    def test_parses_ledger_pagination_range(self) -> None:
        self.assertEqual(_parse_ledger_page_range("1-10 di 44"), (1, 10, 44))
        self.assertEqual(_parse_ledger_page_range("0-0 di 0"), (0, 0, 0))
        self.assertIsNone(_parse_ledger_page_range("1-10 di 5"))

    def test_normalizes_sort_icons_from_ledger_headers(self) -> None:
        self.assertEqual(
            _normalize_ledger_header("arrow_upward Importo Entrata"),
            "Importo Entrata",
        )
        self.assertEqual(
            _normalize_ledger_header("Tipologia Entrataarrow_downward"),
            "Tipologia Entrata",
        )

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
        self.assertEqual(
            selectors["ledger_loading_overlay"]["selector"],
            "#q-loading .q-loading__backdrop",
        )
        self.assertEqual(
            selectors["ledger_page_indicator"]["text_pattern"],
            r"^\d+\s*-\s*\d+\s+di\s+\d+$",
        )
        self.assertEqual(selectors["ledger_search"]["placeholder"], "Cerca")
        self.assertIn("Cassa", configuration["account_name_pattern"])
        self.assertIn("Conto", configuration["account_name_pattern"])


if __name__ == "__main__":
    unittest.main()
