import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import yaml
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from bilancio.client import (
    BilancioClientError,
    PortalExpense,
    PortalOption,
    PortalSubmissionResult,
    TransactionDirection,
)
from bilancio.playwright_client import (
    PlaywrightBilancioClient,
    _format_amount_input,
    _normalize_amount_input,
    _normalize_ledger_header,
    _parse_ledger_page_range,
    _unit_ledger_value_matches,
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


class FakeClickLocator:
    def __init__(self) -> None:
        self.click_count = 0

    async def click(self) -> None:
        self.click_count += 1


class FakeDirectionLocator:
    def __init__(self, *, timeout_after_check: bool = False) -> None:
        self.checked = False
        self.timeout_after_check = timeout_after_check

    @property
    def first(self) -> FakeDirectionLocator:
        return self

    async def wait_for(self, *, state: str, timeout: int) -> None:
        if state != "visible":
            raise AssertionError(f"Unexpected wait state: {state}")

    async def count(self) -> int:
        return 1

    def nth(self, index: int) -> FakeDirectionLocator:
        if index != 0:
            raise IndexError(index)
        return self

    async def is_visible(self) -> bool:
        return True

    async def check(self) -> None:
        self.checked = True
        if self.timeout_after_check:
            raise PlaywrightTimeoutError("Direction check timed out after selection")

    async def is_checked(self) -> bool:
        return self.checked


class FakeDropdownOption:
    def __init__(self, label: str) -> None:
        self.label = label

    async def is_visible(self) -> bool:
        return True

    async def inner_text(self) -> str:
        return self.label


class FakeDropdownOptionList:
    def __init__(self, options: list[FakeDropdownOption]) -> None:
        self.options = options
        self.visible = False

    @property
    def first(self) -> FakeDropdownOptionList:
        return self

    async def wait_for(self, *, state: str, timeout: int) -> None:
        if (state == "visible") != self.visible:
            raise PlaywrightTimeoutError("Dropdown visibility did not match")

    def get_by_role(self, role: str) -> FakeDropdownOptionList:
        return self

    async def count(self) -> int:
        return len(self.options)

    def nth(self, index: int) -> FakeDropdownOption:
        return self.options[index]


class FakeDropdownLocator:
    def __init__(self, option_list: FakeDropdownOptionList) -> None:
        self.option_list = option_list
        self.click_count = 0

    async def count(self) -> int:
        return 1

    def nth(self, index: int) -> FakeDropdownLocator:
        if index != 0:
            raise IndexError(index)
        return self

    async def is_visible(self) -> bool:
        return True

    async def get_attribute(self, name: str) -> str | None:
        return "expense-options" if name == "aria-controls" else None

    async def click(self) -> None:
        self.click_count += 1
        self.option_list.visible = not self.option_list.visible


class FakeDropdownPage:
    def __init__(self, option_list: FakeDropdownOptionList) -> None:
        self.option_list = option_list

    def locator(self, selector: str) -> FakeDropdownOptionList:
        if selector != '[id="expense-options"]':
            raise AssertionError(f"Unexpected selector: {selector}")
        return self.option_list


class FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    async def press(self, key: str) -> None:
        self.pressed.append(key)


class FakeScoutYearSwitch:
    def __init__(
        self,
        *,
        label: str = "26-27",
        expanded: str = "true",
    ) -> None:
        self.label = label
        self.expanded = expanded
        self.click_count = 0

    @property
    def first(self) -> FakeScoutYearSwitch:
        return self

    async def inner_text(self) -> str:
        return f"{self.label} arrow_drop_down"

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
        self.expanded = "false" if self.expanded == "true" else "true"


class FakeSelectedScoutYear:
    def __init__(
        self,
        year_switch: FakeScoutYearSwitch,
        year_label: str = "26-27",
    ) -> None:
        self.year_switch = year_switch
        self.year_label = year_label

    @property
    def first(self) -> FakeSelectedScoutYear:
        return self

    async def wait_for(self, *, state: str, timeout: int) -> None:
        if state == "visible" and (
            self.year_switch.expanded != "false"
            or self.year_label not in self.year_switch.label
        ):
            raise PlaywrightTimeoutError("Scout year menu is still open")


class FakeScoutYearOption:
    def __init__(self, year_switch: FakeScoutYearSwitch, year_label: str) -> None:
        self.year_switch = year_switch
        self.year_label = year_label

    @property
    def first(self) -> FakeScoutYearOption:
        return self

    async def count(self) -> int:
        return 1

    def nth(self, index: int) -> FakeScoutYearOption:
        if index != 0:
            raise IndexError(index)
        return self

    async def is_visible(self) -> bool:
        return self.year_switch.expanded == "true"

    async def wait_for(self, *, state: str, timeout: int) -> None:
        if state == "visible" and self.year_switch.expanded != "true":
            raise PlaywrightTimeoutError("Scout year option is not visible")

    async def click(self) -> None:
        self.year_switch.label = self.year_label


class PlaywrightBilancioClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_options_cache_reuses_choices_and_can_be_refreshed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            option = PortalOption("account", "Cassa Co.ca")
            client._with_page = AsyncMock(return_value=[option])
            first = await client.list_accounts(transaction_date=date(2026, 10, 29))
            second = await client.list_accounts(transaction_date=date(2026, 11, 1))
            self.assertEqual(first, (option,))
            self.assertEqual(first, second)
            client._with_page.assert_awaited_once()
            await client.clear_options_cache()
            await client.list_accounts(transaction_date=date(2026, 10, 29))
            self.assertEqual(client._with_page.await_count, 2)

    async def test_options_cache_separates_year_and_dependent_selections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            client._with_page = AsyncMock(return_value=[PortalOption("type", "Tipo")])
            account = PortalOption("account", "Cassa Co.ca")
            other_account = PortalOption("other", "Altra cassa")
            for selected in (date(2026, 9, 30), date(2026, 10, 1)):
                for selected_account in (account, other_account):
                    for direction in TransactionDirection:
                        for voice in ("Voci Scout", "Voci Ministero"):
                            for _ in range(2):
                                await client.list_transaction_types(
                                    selected_account, direction,
                                    transaction_date=selected, accounting_voice=voice,
                                )
            self.assertEqual(client._with_page.await_count, 16)
            for selected_type in (PortalOption("t1", "Tipo 1"), PortalOption("t2", "Tipo 2")):
                await client.list_units(account, TransactionDirection.INCOME, selected_type)
                for unit in (PortalOption("u1", "Unità 1"), PortalOption("u2", "Unità 2")):
                    await client.list_categories(
                        account, TransactionDirection.INCOME, selected_type, unit
                    )
            self.assertEqual(client._with_page.await_count, 22)

    async def test_options_cache_expires_after_twenty_six_hours(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            client._with_page = AsyncMock(return_value=[PortalOption("account", "Cassa")])
            selected = date(2026, 10, 29)
            with patch("bilancio.playwright_client.asyncio.get_running_loop") as loop:
                loop.return_value.time.return_value = 10.0
                await client.list_accounts(transaction_date=selected)
                loop.return_value.time.return_value = 10.0 + 25 * 60 * 60
                await client.list_accounts(transaction_date=selected)
                self.assertEqual(client._with_page.await_count, 1)
                loop.return_value.time.return_value = 10.0 + 26 * 60 * 60
                await client.list_accounts(transaction_date=selected)
            self.assertEqual(client._with_page.await_count, 2)

    async def test_options_cache_does_not_hide_portal_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            client._with_page = AsyncMock(side_effect=BilancioClientError("Unavailable"))
            for _ in range(2):
                with self.assertRaises(BilancioClientError):
                    await client.list_accounts()
            self.assertEqual(client._with_page.await_count, 2)

    async def test_one_euro_income_and_expense_save_once_and_verify(self) -> None:
        for direction in TransactionDirection:
            for verified in (True, False):
                with self.subTest(direction=direction, verified=verified):
                    with tempfile.TemporaryDirectory() as directory:
                        client = self.make_client(Path(directory))
                        expense = PortalExpense(
                            transaction_date=date(2026, 10, 29),
                            description=f"PROVA {direction.value}",
                            amount=Decimal("1.00"), direction=direction,
                            account=PortalOption("account", "Cassa Co.ca"),
                            transaction_type=PortalOption("type", "Tipologia di prova"),
                            unit=PortalOption("unit", "Gruppo G1"),
                            category=None, notes=None,
                        )
                        page = object()

                        async def with_page(operation, **_kwargs):
                            return await operation(page)

                        client._with_page = AsyncMock(side_effect=with_page)
                        client._open_account_ledger = AsyncMock()
                        client._find_matching_expense = AsyncMock(return_value=False)
                        client._open_operation_form = AsyncMock()
                        client._fill_form = AsyncMock()
                        client._select_accounting_voice = AsyncMock()
                        client._select_dropdown = AsyncMock()
                        client._wait_for_matching_expense = AsyncMock(return_value=verified)
                        client._save_diagnostics = AsyncMock()
                        save = Mock()
                        save.click = AsyncMock()
                        client._locator = Mock(return_value=save)
                        if verified:
                            result = await client.create_expense(expense, dry_run=False)
                            self.assertTrue(result.verified)
                            self.assertFalse(result.dry_run)
                        else:
                            with self.assertRaises(BilancioClientError):
                                await client.create_expense(expense, dry_run=False)
                        save.click.assert_awaited_once()
                        client._wait_for_matching_expense.assert_awaited_once_with(page, expense)
                        client._open_operation_form.assert_awaited_once_with(page, direction)

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

    async def test_preserves_portal_error_when_visible_page_is_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            page = Mock()
            page.is_closed.return_value = True
            page.content = AsyncMock(
                side_effect=AssertionError("Closed page must not be inspected")
            )
            client._ensure_authenticated = AsyncMock()
            operation = AsyncMock(
                side_effect=PlaywrightError("Target page has been closed")
            )

            @asynccontextmanager
            async def page_context():
                yield page, object()

            client._page_context = page_context

            with self.assertRaises(BilancioClientError) as raised:
                await client._with_page(
                    operation,
                    transaction_date=date(2026, 10, 26),
                    max_attempts=1,
                )

            self.assertIsInstance(raised.exception.__cause__, PlaywrightError)
            self.assertIn(
                type(raised.exception.__cause__).__name__,
                str(raised.exception),
            )
            page.content.assert_not_awaited()

    async def test_reports_the_stage_when_playwright_times_out(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))

            async def timeout_operation() -> None:
                raise PlaywrightTimeoutError("Timed out")

            with self.assertRaisesRegex(
                BilancioClientError,
                "AGESCI timed out while selecting the transaction type",
            ):
                await client._run_portal_stage(
                    "selecting the transaction type",
                    timeout_operation(),
                )

    async def test_live_submission_rejects_missing_transaction_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            expense = PortalExpense(
                transaction_date=date(2026, 10, 26),
                description="Test receipt",
                amount=Decimal("1.00"),
                direction=TransactionDirection.EXPENSE,
                account=PortalOption("account", "Cassa di prova"),
                transaction_type=None,
                unit=None,
                category=None,
                notes=None,
            )
            client._with_page = AsyncMock()

            with self.assertRaises(BilancioClientError):
                await client.create_expense(expense, dry_run=False)

            client._with_page.assert_not_awaited()

    async def test_live_submission_allows_missing_unit_for_unmapped_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            expense = PortalExpense(
                transaction_date=date(2026, 10, 26),
                description="Test receipt",
                amount=Decimal("1.00"),
                direction=TransactionDirection.EXPENSE,
                account=PortalOption("account", "Cassa di prova"),
                transaction_type=PortalOption("type", "Tipologia di prova"),
                unit=None,
                category=None,
                notes=None,
            )
            client._with_page = AsyncMock(
                return_value=PortalSubmissionResult(verified=True, dry_run=False)
            )

            result = await client.create_expense(expense, dry_run=False)

            self.assertTrue(result.verified)
            client._with_page.assert_awaited_once()

    async def test_fills_core_fields_after_direction_before_dropdowns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            events: list[str] = []
            expense = PortalExpense(
                transaction_date=date(2026, 10, 26),
                description="Test receipt",
                amount=Decimal("25.00"),
                direction=TransactionDirection.EXPENSE,
                account=PortalOption("account", "Conto Co.ca"),
                transaction_type=PortalOption("type", "A.2 Test"),
                unit=None,
                category=None,
                notes=None,
            )

            async def with_page(operation, **_kwargs):
                return await operation(object())

            async def open_ledger(_page, _account):
                events.append("ledger")

            async def find_existing(_page, _expense):
                events.append("find_existing")
                return False

            async def open_operation(_page, _direction):
                events.append("direction")

            async def fill_form(_page, _expense):
                events.append("core_fields")

            async def select_voice(_page, _voice):
                events.append("accounting_voice")

            async def select_dropdown(page, selector_key, option, direction=None):
                events.append(selector_key)

            client._with_page = AsyncMock(side_effect=with_page)
            client._open_account_ledger = AsyncMock(side_effect=open_ledger)
            client._find_matching_expense = AsyncMock(side_effect=find_existing)
            client._open_operation_form = AsyncMock(side_effect=open_operation)
            client._fill_form = AsyncMock(side_effect=fill_form)
            client._select_accounting_voice = AsyncMock(side_effect=select_voice)
            client._select_dropdown = AsyncMock(side_effect=select_dropdown)

            result = await client.create_expense(expense, dry_run=True)

            self.assertTrue(result.verified)
            self.assertTrue(result.dry_run)
            self.assertEqual(
                events,
                [
                    "ledger",
                    "find_existing",
                    "direction",
                    "core_fields",
                    "accounting_voice",
                    "transaction_type",
                    "unit",
                    "core_fields",
                ],
            )

    async def test_selects_and_verifies_expense_direction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            add_operation = FakeClickLocator()
            direction_locator = FakeDirectionLocator(timeout_after_check=True)
            selected_direction: dict[str, str] = {}

            def get_locator(
                _page: object,
                key: str,
                **values: str,
            ) -> FakeClickLocator | FakeDirectionLocator:
                if key == "add_operation":
                    return add_operation
                if key == "direction":
                    selected_direction.update(values)
                    return direction_locator
                raise AssertionError(f"Unexpected selector: {key}")

            client._locator = get_locator

            await client._open_operation_form(
                object(),
                TransactionDirection.EXPENSE,
            )

            self.assertEqual(add_operation.click_count, 1)
            self.assertTrue(direction_locator.checked)
            self.assertEqual(selected_direction, {"direction_label": "Uscita"})

    async def test_closes_operation_form_after_reading_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            keyboard = FakeKeyboard()
            page = Mock(keyboard=keyboard)
            add_operation = FakeVisibilityLocator(visible=True)
            client._locator = Mock(return_value=add_operation)

            await client._close_operation_form(page)

            self.assertEqual(keyboard.pressed, ["Escape"])
            client._locator.assert_called_once_with(page, "add_operation")

    async def test_closes_dropdown_after_reading_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            option_list = FakeDropdownOptionList(
                [FakeDropdownOption("A.2 Oneri e servizi bancari")]
            )
            dropdown = FakeDropdownLocator(option_list)
            client._dropdown_locator = Mock(return_value=dropdown)

            options = await client._read_dropdown(
                FakeDropdownPage(option_list),
                "transaction_type",
                TransactionDirection.EXPENSE,
            )

            self.assertEqual(
                [option.label for option in options],
                ["A.2 Oneri e servizi bancari"],
            )
            self.assertEqual(dropdown.click_count, 2)
            self.assertFalse(option_list.visible)

    async def test_saves_session_after_manual_login_and_waits_for_close(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            browser = Mock()
            browser.is_connected.side_effect = (True, False, False)
            page = Mock()
            page.is_closed.return_value = False
            context = AsyncMock()
            username = Mock()
            username.count = AsyncMock(return_value=0)
            year_switch = Mock()
            year_switch.count = AsyncMock(return_value=1)
            year_switch.first.is_visible = AsyncMock(return_value=True)
            locators = {
                "username": username,
                "scout_year_switch": year_switch,
            }
            client._visible_browser = browser
            client._visible_page = page
            client._visible_context = context
            client._locator = lambda _page, key, **_values: locators[key]

            saved = await client.wait_for_visible_browser_close()

            self.assertTrue(saved)
            context.storage_state.assert_awaited_once_with(
                path=str(client._session_state_path),
            )
            self.assertIs(client._visible_browser, browser)
            await client.close()
            self.assertIsNone(client._visible_browser)

    async def test_stops_waiting_when_visible_page_is_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            browser = Mock()
            browser.is_connected.return_value = True
            browser.close = AsyncMock()
            context = AsyncMock()
            page = Mock()
            page.is_closed.return_value = True
            client._visible_browser = browser
            client._visible_context = context
            client._visible_page = page

            saved = await client.wait_for_visible_browser_close()
            await client.close()

            self.assertFalse(saved)
            context.close.assert_awaited_once()
            browser.close.assert_awaited_once()

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

    async def test_reuses_an_authenticated_page_without_opening_login_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            cookie_button = Mock()
            cookie_button.count = AsyncMock(return_value=0)
            username = Mock()
            username.count = AsyncMock(return_value=0)
            scout_year_switch = Mock()
            scout_year_switch.count = AsyncMock(return_value=1)
            scout_year_switch.first.is_visible = AsyncMock(return_value=True)
            locators = {
                "cookie_accept": cookie_button,
                "username": username,
                "scout_year_switch": scout_year_switch,
            }
            client._locator = lambda _page, key, **_values: locators[key]
            client._select_scout_year = AsyncMock()
            client._select_role = AsyncMock()
            page = Mock()
            page.goto = AsyncMock()
            context = Mock()
            context.storage_state = AsyncMock()

            await client._ensure_authenticated(
                page,
                context,
                date(2025, 11, 1),
            )

            page.goto.assert_not_awaited()
            client._select_scout_year.assert_awaited_once_with(page, "25-26")
            client._select_role.assert_awaited_once_with(page)
            context.storage_state.assert_awaited_once()

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

    async def test_closes_scout_year_menu_after_changing_year(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = self.make_client(Path(directory))
            year_switch = FakeScoutYearSwitch(expanded="false")
            selected_year = FakeSelectedScoutYear(year_switch, "25-26")
            year_option = FakeScoutYearOption(year_switch, "25-26")

            def get_locator(_page: object, key: str, **_values: str) -> object:
                if key == "scout_year_switch":
                    return year_switch
                if key == "scout_year_option":
                    return year_option
                if key == "scout_year_selected":
                    return selected_year
                raise AssertionError(f"Unexpected selector: {key}")

            client._locator = get_locator

            await client._select_scout_year(object(), "25-26")

            self.assertEqual(year_switch.label, "25-26")
            self.assertEqual(year_switch.click_count, 2)
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
            FakeDelayedInputLocator(value="1.234,56", delay=0.01),
            expected="1234.56",
            timeout_ms=1000,
            normalize=_normalize_amount_input,
        )

        self.assertTrue(matches)

    def test_normalizes_localized_and_grouped_amount_values(self) -> None:
        self.assertEqual(_normalize_amount_input("25"), "25.00")
        self.assertEqual(_normalize_amount_input("2500"), "2500.00")
        self.assertEqual(_normalize_amount_input("1,00"), "1.00")
        self.assertEqual(_normalize_amount_input("25,00"), "25.00")
        self.assertEqual(_normalize_amount_input("25.00"), "25.00")
        self.assertEqual(_normalize_amount_input("1.234,56"), "1234.56")
        self.assertEqual(_normalize_amount_input("1,234.56"), "1234.56")

    async def test_rejects_2500_when_expected_amount_is_25(self) -> None:
        matches = await _wait_for_input_value(
            FakeDelayedInputLocator(value="2500", delay=0),
            expected="25.00",
            timeout_ms=10,
            normalize=_normalize_amount_input,
        )

        self.assertFalse(matches)

    def test_formats_amounts_with_dot_decimal_separator(self) -> None:
        self.assertEqual(_format_amount_input(Decimal("25")), "25.00")
        self.assertEqual(_format_amount_input(Decimal("2500")), "2500.00")

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

    def test_matches_short_group_unit_value_in_ledger(self) -> None:
        unit = PortalOption("unit", "Gruppo G1")

        self.assertTrue(_unit_ledger_value_matches("G1", unit))
        self.assertTrue(_unit_ledger_value_matches("Gruppo G1", unit))
        self.assertFalse(_unit_ledger_value_matches("G2", unit))
        self.assertTrue(_unit_ledger_value_matches("", None))
        self.assertFalse(_unit_ledger_value_matches("G1", None))

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
            selectors["amount"]["name_template"],
            "Importo {direction_label}",
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
