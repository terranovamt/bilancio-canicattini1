from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
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
    ACCOUNTING_VOICE_OPTIONS,
    PortalExpense,
    PortalOption,
    PortalOptionsUnavailable,
    PortalSubmissionResult,
    TransactionDirection,
    scout_year_label,
)

logger = logging.getLogger(__name__)
_MAX_ATTEMPTS = 3


async def _wait_for_visible_locator(locator: Locator, timeout_ms: int) -> bool:
    try:
        await locator.first.wait_for(state="visible", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return False
    return True


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
    ) -> None:
        self._username = username
        self._password = password
        self._session_state_path = Path(session_state_path)
        self._debug_path = Path(debug_path)
        self._browser_channel = browser_channel
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

        await self._with_page(check, transaction_date=date.today())

    async def list_accounts(
        self,
        *,
        transaction_date: date | None = None,
    ) -> Sequence[PortalOption]:
        return await self._with_page(
            self._read_accounts,
            transaction_date=transaction_date or date.today(),
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
            return await self._read_dropdown(page, "transaction_type", direction)

        return await self._with_page(read, transaction_date=transaction_date)

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
            return await self._read_dropdown(page, "unit")

        return await self._with_page(read, transaction_date=transaction_date)

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
            return await self._read_dropdown(page, "category")

        return await self._with_page(read, transaction_date=transaction_date)

    async def create_expense(
        self,
        expense: PortalExpense,
        *,
        dry_run: bool,
    ) -> PortalSubmissionResult:
        async def fill_form(page: Page) -> PortalSubmissionResult:
            await self._open_account_ledger(page, expense.account)
            existing = await self._find_matching_expense(page, expense)
            if existing:
                return PortalSubmissionResult(
                    verified=True,
                    confirmation_id="existing-entry",
                    dry_run=dry_run,
                )
            await self._open_operation_form(page, expense.direction)
            await self._select_accounting_voice(page, expense.accounting_voice)
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
            if dry_run:
                return PortalSubmissionResult(verified=True, dry_run=True)
            await self._locator(page, "save").click()
            if not await self._wait_for_matching_expense(page, expense):
                await self._save_diagnostics(page, "submission-verification-failed")
                raise BilancioClientError(
                    "AGESCI did not show the submitted expense in the account ledger"
                )
            return PortalSubmissionResult(verified=True, dry_run=False)

        return await self._with_page(
            fill_form,
            transaction_date=expense.transaction_date,
            max_attempts=1,
        )

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
                        await self._ensure_authenticated(
                            page,
                            context,
                            transaction_date or date.today(),
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
                            last_diagnostic_path = str(
                                await self._save_diagnostics(page, "portal-error")
                            )
                            if isinstance(error, BilancioClientError):
                                error.diagnostic_path = last_diagnostic_path
                            break
                await asyncio.sleep(2**attempt)
            raise BilancioClientError(
                f"AGESCI portal operation failed after {max_attempts} attempts",
                diagnostic_path=last_diagnostic_path,
            ) from last_error

    @asynccontextmanager
    async def _page_context(self):
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

    async def _ensure_authenticated(
        self,
        page: Page,
        context: BrowserContext,
        transaction_date: date,
    ) -> None:
        await page.goto(self._login_url, wait_until="domcontentloaded")
        cookie_button = self._locator(page, "cookie_accept")
        if await cookie_button.count() and await cookie_button.first.is_visible():
            await cookie_button.first.click()

        username = self._locator(page, "username")
        scout_year_switch = self._locator(page, "scout_year_switch")
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
        selected_year = self._locator(
            page,
            "scout_year_selected",
            year_label=year_label,
        )
        if not await _wait_for_visible_locator(selected_year, self._timeout_ms):
            raise PortalOptionsUnavailable(
                f"Scout year did not become active: {year_label}"
            )

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
        await direction_locator.check()

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
        option_list = await self._dropdown_option_list_locator(page, locator)
        option_config = self._selectors.get("portal_option", {})
        option_role = option_config.get("role")
        if not isinstance(option_role, str):
            raise BilancioClientError("AGESCI option selector role is invalid")
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
        await self._require_one(option_list, "dropdown_option_list")
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

    async def _find_matching_expense(
        self,
        page: Page,
        expense: PortalExpense,
    ) -> bool:
        return await self._matching_expense_count(page, expense) > 0

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

    async def _matching_expense_count(
        self,
        page: Page,
        expense: PortalExpense,
    ) -> int:
        rows = page.get_by_role("row")
        if await rows.count() < 2:
            raise PortalOptionsUnavailable("AGESCI account ledger rows were not found")
        headers = [
            " ".join(text.split())
            for text in await rows.nth(1).get_by_role("columnheader").all_text_contents()
        ]
        required_headers = (
            "Data Operazione",
            "Unità",
            "Descrizione Operazione",
            "Importo Entrata" if expense.direction is TransactionDirection.INCOME else "Importo Uscita",
            "Tipologia Entrata" if expense.direction is TransactionDirection.INCOME else "Tipologia Uscita",
        )
        if any(header not in headers for header in required_headers):
            raise PortalOptionsUnavailable(
                "AGESCI account ledger columns could not be verified"
            )
        indices = {header: headers.index(header) for header in headers}
        expected = {
            "Data Operazione": expense.transaction_date.strftime("%d/%m/%Y"),
            "Unità": expense.unit.label,
            "Descrizione Operazione": expense.description,
            required_headers[3]: f"{expense.amount:.2f}",
            required_headers[4]: expense.transaction_type.label,
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
            if all(values.get(key) == value for key, value in expected.items()):
                matches += 1
        return matches

    def _locator(self, page: Page, key: str, **values: str) -> Locator:
        selector = self._selectors.get(key)
        if selector is None:
            raise BilancioClientError(f"AGESCI selector is missing: {key}")
        if "text" in selector:
            return page.get_by_text(str(selector["text"]))
        text_template = selector.get("text_template")
        if isinstance(text_template, str):
            return page.get_by_text(text_template.format(**values), exact=True)
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
