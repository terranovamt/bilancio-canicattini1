from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml
from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from bilancio.client import (
    AccountLedger,
    BilancioClientError,
    ACCOUNTING_VOICE_OPTIONS,
    LedgerEntry,
    PortalExpense,
    PortalOption,
    PortalOptionsUnavailable,
    PortalSubmissionResult,
    TransactionDirection,
    default_unit_for_account,
    scout_year_label,
)

logger = logging.getLogger(__name__)
_MAX_ATTEMPTS = 3
# Covers a full day so the nightly preload keeps menus fast until the next one.
_OPTIONS_CACHE_SECONDS = 26 * 60 * 60


async def _wait_for_visible_locator(locator: Locator, timeout_ms: int) -> bool:
    try:
        await locator.first.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return False
    return True


async def _wait_for_hidden_locator(locator: Locator, timeout_ms: int) -> bool:
    try:
        await locator.first.wait_for(state="hidden", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return False
    return True


async def _wait_for_input_value(
    locator: Locator,
    expected: str,
    timeout_ms: int,
    normalize: Callable[[str], str] = lambda value: value,
) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    normalized_expected = normalize(expected)
    while True:
        if normalize(await locator.input_value()) == normalized_expected:
            return True
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.1)


def _parse_ledger_page_range(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(\d+)\s*-\s*(\d+)\s+di\s+(\d+)", " ".join(value.split()))
    if match is None:
        return None
    first, last, total = (int(part) for part in match.groups())
    if total == 0:
        return (0, 0, 0) if first == 0 and last == 0 else None
    if first < 1 or last < first or last > total:
        return None
    return first, last, total


async def _wait_for_ledger_data(
    loading_overlay: Locator,
    page_indicator: Locator,
    rows: Locator,
    timeout_ms: int,
) -> tuple[int, int, int]:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    if not await _wait_for_visible_locator(page_indicator, timeout_ms):
        raise PortalOptionsUnavailable("AGESCI ledger pagination did not load")

    while True:
        if await loading_overlay.count() and await loading_overlay.first.is_visible():
            await asyncio.sleep(0.1)
        else:
            page_range = _parse_ledger_page_range(
                await page_indicator.first.inner_text()
            )
            if page_range is not None:
                first, last, total = page_range
                expected_rows = max(0, last - first + 1)
                if await rows.count() >= expected_rows + 2:
                    return page_range
            await asyncio.sleep(0.1)
        if asyncio.get_running_loop().time() >= deadline:
            raise PortalOptionsUnavailable("AGESCI ledger rows did not finish loading")


async def _wait_for_stable_ledger_filter(
    loading_overlay: Locator,
    rows: Locator,
    timeout_ms: int,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    previous_count = await rows.count()
    stable_observations = 0
    while stable_observations < 3:
        if await loading_overlay.count() and await loading_overlay.first.is_visible():
            stable_observations = 0
        else:
            row_count = await rows.count()
            if row_count == previous_count:
                stable_observations += 1
            else:
                previous_count = row_count
                stable_observations = 0
        if asyncio.get_running_loop().time() >= deadline:
            raise PortalOptionsUnavailable(
                "AGESCI ledger search results did not finish updating"
            )
        await asyncio.sleep(0.1)


def _normalize_amount_input(value: str) -> str:
    normalized = re.sub(r"[^\d,.-]", "", value)
    if "," not in normalized:
        if re.fullmatch(r"-?\d+", normalized):
            return f"{Decimal(normalized):.2f}"
        return normalized
    if "." not in normalized:
        return normalized.replace(",", ".")
    if normalized.rfind(",") > normalized.rfind("."):
        return normalized.replace(".", "").replace(",", ".")
    return normalized.replace(",", "")


def _format_amount_input(amount: Decimal) -> str:
    return f"{amount:.2f}"


def _unit_ledger_value_matches(value: str, unit: PortalOption | None) -> bool:
    if unit is None:
        return not value
    if value == unit.label:
        return True
    group_prefix = "Gruppo "
    return unit.label.startswith(group_prefix) and value == unit.label[len(group_prefix) :]


def _normalize_ledger_header(value: str) -> str:
    return " ".join(
        value.replace("arrow_upward", "")
        .replace("arrow_downward", "")
        .split()
    )


def _parse_ledger_amount(value: str) -> Decimal:
    normalized = _normalize_amount_input(_normalize_ledger_header(value))
    if not normalized:
        return Decimal("0")
    try:
        amount = Decimal(normalized)
    except InvalidOperation as error:
        raise PortalOptionsUnavailable("AGESCI ledger amount is not readable") from error
    if not amount.is_finite():
        raise PortalOptionsUnavailable("AGESCI ledger amount is not readable")
    return amount


def _ledger_url_account(url: str) -> str | None:
    values = parse_qs(urlparse(url).query).get("c")
    return values[0] if values else None


async def _wait_for_authentication_marker(
    username: Locator,
    scout_year_switch: Locator,
    timeout_ms: int,
) -> bool | None:
    tasks = {
        asyncio.create_task(_wait_for_visible_locator(username, timeout_ms)): True,
        asyncio.create_task(
            _wait_for_visible_locator(scout_year_switch, timeout_ms)
        ): False,
    }
    pending = set(tasks)
    while pending:
        completed, pending = await asyncio.wait(
            pending,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in completed:
            if task.result():
                for waiting_task in pending:
                    waiting_task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                return tasks[task]
    return None


class ManualPortalLoginRequired(BilancioClientError):
    """Raised when credentials do not complete the portal login."""


class PlaywrightBilancioClient:
    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        selectors_path: str | Path,
        session_state_path: str | Path,
        debug_path: str | Path,
        browser_channel: str | None = None,
        timeout_ms: int = 20_000,
        headless: bool = True,
    ) -> None:
        self._username = username
        self._password = password
        self._session_state_path = Path(session_state_path)
        self._debug_path = Path(debug_path)
        self._browser_channel = browser_channel
        self._timeout_ms = timeout_ms
        self._headless = headless
        self._lock = asyncio.Lock()
        self._options_lock = asyncio.Lock()
        self._options_cache: dict[
            tuple[object, ...], tuple[float, tuple[PortalOption, ...]]
        ] = {}
        self._visible_playwright: Playwright | None = None
        self._visible_browser: Browser | None = None
        self._visible_context: BrowserContext | None = None
        self._visible_page: Page | None = None

        try:
            loaded = yaml.safe_load(Path(selectors_path).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise BilancioClientError(
                "AGESCI selector configuration could not be loaded"
            ) from error
        if not isinstance(loaded, dict) or not isinstance(loaded.get("selectors"), dict):
            raise BilancioClientError("AGESCI selector configuration is invalid")
        configured_url = loaded.get("login_url")
        if not isinstance(configured_url, str) or not configured_url.strip():
            raise BilancioClientError("AGESCI login URL is missing from selectors")
        self._login_url = base_url.strip() or configured_url
        self._selectors: dict[str, dict[str, object]] = loaded["selectors"]
        self._account_pattern = re.compile(
            str(loaded.get("account_name_pattern", "")),
            re.IGNORECASE,
        )

    async def clear_options_cache(self) -> None:
        async with self._options_lock:
            self._options_cache.clear()
        logger.info("Portal options cache cleared")

    async def _cached_options(
        self,
        key: tuple[object, ...],
        operation: Callable[[Page], Awaitable[Sequence[PortalOption]]],
        transaction_date: date | None,
    ) -> Sequence[PortalOption]:
        selected_date = transaction_date or date.today()
        cache_key = (scout_year_label(selected_date), *key)
        async with self._options_lock:
            now = asyncio.get_running_loop().time()
            cached = self._options_cache.get(cache_key)
            if cached is not None and now - cached[0] < _OPTIONS_CACHE_SECONDS:
                return cached[1]
            options = tuple(
                await self._with_page(operation, transaction_date=selected_date)
            )
            self._options_cache[cache_key] = (
                asyncio.get_running_loop().time(),
                options,
            )
            return options

    async def health_check(self) -> None:
        async def check(page: Page) -> None:
            accounts = await self._read_accounts(page)
            if not accounts:
                raise PortalOptionsUnavailable(
                    "The portal did not expose any account links"
                )
            await self._open_account_form(
                page,
                accounts[0],
                TransactionDirection.EXPENSE,
            )

        await self._with_page(check, transaction_date=date.today())

    async def list_accounts(
        self,
        *,
        transaction_date: date | None = None,
    ) -> Sequence[PortalOption]:
        return await self._cached_options(
            ("accounts",),
            self._read_accounts,
            transaction_date,
        )

    async def list_transaction_types(
        self,
        account: PortalOption,
        direction: TransactionDirection,
        *,
        transaction_date: date | None = None,
        accounting_voice: str = "Voci Scout",
    ) -> Sequence[PortalOption]:
        async def read(page: Page) -> Sequence[PortalOption]:
            await self._open_account_form(page, account, direction)
            await self._select_accounting_voice(page, accounting_voice)
            options = await self._read_dropdown(
                page,
                "transaction_type",
                direction,
            )
            await self._close_operation_form(page)
            return options

        return await self._cached_options(
            ("types", account, direction, accounting_voice),
            read,
            transaction_date,
        )

    async def list_units(
        self,
        account: PortalOption,
        direction: TransactionDirection,
        transaction_type: PortalOption,
        *,
        transaction_date: date | None = None,
        accounting_voice: str = "Voci Scout",
    ) -> Sequence[PortalOption]:
        async def read(page: Page) -> Sequence[PortalOption]:
            await self._open_account_form(page, account, direction)
            await self._select_accounting_voice(page, accounting_voice)
            await self._select_dropdown(
                page,
                "transaction_type",
                transaction_type,
                direction,
            )
            options = await self._read_dropdown(page, "unit")
            await self._close_operation_form(page)
            return options

        return await self._cached_options(
            ("units", account, direction, accounting_voice, transaction_type),
            read,
            transaction_date,
        )

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
        async def read(page: Page) -> Sequence[PortalOption]:
            await self._open_account_form(page, account, direction)
            await self._select_accounting_voice(page, accounting_voice)
            await self._select_dropdown(
                page,
                "transaction_type",
                transaction_type,
                direction,
            )
            await self._select_dropdown(page, "unit", unit)
            options = await self._read_dropdown(page, "category")
            await self._close_operation_form(page)
            return options

        return await self._cached_options(
            ("categories", account, direction, accounting_voice, transaction_type, unit),
            read,
            transaction_date,
        )

    async def read_ledgers(
        self,
        *,
        transaction_date: date,
        include_entries: bool = True,
    ) -> Sequence[AccountLedger]:
        async def read(page: Page) -> list[AccountLedger]:
            accounts = await self._read_accounts(page)
            if not include_entries:
                return await self._read_account_overview(page, accounts)
            ledgers: list[AccountLedger] = []
            for account in accounts:
                await self._open_account_ledger(page, account)
                await page.wait_for_url(
                    lambda url: _ledger_url_account(url) == account.label,
                    timeout=self._timeout_ms,
                )
                ledgers.append(await self._read_account_ledger(page, account))
            return ledgers

        return await self._with_page(
            read,
            transaction_date=transaction_date,
            max_attempts=2,
        )

    async def create_expense(
        self,
        expense: PortalExpense,
        *,
        dry_run: bool,
    ) -> PortalSubmissionResult:
        if not dry_run and expense.transaction_type is None:
            raise BilancioClientError(
                "AGESCI requires a transaction type for real submissions"
            )
        unit = expense.unit or default_unit_for_account(expense.account)
        expense_to_submit = replace(expense, unit=unit)

        async def fill_form(page: Page) -> PortalSubmissionResult:
            nonlocal expense_to_submit
            expense_to_submit = replace(expense, unit=unit)
            await self._run_portal_stage(
                "opening the account ledger",
                self._open_account_ledger(page, expense.account),
            )
            existing = await self._run_portal_stage(
                "checking for an existing expense",
                self._find_matching_expense(
                    page,
                    expense_to_submit,
                    allow_missing_unit=True,
                ),
            )
            if existing:
                return PortalSubmissionResult(
                    verified=True,
                    confirmation_id="existing-entry",
                    dry_run=dry_run,
                )
            await self._run_portal_stage(
                "selecting the transaction direction",
                self._open_operation_form(page, expense.direction),
            )
            await self._run_portal_stage(
                "filling date, description, and amount",
                self._fill_form(page, expense_to_submit),
            )
            await self._run_portal_stage(
                "selecting the accounting voice",
                self._select_accounting_voice(page, expense.accounting_voice),
            )
            if expense.transaction_type is not None:
                await self._run_portal_stage(
                    "selecting the transaction type",
                    self._select_dropdown(
                        page,
                        "transaction_type",
                        expense.transaction_type,
                        expense.direction,
                    ),
                )
            if unit is not None:
                selected_unit = await self._run_portal_stage(
                    "selecting the unit",
                    self._select_dropdown(page, "unit", unit, allow_empty=True),
                )
                if selected_unit is None:
                    logger.info("Portal offers no unit; leaving the unit empty")
                    expense_to_submit = replace(expense_to_submit, unit=None)
            if expense.category is not None:
                await self._run_portal_stage(
                    "selecting the category",
                    self._select_dropdown(page, "category", expense.category),
                )
            await self._run_portal_stage(
                "verifying date, description, and amount",
                self._fill_form(page, expense_to_submit),
            )
            if dry_run:
                return PortalSubmissionResult(
                    verified=True,
                    dry_run=True,
                    unit_left_empty=unit is not None and expense_to_submit.unit is None,
                )
            await self._locator(page, "save").click()
            if not await self._wait_for_matching_expense(page, expense_to_submit):
                await self._save_diagnostics(page, "submission-verification-failed")
                raise BilancioClientError(
                    "AGESCI did not show the submitted expense in the account ledger"
                )
            return PortalSubmissionResult(
                verified=True,
                dry_run=False,
                unit_left_empty=unit is not None and expense_to_submit.unit is None,
            )

        return await self._with_page(
            fill_form,
            transaction_date=expense.transaction_date,
            max_attempts=1,
        )

    async def _run_portal_stage[T](
        self,
        stage: str,
        operation: Awaitable[T],
    ) -> T:
        try:
            return await operation
        except PlaywrightTimeoutError as error:
            logger.warning("AGESCI stage timed out: %s", stage)
            raise BilancioClientError(
                f"AGESCI timed out while {stage}"
            ) from error

    async def _with_page[T](
        self,
        operation: Callable[[Page], Awaitable[T]],
        *,
        transaction_date: date | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> T:
        async with self._lock:
            last_error: Exception | None = None
            last_diagnostic_path: str | None = None
            for attempt in range(max_attempts):
                async with self._page_context() as (page, context):
                    try:
                        await self._run_portal_stage(
                            "authenticating with the portal",
                            self._ensure_authenticated(
                                page,
                                context,
                                transaction_date or date.today(),
                            ),
                        )
                        return await operation(page)
                    except PortalOptionsUnavailable as error:
                        error.diagnostic_path = str(
                            await self._save_diagnostics(page, "selectors")
                        )
                        raise
                    except ManualPortalLoginRequired as error:
                        error.diagnostic_path = str(
                            await self._save_diagnostics(
                                page,
                                "manual-login-required",
                            )
                        )
                        raise
                    except (PlaywrightError, BilancioClientError) as error:
                        last_error = error
                        if attempt == max_attempts - 1:
                            if page.is_closed():
                                logger.warning(
                                    "Portal page closed before diagnostics "
                                    "could be saved"
                                )
                            else:
                                try:
                                    last_diagnostic_path = str(
                                        await self._save_diagnostics(
                                            page,
                                            "portal-error",
                                        )
                                    )
                                except (PlaywrightError, OSError) as diagnostic_error:
                                    logger.warning(
                                        "Could not save portal diagnostics "
                                        "after failure (%s)",
                                        type(diagnostic_error).__name__,
                                    )
                                if (
                                    isinstance(error, BilancioClientError)
                                    and last_diagnostic_path is not None
                                ):
                                    error.diagnostic_path = last_diagnostic_path
                            break
                await asyncio.sleep(2**attempt)
            raise BilancioClientError(
                "AGESCI portal operation failed after "
                f"{max_attempts} attempts "
                f"({type(last_error).__name__ if last_error else 'UnknownError'}"
                f"{': ' + str(last_error) if isinstance(last_error, BilancioClientError) else ''})",
                diagnostic_path=last_diagnostic_path,
            ) from last_error

    @asynccontextmanager
    async def _page_context(self):
        if not self._headless:
            if self._visible_playwright is None:
                self._visible_playwright = await async_playwright().start()
            if (
                self._visible_browser is None
                or not self._visible_browser.is_connected()
            ):
                launch_options: dict[str, object] = {
                    "headless": False,
                    "slow_mo": 350,
                }
                if self._browser_channel is not None:
                    launch_options["channel"] = self._browser_channel
                self._visible_browser = await self._visible_playwright.chromium.launch(
                    **launch_options
                )
                self._visible_context = None
                self._visible_page = None
            if self._visible_context is None:
                state_path = self._session_state_path
                self._visible_context = await self._visible_browser.new_context(
                    storage_state=(
                        str(state_path)
                        if state_path.is_file()
                        else None
                    )
                )
                self._visible_context.set_default_timeout(self._timeout_ms)
            if self._visible_page is None or self._visible_page.is_closed():
                self._visible_page = await self._visible_context.new_page()
            yield self._visible_page, self._visible_context
            return

        async with async_playwright() as playwright:
            launch_options: dict[str, object] = {"headless": True}
            if self._browser_channel is not None:
                launch_options["channel"] = self._browser_channel
            browser: Browser = await playwright.chromium.launch(**launch_options)
            context: BrowserContext | None = None
            try:
                state_path = self._session_state_path
                context = await browser.new_context(
                    storage_state=(
                        str(state_path)
                        if state_path.is_file()
                        else None
                    )
                )
                context.set_default_timeout(self._timeout_ms)
                page = await context.new_page()
                yield page, context
            finally:
                if context is not None:
                    await context.close()
                await browser.close()

    async def close(self) -> None:
        browser = self._visible_browser
        browser_connected = browser is not None and browser.is_connected()
        if self._visible_context is not None and browser_connected:
            await self._visible_context.close()
        self._visible_context = None
        if browser_connected and browser is not None:
            await browser.close()
        self._visible_browser = None
        if self._visible_playwright is not None:
            await self._visible_playwright.stop()
            self._visible_playwright = None
        self._visible_page = None

    async def wait_for_visible_browser_close(self) -> bool:
        browser = self._visible_browser
        session_saved = False
        while browser is not None and browser.is_connected():
            page = self._visible_page
            if page is None or page.is_closed():
                break
            context = self._visible_context
            if (
                not session_saved
                and page is not None
                and context is not None
            ):
                username = self._locator(page, "username")
                year_switch = self._locator(page, "scout_year_switch")
                login_visible = (
                    await username.count()
                    and await username.first.is_visible()
                )
                year_visible = (
                    await year_switch.count()
                    and await year_switch.first.is_visible()
                )
                if year_visible and not login_visible:
                    self._session_state_path.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )
                    await context.storage_state(
                        path=str(self._session_state_path)
                    )
                    session_saved = True
            await asyncio.sleep(0.5)
        return session_saved

    async def _ensure_authenticated(
        self,
        page: Page,
        context: BrowserContext,
        transaction_date: date,
    ) -> None:
        cookie_button = self._locator(page, "cookie_accept")
        if await cookie_button.count() and await cookie_button.first.is_visible():
            await cookie_button.first.click()

        username = self._locator(page, "username")
        scout_year_switch = self._locator(page, "scout_year_switch")
        if (
            await scout_year_switch.count()
            and await scout_year_switch.first.is_visible()
            and not (
                await username.count()
                and await username.first.is_visible()
            )
        ):
            await self._select_scout_year(
                page,
                scout_year_label(transaction_date),
            )
            await self._select_role(page)
            self._session_state_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            await context.storage_state(path=str(self._session_state_path))
            return

        await page.goto(self._login_url, wait_until="domcontentloaded")
        if await cookie_button.count() and await cookie_button.first.is_visible():
            await cookie_button.first.click()
        login_visible = await _wait_for_authentication_marker(
            username,
            scout_year_switch,
            self._timeout_ms,
        )
        if login_visible is None:
            raise ManualPortalLoginRequired(
                "AGESCI showed neither the login form nor the scout-year menu"
            )
        if await cookie_button.count() and await cookie_button.first.is_visible():
            await cookie_button.first.click()

        if login_visible:
            await self._require_one(username, "username")
            password = self._locator(page, "password")
            await self._require_one(password, "password")
            await username.fill(self._username)
            await password.fill(self._password)
            await self._locator(page, "login").click()
            try:
                await username.first.wait_for(
                    state="hidden",
                    timeout=self._timeout_ms,
                )
                await scout_year_switch.first.wait_for(
                    state="visible",
                    timeout=self._timeout_ms,
                )
            except PlaywrightTimeoutError:
                raise ManualPortalLoginRequired(
                    "AGESCI login did not complete; manual verification may be required"
                ) from None

        await self._select_scout_year(page, scout_year_label(transaction_date))
        await self._select_role(page)
        self._session_state_path.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(self._session_state_path))

    async def _select_role(self, page: Page) -> None:
        role_switch = self._locator(page, "role_switch")
        if not await role_switch.count() or not await role_switch.first.is_visible():
            return
        await self._require_one(role_switch, "role_switch")
        if await role_switch.first.get_attribute("aria-expanded") != "true":
            await role_switch.click()
        role_entry = self._locator(page, "role_entry")
        if not await _wait_for_visible_locator(role_entry, self._timeout_ms):
            raise PortalOptionsUnavailable(
                "Treasurer role option did not become visible"
            )
        await self._require_one(role_entry, "role_entry")
        await role_entry.click()
        selected_role = self._locator(page, "treasurer_role_selected")
        if not await _wait_for_visible_locator(selected_role, self._timeout_ms):
            raise PortalOptionsUnavailable(
                "Treasurer role did not become active"
            )

    async def _select_scout_year(self, page: Page, year_label: str) -> None:
        year_switch = self._locator(page, "scout_year_switch")
        await self._require_one(year_switch, "scout_year_switch")
        current_label = " ".join((await year_switch.first.inner_text()).split())
        if year_label in current_label:
            if await year_switch.first.get_attribute("aria-expanded") == "true":
                await year_switch.click()
                selected_year = self._locator(
                    page,
                    "scout_year_selected",
                    year_label=year_label,
                )
                if not await _wait_for_visible_locator(
                    selected_year,
                    self._timeout_ms,
                ):
                    raise PortalOptionsUnavailable(
                        "Scout year menu did not close"
                    )
            return
        if await year_switch.first.get_attribute("aria-expanded") != "true":
            await year_switch.click()
        year_entry = self._locator(
            page,
            "scout_year_option",
            year_label=year_label,
        )
        if not await _wait_for_visible_locator(year_entry, self._timeout_ms):
            raise PortalOptionsUnavailable(
                f"Scout year option did not become visible: {year_label}"
            )
        await self._require_one(year_entry, "scout_year_option")
        await year_entry.click()
        deadline = asyncio.get_running_loop().time() + self._timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            if (
                await year_switch.count()
                and await year_switch.first.is_visible()
                and year_label
                in " ".join((await year_switch.first.inner_text()).split())
            ):
                break
            await asyncio.sleep(0.1)
        else:
            raise PortalOptionsUnavailable(
                f"Scout year did not become active: {year_label}"
            )
        if await year_switch.first.get_attribute("aria-expanded") == "true":
            await year_switch.first.click()
        selected_year = self._locator(
            page,
            "scout_year_selected",
            year_label=year_label,
        )
        if not await _wait_for_visible_locator(selected_year, self._timeout_ms):
            raise PortalOptionsUnavailable("Scout year menu did not close")

    async def _read_accounts(self, page: Page) -> Sequence[PortalOption]:
        await self._locator(page, "prima_nota").click()
        links = self._locator(page, "account_links")
        matching_links = links.filter(has_text=self._account_pattern)
        if not await _wait_for_visible_locator(matching_links, self._timeout_ms):
            raise PortalOptionsUnavailable(
                "The portal did not expose any matching account links"
            )
        options: list[PortalOption] = []
        for index in range(await matching_links.count()):
            link = matching_links.nth(index)
            if not await link.is_visible():
                continue
            label = " ".join((await link.inner_text()).split())
            if label and not any(option.label == label for option in options):
                options.append(PortalOption(value=label, label=label))
        return options

    async def _open_account_form(
        self,
        page: Page,
        account: PortalOption,
        direction: TransactionDirection,
    ) -> None:
        await self._open_account_ledger(page, account)
        await self._open_operation_form(page, direction)

    async def _open_account_ledger(
        self,
        page: Page,
        account: PortalOption,
    ) -> None:
        await self._locator(page, "prima_nota").click()
        account_link = page.get_by_role("link", name=account.label, exact=True)
        if not await _wait_for_visible_locator(account_link, self._timeout_ms):
            raise PortalOptionsUnavailable(
                f"AGESCI account link did not become visible: {account.label}"
            )
        await self._require_one(account_link, "account_link")
        await account_link.click()

    async def _open_operation_form(
        self,
        page: Page,
        direction: TransactionDirection,
    ) -> None:
        await self._locator(page, "add_operation").click()
        direction_label = self._direction_label(direction)
        direction_locator = self._locator(
            page,
            "direction",
            direction_label=direction_label,
        )
        if not await _wait_for_visible_locator(direction_locator, self._timeout_ms):
            raise PortalOptionsUnavailable(
                "The transaction direction control did not become visible"
            )
        await self._require_one(direction_locator, "direction")
        try:
            await direction_locator.check()
        except PlaywrightTimeoutError:
            if not await direction_locator.is_checked():
                raise
        if not await direction_locator.is_checked():
            raise PortalOptionsUnavailable(
                "The requested transaction direction did not become active"
            )

    async def _close_operation_form(self, page: Page) -> None:
        await page.keyboard.press("Escape")
        add_operation = self._locator(page, "add_operation")
        if not await _wait_for_visible_locator(add_operation, self._timeout_ms):
            raise PortalOptionsUnavailable(
                "AGESCI operation form did not close after reading options"
            )

    async def _select_accounting_voice(
        self,
        page: Page,
        accounting_voice: str,
    ) -> None:
        if accounting_voice not in ACCOUNTING_VOICE_OPTIONS:
            raise ValueError("Unsupported AGESCI accounting voice")
        voice = self._locator(
            page,
            "accounting_voice",
            accounting_voice=accounting_voice,
        )
        await self._require_one(voice, "accounting_voice")
        if not await voice.is_checked():
            await voice.check(force=True)

    async def _read_dropdown(
        self,
        page: Page,
        selector_key: str,
        direction: TransactionDirection | None = None,
    ) -> Sequence[PortalOption]:
        locator = self._dropdown_locator(page, selector_key, direction)
        await self._require_one(locator, selector_key)
        await locator.click()
        option_list = await self._dropdown_option_list_locator(page, locator)
        option_config = self._selectors.get("portal_option", {})
        option_role = option_config.get("role")
        if not isinstance(option_role, str):
            raise BilancioClientError("AGESCI option selector role is invalid")
        options_locator = option_list.get_by_role(option_role)
        if not await _wait_for_visible_locator(options_locator, self._timeout_ms):
            raise PortalOptionsUnavailable(
                f"The portal returned no options for {selector_key}"
            )
        options: list[PortalOption] = []
        for index in range(await options_locator.count()):
            option = options_locator.nth(index)
            if not await option.is_visible():
                continue
            label = " ".join((await option.inner_text()).split())
            if label and not any(existing.label == label for existing in options):
                options.append(PortalOption(value=label, label=label))
        if not options:
            raise PortalOptionsUnavailable(
                f"The portal returned no options for {selector_key}"
            )
        await locator.click()
        if not await _wait_for_hidden_locator(option_list, self._timeout_ms):
            raise PortalOptionsUnavailable(
                f"The portal option list did not close after reading {selector_key}"
            )
        return options

    async def _select_dropdown(
        self,
        page: Page,
        selector_key: str,
        option: PortalOption,
        direction: TransactionDirection | None = None,
        *,
        allow_empty: bool = False,
    ) -> PortalOption | None:
        locator = self._dropdown_locator(page, selector_key, direction)
        await self._require_one(locator, selector_key)
        await locator.click()
        option_list = await self._dropdown_option_list_locator(page, locator)
        option_config = self._selectors.get("portal_option", {})
        option_role = option_config.get("role")
        if not isinstance(option_role, str):
            raise BilancioClientError("AGESCI option selector role is invalid")
        if allow_empty and await self._option_list_is_empty(
            option_list.get_by_role(option_role),
            option_list.get_by_role("listitem").filter(
                has_text=re.compile(r"^\s*No results\s*$", re.IGNORECASE)
            ),
        ):
            await locator.click()
            if not await _wait_for_hidden_locator(option_list, self._timeout_ms):
                raise PortalOptionsUnavailable(
                    f"The empty portal option list did not close: {selector_key}"
                )
            return None
        option_locator = option_list.get_by_role(
            option_role,
            name=option.label,
            exact=True,
        )
        if not await _wait_for_visible_locator(option_locator, self._timeout_ms):
            raise PortalOptionsUnavailable(
                f"The portal option did not become visible: {selector_key}"
            )
        await self._require_one(option_locator, f"{selector_key}_option")
        await option_locator.click()
        return option

    async def _option_list_is_empty(
        self,
        options: Locator,
        empty_marker: Locator,
    ) -> bool:
        """Wait until the list shows options or stays on "No results" briefly."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_ms / 1000
        empty_since: float | None = None
        while loop.time() < deadline:
            if await options.count() > 0:
                return False
            if await empty_marker.count() > 0:
                empty_since = empty_since or loop.time()
                if loop.time() - empty_since >= 1.5:
                    return True
            else:
                empty_since = None
            await asyncio.sleep(0.1)
        return False

    async def _dropdown_option_list_locator(
        self,
        page: Page,
        dropdown: Locator,
    ) -> Locator:
        controls = await dropdown.get_attribute("aria-controls")
        controlled_ids = controls.split() if controls else []
        if len(controlled_ids) != 1:
            raise PortalOptionsUnavailable(
                "The portal dropdown did not expose one option list"
            )
        option_list = page.locator(f"[id={json.dumps(controlled_ids[0])}]")
        if not await _wait_for_visible_locator(option_list, self._timeout_ms):
            raise PortalOptionsUnavailable(
                "The portal dropdown option list did not become visible"
            )
        return option_list

    def _dropdown_locator(
        self,
        page: Page,
        selector_key: str,
        direction: TransactionDirection | None,
    ) -> Locator:
        if selector_key == "category":
            return self._locator(page, selector_key)
        values: dict[str, str] = {}
        if direction is not None:
            values["direction_label"] = self._direction_label(direction)
        return self._locator(page, selector_key, **values)

    async def _fill_form(self, page: Page, expense: PortalExpense) -> None:
        date_locator = self._locator(page, "date")
        description_locator = self._locator(page, "description")
        amount_locator = self._locator(
            page,
            "amount",
            direction_label=self._direction_label(expense.direction),
        )
        await self._require_one(date_locator, "date")
        await self._require_one(description_locator, "description")
        await self._require_one(amount_locator, "amount")
        await self._fill_and_verify_field(
            date_locator,
            "date",
            expense.transaction_date.strftime("%d/%m/%Y"),
        )
        await self._fill_and_verify_field(
            description_locator,
            "description",
            expense.description,
        )
        await self._fill_and_verify_field(
            amount_locator,
            "amount",
            _format_amount_input(expense.amount),
            normalize=_normalize_amount_input,
            expected_value=f"{expense.amount:.2f}",
            sequential=True,
        )
        if expense.notes:
            notes_locator = self._locator(page, "notes")
            await self._require_one(notes_locator, "notes")
            await self._fill_and_verify_field(
                notes_locator,
                "notes",
                expense.notes,
            )

    async def _fill_and_verify_field(
        self,
        locator: Locator,
        field_name: str,
        value: str,
        *,
        normalize: Callable[[str], str] = lambda input_value: input_value,
        expected_value: str | None = None,
        sequential: bool = False,
    ) -> None:
        try:
            if sequential:
                await locator.click()
                await locator.press("Control+A")
                await locator.press_sequentially(value, delay=120)
            else:
                await locator.fill(value)
        except PlaywrightError as error:
            raise BilancioClientError(
                f"AGESCI {field_name} field could not be filled"
            ) from error
        if not await _wait_for_input_value(
            locator,
            expected_value if expected_value is not None else value,
            self._timeout_ms,
            normalize=normalize,
        ):
            raise BilancioClientError(
                f"AGESCI {field_name} field did not retain the requested value"
            )

    async def _find_matching_expense(
        self,
        page: Page,
        expense: PortalExpense,
        *,
        allow_missing_unit: bool = False,
    ) -> bool:
        return (
            await self._matching_expense_count(
                page,
                expense,
                allow_missing_unit=allow_missing_unit,
            )
            > 0
        )

    async def _wait_for_matching_expense(
        self,
        page: Page,
        expense: PortalExpense,
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + 15
        while asyncio.get_running_loop().time() < deadline:
            if await self._find_matching_expense(page, expense):
                return True
            await asyncio.sleep(0.25)
        return False

    async def _read_account_overview(
        self, page: Page, accounts: Sequence[PortalOption]
    ) -> list[AccountLedger]:
        rows = self._locator(page, "ledger_rows")
        first, last, total = await _wait_for_ledger_data(
            self._locator(page, "ledger_loading_overlay"),
            self._locator(page, "ledger_page_indicator"),
            rows,
            self._timeout_ms,
        )
        if first != 1 or last != total or total != len(accounts):
            raise PortalOptionsUnavailable("AGESCI account overview is incomplete")
        headers = [
            _normalize_ledger_header(text)
            for text in await rows.nth(1).get_by_role("columnheader").all_text_contents()
        ]
        required = ("Conto", "Saldo Iniziale", "Entrate", "Uscite", "Saldo")
        if any(headers.count(header) != 1 for header in required):
            raise PortalOptionsUnavailable("AGESCI account overview columns are invalid")
        columns = {header: headers.index(header) for header in required}
        known = {account.label: account for account in accounts}
        result: list[AccountLedger] = []
        seen: set[str] = set()
        for index in range(2, await rows.count()):
            cells = await rows.nth(index).get_by_role("cell").all_text_contents()
            if len(cells) != len(headers):
                raise PortalOptionsUnavailable("AGESCI account overview row is invalid")
            label = " ".join(cells[columns["Conto"]].split())
            if label not in known or label in seen:
                raise PortalOptionsUnavailable("AGESCI account overview account is invalid")
            values: dict[str, Decimal] = {}
            for header in required[1:]:
                value = cells[columns[header]]
                if not value.strip():
                    raise PortalOptionsUnavailable("AGESCI account overview amount is empty")
                values[header] = _parse_ledger_amount(value)
            if values["Saldo Iniziale"] + values["Entrate"] - values["Uscite"] != values["Saldo"]:
                raise PortalOptionsUnavailable("AGESCI account overview balance is inconsistent")
            seen.add(label)
            result.append(AccountLedger(
                account=known[label],
                opening_balance=values["Saldo Iniziale"],
                total_income=values["Entrate"],
                total_expense=values["Uscite"],
                balance=values["Saldo"],
                entries=(), total_rows=0, complete=True, entries_available=False,
            ))
        if seen != set(known):
            raise PortalOptionsUnavailable("AGESCI account overview accounts are missing")
        return result

    async def _read_account_ledger(
        self,
        page: Page,
        account: PortalOption,
    ) -> AccountLedger:
        rows = self._locator(page, "ledger_rows")
        first, last, total_rows = await _wait_for_ledger_data(
            self._locator(page, "ledger_loading_overlay"),
            self._locator(page, "ledger_page_indicator"),
            rows,
            self._timeout_ms,
        )
        headers = [
            _normalize_ledger_header(text)
            for text in await rows.nth(1).get_by_role("columnheader").all_text_contents()
        ]
        required = (
            "Data Operazione",
            "Descrizione Operazione",
            "Importo Entrata",
            "Importo Uscita",
            "Saldo",
        )
        if any(header not in headers for header in required):
            raise PortalOptionsUnavailable(
                "AGESCI account ledger columns could not be verified"
            )
        columns = {header: headers.index(header) for header in headers}

        totals = await rows.nth(0).get_by_role("columnheader").all_text_contents()
        if len(totals) != len(headers):
            raise PortalOptionsUnavailable("AGESCI ledger totals could not be read")
        totals_income = _parse_ledger_amount(totals[columns["Importo Entrata"]])
        totals_expense = _parse_ledger_amount(totals[columns["Importo Uscita"]])
        balance = _parse_ledger_amount(totals[columns["Saldo"]])

        def column(cells: list[str], header: str) -> str:
            index = columns.get(header)
            if index is None or index >= len(cells):
                return ""
            return " ".join(cells[index].split())

        entries: list[LedgerEntry] = []
        for index in range(2, await rows.count()):
            cells = await rows.nth(index).get_by_role("cell").all_text_contents()
            try:
                transaction_date = datetime.strptime(
                    column(cells, "Data Operazione"), "%d/%m/%Y"
                ).date()
            except ValueError:
                continue
            income = _parse_ledger_amount(column(cells, "Importo Entrata"))
            expense = _parse_ledger_amount(column(cells, "Importo Uscita"))
            entries.append(
                LedgerEntry(
                    transaction_date=transaction_date,
                    description=column(cells, "Descrizione Operazione"),
                    income=income,
                    expense=expense,
                    transaction_type=(
                        column(cells, "Tipologia Entrata")
                        or column(cells, "Tipologia Uscita")
                    ),
                    unit=column(cells, "Unità"),
                )
            )

        opening = [entry for entry in entries if entry.is_opening_balance]
        opening_income = sum((entry.income for entry in opening), Decimal("0"))
        opening_expense = sum((entry.expense for entry in opening), Decimal("0"))
        return AccountLedger(
            account=account,
            opening_balance=opening_income - opening_expense,
            total_income=totals_income - opening_income,
            total_expense=totals_expense - opening_expense,
            balance=balance,
            entries=tuple(entries),
            total_rows=total_rows,
            complete=first <= 1 and last >= total_rows,
        )

    async def _matching_expense_count(
        self,
        page: Page,
        expense: PortalExpense,
        *,
        allow_missing_unit: bool = False,
    ) -> int:
        rows = self._locator(page, "ledger_rows")
        loading_overlay = self._locator(page, "ledger_loading_overlay")
        page_indicator = self._locator(page, "ledger_page_indicator")
        await _wait_for_ledger_data(
            loading_overlay,
            page_indicator,
            rows,
            self._timeout_ms,
        )
        search = self._locator(page, "ledger_search")
        await self._require_one(search, "ledger_search")
        await search.fill(expense.description)
        await _wait_for_stable_ledger_filter(
            loading_overlay,
            rows,
            self._timeout_ms,
        )

        if not await _wait_for_visible_locator(rows.nth(1), self._timeout_ms):
            raise PortalOptionsUnavailable("AGESCI account ledger rows were not found")
        header_row = rows.nth(1)
        required_headers = (
            "Data Operazione",
            "Unità",
            "Descrizione Operazione",
            "Importo Entrata" if expense.direction is TransactionDirection.INCOME else "Importo Uscita",
            "Tipologia Entrata" if expense.direction is TransactionDirection.INCOME else "Tipologia Uscita",
        )
        for header in required_headers:
            header_locator = header_row.get_by_role("columnheader").filter(
                has_text=re.compile(re.escape(header), re.IGNORECASE)
            )
            if not await _wait_for_visible_locator(header_locator, self._timeout_ms):
                raise PortalOptionsUnavailable(
                    "AGESCI account ledger columns could not be verified"
                )
        headers = [
            _normalize_ledger_header(text)
            for text in await header_row.get_by_role("columnheader").all_text_contents()
        ]
        if any(header not in headers for header in required_headers):
            raise PortalOptionsUnavailable(
                "AGESCI account ledger columns could not be verified"
            )
        indices = {header: headers.index(header) for header in headers}
        expected = {
            "Data Operazione": expense.transaction_date.strftime("%d/%m/%Y"),
            "Unità": expense.unit.label if expense.unit is not None else "",
            "Descrizione Operazione": expense.description,
            required_headers[3]: f"{expense.amount:.2f}",
            required_headers[4]: (
                expense.transaction_type.label
                if expense.transaction_type is not None
                else ""
            ),
        }
        if expense.category is not None:
            if "Categoria" not in indices:
                raise PortalOptionsUnavailable(
                    "AGESCI category column could not be verified"
                )
            expected["Categoria"] = expense.category.label

        matches = 0
        for index in range(2, await rows.count()):
            cells = await rows.nth(index).get_by_role("cell").all_text_contents()
            if len(cells) < len(headers):
                continue
            values = {
                header: " ".join(cells[column].split())
                for header, column in indices.items()
                if column < len(cells)
            }
            amount = values.get(required_headers[3], "")
            normalized_amount = re.sub(r"[^\d,.-]", "", amount)
            if "," in normalized_amount:
                normalized_amount = normalized_amount.replace(".", "").replace(",", ".")
            values[required_headers[3]] = normalized_amount
            if all(
                values.get(key) == value
                for key, value in expected.items()
                if key != "Unità"
            ) and (
                _unit_ledger_value_matches(values.get("Unità", ""), expense.unit)
                or (allow_missing_unit and not values.get("Unità"))
            ):
                matches += 1
        return matches

    def _locator(self, page: Page, key: str, **values: str) -> Locator:
        selector = self._selectors.get(key)
        if selector is None:
            raise BilancioClientError(f"AGESCI selector is missing: {key}")
        css_selector = selector.get("selector")
        if isinstance(css_selector, str):
            return page.locator(css_selector)
        if "text" in selector:
            return page.get_by_text(str(selector["text"]))
        text_template = selector.get("text_template")
        if isinstance(text_template, str):
            return page.get_by_text(text_template.format(**values), exact=True)
        text_pattern = selector.get("text_pattern")
        if isinstance(text_pattern, str):
            return page.get_by_text(re.compile(text_pattern), exact=True)
        placeholder = selector.get("placeholder")
        if isinstance(placeholder, str):
            return page.get_by_placeholder(
                placeholder,
                exact=bool(selector.get("exact", False)),
            )
        role = selector.get("role")
        if not isinstance(role, str):
            raise BilancioClientError(f"AGESCI selector role is invalid: {key}")
        name_template = selector.get("name_template")
        if isinstance(name_template, str):
            name = name_template.format(**values)
            return page.get_by_role(
                role,
                name=name,
                exact=bool(selector.get("exact", False)),
            )
        name_pattern = selector.get("name_pattern")
        if isinstance(name_pattern, str):
            return page.get_by_role(
                role,
                name=re.compile(name_pattern, re.IGNORECASE),
            )
        name = selector.get("name")
        if isinstance(name, str):
            return page.get_by_role(
                role,
                name=name,
                exact=bool(selector.get("exact", False)),
            )
        text_contains = selector.get("text_contains")
        if isinstance(text_contains, str):
            return page.get_by_role(role).filter(has_text=text_contains)
        return page.get_by_role(role)

    async def _require_one(
        self,
        locator: Locator,
        selector_name: str,
    ) -> None:
        visible_count = sum(
            [
                await locator.nth(index).is_visible()
                for index in range(await locator.count())
            ]
        )
        if visible_count != 1:
            raise PortalOptionsUnavailable(
                f"Expected one visible AGESCI match for {selector_name}, "
                f"found {visible_count}"
            )

    async def _save_diagnostics(self, page: Page, reason: str) -> Path:
        self._debug_path.mkdir(parents=True, exist_ok=True)
        diagnostic_id = uuid.uuid4().hex
        base_name = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{reason}_{diagnostic_id}"
        html_path = self._debug_path / f"{base_name}.html"
        screenshot_path = self._debug_path / f"{base_name}.png"
        html = await page.content()
        html = re.sub(
            r"(<input\b[^>]*?)\svalue=(['\"])[^'\"]*\2",
            r'\1 value="[REDACTED]"',
            html,
            flags=re.IGNORECASE,
        )
        html = re.sub(
            r"(<textarea\b[^>]*>).*?(</textarea>)",
            r"\1[REDACTED]\2",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        html_path.write_text(html, encoding="utf-8")
        try:
            await page.screenshot(
                path=str(screenshot_path),
                full_page=True,
                mask=[page.locator("input, textarea")],
            )
        except PlaywrightError:
            logger.warning("Could not capture AGESCI diagnostic screenshot")
        return screenshot_path

    @staticmethod
    def _direction_label(direction: TransactionDirection) -> str:
        return "Entrata" if direction is TransactionDirection.INCOME else "Uscita"
