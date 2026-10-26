from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import yaml
from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from bilancio.client import (
    BilancioClientError,
    PortalExpense,
    PortalOption,
    PortalOptionsUnavailable,
    PortalSubmissionResult,
    TransactionDirection,
)

logger = logging.getLogger(__name__)
_MAX_ATTEMPTS = 3


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
        timeout_ms: int = 20_000,
    ) -> None:
        self._username = username
        self._password = password
        self._session_state_path = Path(session_state_path)
        self._debug_path = Path(debug_path)
        self._timeout_ms = timeout_ms
        self._lock = asyncio.Lock()

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

        await self._with_page(check)

    async def list_accounts(self) -> Sequence[PortalOption]:
        return await self._with_page(self._read_accounts)

    async def list_transaction_types(
        self,
        account: PortalOption,
        direction: TransactionDirection,
    ) -> Sequence[PortalOption]:
        async def read(page: Page) -> Sequence[PortalOption]:
            await self._open_account_form(page, account, direction)
            return await self._read_dropdown(page, "transaction_type", direction)

        return await self._with_page(read)

    async def list_units(
        self,
        account: PortalOption,
        direction: TransactionDirection,
        transaction_type: PortalOption,
    ) -> Sequence[PortalOption]:
        async def read(page: Page) -> Sequence[PortalOption]:
            await self._open_account_form(page, account, direction)
            await self._select_dropdown(
                page,
                "transaction_type",
                transaction_type,
                direction,
            )
            return await self._read_dropdown(page, "unit")

        return await self._with_page(read)

    async def list_categories(
        self,
        account: PortalOption,
        direction: TransactionDirection,
        transaction_type: PortalOption,
        unit: PortalOption,
    ) -> Sequence[PortalOption]:
        async def read(page: Page) -> Sequence[PortalOption]:
            await self._open_account_form(page, account, direction)
            await self._select_dropdown(
                page,
                "transaction_type",
                transaction_type,
                direction,
            )
            await self._select_dropdown(page, "unit", unit)
            return await self._read_dropdown(page, "category")

        return await self._with_page(read)

    async def create_expense(
        self,
        expense: PortalExpense,
        *,
        dry_run: bool,
    ) -> PortalSubmissionResult:
        if not dry_run:
            raise BilancioClientError(
                "Live AGESCI saving remains disabled until result verification is confirmed"
            )

        async def fill_form(page: Page) -> PortalSubmissionResult:
            await self._open_account_form(
                page,
                expense.account,
                expense.direction,
            )
            await self._select_dropdown(
                page,
                "transaction_type",
                expense.transaction_type,
                expense.direction,
            )
            await self._select_dropdown(page, "unit", expense.unit)
            if expense.category is not None:
                await self._select_dropdown(page, "category", expense.category)
            await self._fill_form(page, expense)
            return PortalSubmissionResult(verified=True, dry_run=True)

        return await self._with_page(fill_form)

    async def _with_page[T](
        self,
        operation: Callable[[Page], Awaitable[T]],
    ) -> T:
        async with self._lock:
            last_error: Exception | None = None
            last_diagnostic_path: str | None = None
            for attempt in range(_MAX_ATTEMPTS):
                async with self._page_context() as (page, context):
                    try:
                        await self._ensure_authenticated(page, context)
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
                        if attempt == _MAX_ATTEMPTS - 1:
                            last_diagnostic_path = str(
                                await self._save_diagnostics(page, "portal-error")
                            )
                            if isinstance(error, BilancioClientError):
                                error.diagnostic_path = last_diagnostic_path
                            break
                await asyncio.sleep(2**attempt)
            raise BilancioClientError(
                "AGESCI portal operation failed after three attempts",
                diagnostic_path=last_diagnostic_path,
            ) from last_error

    @asynccontextmanager
    async def _page_context(self):
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(headless=True)
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

    async def _ensure_authenticated(
        self,
        page: Page,
        context: BrowserContext,
    ) -> None:
        await page.goto(self._login_url, wait_until="domcontentloaded")
        cookie_button = self._locator(page, "cookie_accept")
        if await cookie_button.count() and await cookie_button.first.is_visible():
            await cookie_button.first.click()

        username = self._locator(page, "username")
        if await username.count() and await username.first.is_visible():
            await self._require_one(username, "username")
            password = self._locator(page, "password")
            await self._require_one(password, "password")
            await username.fill(self._username)
            await password.fill(self._password)
            await self._locator(page, "login").click()
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeoutError:
                pass
            if await username.count() and await username.first.is_visible():
                raise ManualPortalLoginRequired(
                    "AGESCI login did not complete; manual verification may be required"
                )

        await self._select_role(page)
        self._session_state_path.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(self._session_state_path))

    async def _select_role(self, page: Page) -> None:
        role_switch = self._locator(page, "role_switch")
        if not await role_switch.count() or not await role_switch.first.is_visible():
            return
        await self._require_one(role_switch, "role_switch")
        await role_switch.click()
        role_entry = self._locator(page, "role_entry")
        await self._require_one(role_entry, "role_entry")
        await role_entry.click()

    async def _read_accounts(self, page: Page) -> Sequence[PortalOption]:
        await self._locator(page, "prima_nota").click()
        links = self._locator(page, "account_links")
        options: list[PortalOption] = []
        for index in range(await links.count()):
            link = links.nth(index)
            if not await link.is_visible():
                continue
            label = " ".join((await link.inner_text()).split())
            if (
                label
                and self._account_pattern.search(label)
                and not any(option.label == label for option in options)
            ):
                options.append(PortalOption(value=label, label=label))
        if not options:
            raise PortalOptionsUnavailable(
                "The portal did not expose any matching account links"
            )
        return options

    async def _open_account_form(
        self,
        page: Page,
        account: PortalOption,
        direction: TransactionDirection,
    ) -> None:
        await self._locator(page, "prima_nota").click()
        account_link = page.get_by_role("link", name=account.label, exact=True)
        await self._require_one(account_link, "account_link")
        await account_link.click()
        await self._locator(page, "add_operation").click()
        direction_label = self._direction_label(direction)
        direction_locator = self._locator(
            page,
            "direction",
            direction_label=direction_label,
        )
        await self._require_one(direction_locator, "direction")
        await direction_locator.check()

    async def _read_dropdown(
        self,
        page: Page,
        selector_key: str,
        direction: TransactionDirection | None = None,
    ) -> Sequence[PortalOption]:
        locator = self._dropdown_locator(page, selector_key, direction)
        await self._require_one(locator, selector_key)
        await locator.click()
        options_locator = self._locator(page, "portal_option")
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
        return options

    async def _select_dropdown(
        self,
        page: Page,
        selector_key: str,
        option: PortalOption,
        direction: TransactionDirection | None = None,
    ) -> None:
        locator = self._dropdown_locator(page, selector_key, direction)
        await self._require_one(locator, selector_key)
        await locator.click()
        option_config = self._selectors.get("portal_option", {})
        option_role = option_config.get("role")
        if not isinstance(option_role, str):
            raise BilancioClientError("AGESCI option selector role is invalid")
        option_locator = page.get_by_role(
            option_role,
            name=option.label,
            exact=True,
        )
        await self._require_one(option_locator, f"{selector_key}_option")
        await option_locator.click()

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
        amount_locator = self._locator(page, "amount")
        await self._require_one(date_locator, "date")
        await self._require_one(description_locator, "description")
        await self._require_one(amount_locator, "amount")
        await date_locator.fill(expense.transaction_date.strftime("%d/%m/%Y"))
        await description_locator.fill(expense.description)
        await amount_locator.fill(f"{expense.amount:.2f}")
        notes = expense.notes
        if notes:
            notes_locator = self._locator(page, "notes")
            await self._require_one(notes_locator, "notes")
            await notes_locator.fill(notes)

        if (
            await date_locator.input_value()
            != expense.transaction_date.strftime("%d/%m/%Y")
            or await description_locator.input_value() != expense.description
            or await amount_locator.input_value() != f"{expense.amount:.2f}"
        ):
            await self._save_diagnostics(page, "form-verification-failed")
            raise BilancioClientError(
                "AGESCI form values did not match the requested expense"
            )

    def _locator(self, page: Page, key: str, **values: str) -> Locator:
        selector = self._selectors.get(key)
        if selector is None:
            raise BilancioClientError(f"AGESCI selector is missing: {key}")
        if "text" in selector:
            return page.get_by_text(str(selector["text"]))
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
