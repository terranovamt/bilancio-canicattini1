from __future__ import annotations

import asyncio
import calendar
import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from collections.abc import Callable
from typing import Protocol, runtime_checkable
import uuid

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import ContextTypes

from bilancio.client import (
    BilancioClient,
    BilancioClientError,
    PortalExpense,
    PortalOption,
    TransactionDirection,
    default_unit_for_account,
)
from bot import messages as texts
from config import Settings
from db.database import Database
from storage.archive import FileArchive
from storage.pending import MAX_PENDING_RECEIPT_BYTES, PendingReceiptStore, has_receipt_file
from vision.ollama_client import (
    AnalysisPhase,
    AnalysisProgress,
    LocalVisionError,
    VisionFailureCode,
)
from vision.preprocess import ImagePreprocessingError
from vision.receipt_schema import (
    ReceiptData,
    ReceiptParseError,
    missing_required_fields,
)

logger = logging.getLogger(__name__)

_FIELD_PROMPTS = {
    "date": texts.MISSING_DATE,
    "merchant": texts.MISSING_MERCHANT,
    "total_amount": texts.MISSING_AMOUNT,
}
_EDIT_PROMPTS = {
    "date": texts.EDIT_DATE,
    "merchant": texts.EDIT_MERCHANT,
    "total_amount": texts.EDIT_AMOUNT,
    "suggested_category": texts.EDIT_CATEGORY,
}
_EDIT_FIELDS = {"date", "merchant", "total_amount", "suggested_category"}
_MANUAL_EDIT_PROMPTS = {
    "date": texts.MANUAL_DATE,
    "merchant": texts.MANUAL_MERCHANT,
    "total_amount": texts.MANUAL_AMOUNT,
}
_CALLBACK_STATES = {
    "confirm": {"awaiting_confirmation"},
    "edit": {"awaiting_confirmation", "awaiting_account", "awaiting_receipt_data", "awaiting_edit"},
    "today": {"awaiting_receipt_data", "awaiting_edit"},
    "cal": {"awaiting_receipt_data", "awaiting_edit"},
    "day": {"awaiting_receipt_data", "awaiting_edit"},
    "key": {"awaiting_receipt_data", "awaiting_edit"},
    "manual_save": {"awaiting_manual_final_confirmation"},
    "direction": {"awaiting_direction"},
    "voice": {"awaiting_accounting_voice"},
    "choose": {
        "awaiting_account",
        "awaiting_transaction_type",
        "awaiting_unit",
        "awaiting_category",
    },
    "no_category": {"awaiting_category"},
    "no_notes": {"awaiting_notes"},
    "more_types": {"awaiting_transaction_type"},
}
_STALE_CHOICE_TEXT = texts.STALE_BUTTON
_BUSY_TEXT = texts.BUSY


_MANUAL_DEFAULT_DESCRIPTION = texts.MANUAL_DESCRIPTION
_MANUAL_DEFAULT_AMOUNT = "0.10"
_TOP_TYPES_COUNT = 6
_ANALYSIS_PROGRESS_INTERVAL_SECONDS = 30
_FORM_STATES = {"awaiting_receipt_data", "awaiting_edit"}
_NOTES_MAX_LENGTH = 500
_AMOUNT_MAX_DIGITS = 7
_CALENDAR_BLANK = "·"
_FORM_KEYS = {"form_chat_id", "form_message_id"}
_DATE_PATTERN = re.compile(r"(\d{1,2})\s*[-/.\s]\s*(\d{1,2})\s*[-/.\s]\s*(\d{4}|\d{2})")
_ISO_DATE_PATTERN = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


def parse_italian_date(value: str) -> date:
    """Parse day-month-year dates, accepting short parts and two-digit years."""
    text = value.strip()
    iso = _ISO_DATE_PATTERN.fullmatch(text)
    if iso is not None:
        year, month, day = (int(part) for part in iso.groups())
    else:
        match = _DATE_PATTERN.fullmatch(text)
        if match is None:
            raise ValueError("Date format is invalid")
        day, month = int(match.group(1)), int(match.group(2))
        year = int(match.group(3))
        if len(match.group(3)) == 2:
            year += 2000
    return date(year, month, day)


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _calendar_rows(shown: date, today: date) -> list[list[InlineKeyboardButton]]:
    """Build an inline month grid; future days are not selectable."""
    first = _month_start(shown)
    previous = _month_start(first - timedelta(days=1))
    following = _month_start(first + timedelta(days=32))
    forward = (
        InlineKeyboardButton("▶️", callback_data=f"receipt:cal:{following:%Y-%m}")
        if following <= _month_start(today)
        else InlineKeyboardButton(_CALENDAR_BLANK, callback_data="receipt:noop")
    )
    rows = [
        [
            InlineKeyboardButton("◀️", callback_data=f"receipt:cal:{previous:%Y-%m}"),
            InlineKeyboardButton(
                f"{texts.MONTH_NAMES[first.month - 1]} {first.year}",
                callback_data="receipt:noop",
            ),
            forward,
        ],
        [
            InlineKeyboardButton(label, callback_data="receipt:noop")
            for label in texts.CALENDAR_WEEKDAYS
        ],
    ]
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(first.year, first.month):
        row = []
        for day in week:
            current = first.replace(day=day) if day else None
            if current is None or current > today:
                row.append(InlineKeyboardButton(_CALENDAR_BLANK, callback_data="receipt:noop"))
            else:
                row.append(
                    InlineKeyboardButton(
                        str(day), callback_data=f"receipt:day:{current.isoformat()}"
                    )
                )
        rows.append(row)
    return rows


def _amount_keypad_rows() -> list[list[InlineKeyboardButton]]:
    rows = [
        [InlineKeyboardButton(digit, callback_data=f"receipt:key:{digit}") for digit in group]
        for group in ("123", "456", "789")
    ]
    rows.append([
        InlineKeyboardButton(",", callback_data="receipt:key:comma"),
        InlineKeyboardButton("0", callback_data="receipt:key:0"),
        InlineKeyboardButton("⌫", callback_data="receipt:key:back"),
    ])
    rows.append([InlineKeyboardButton(texts.AMOUNT_CONFIRM_BUTTON, callback_data="receipt:key:ok")])
    return rows


def next_amount_input(current: str, key: str) -> str:
    """Apply one keypad press to the amount typed so far (Italian decimal comma)."""
    if key == "back":
        return current[:-1]
    if key == "comma":
        return current if "," in current else (current or "0") + ","
    if len(key) != 1 or not key.isdigit():
        raise ValueError("Unsupported keypad key")
    whole, separator, decimals = current.partition(",")
    if separator and len(decimals) >= 2:
        return current
    if not separator and len(whole) >= _AMOUNT_MAX_DIGITS:
        return current
    if current == "0":
        return key
    return current + key
_ANALYSIS_PHASE_TEXTS = {
    AnalysisPhase.PREPARING_IMAGE: texts.ANALYSIS_PREPARING_IMAGE,
    AnalysisPhase.LOADING_MODEL: texts.ANALYSIS_LOADING_MODEL,
    AnalysisPhase.READING_IMAGE: texts.ANALYSIS_READING_IMAGE,
    AnalysisPhase.WRITING: texts.ANALYSIS_RECEIVING,
}


def _analysis_progress_text(progress: AnalysisProgress | None, seconds: int) -> str:
    template = (
        texts.ANALYSIS_WAITING if progress is None else _ANALYSIS_PHASE_TEXTS[progress.phase]
    )
    characters = progress.characters if progress is not None else 0
    return template.format(seconds=seconds, characters=characters)


class ReceiptAnalyzer(Protocol):
    async def analyze(self, file_bytes: bytes, file_name: str) -> ReceiptData:
        """Analyze a receipt using the configured local vision model."""


@runtime_checkable
class ProgressReceiptAnalyzer(Protocol):
    async def analyze_with_progress(
        self,
        file_bytes: bytes,
        file_name: str,
        *,
        on_progress: Callable[[AnalysisProgress], None],
    ) -> ReceiptData:
        """Analyze a receipt and report the current analysis phase."""


class ReceiptWorkflow:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        analyzer: ReceiptAnalyzer | None,
        pending_store: PendingReceiptStore,
        archive: FileArchive,
        bilancio_client: BilancioClient | None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._analyzer = analyzer
        self._pending_store = pending_store
        self._archive = archive
        self._bilancio = bilancio_client
        self._busy_users: set[int] = set()

    async def start_manual_expense(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        collect_data: bool = True,
        direction: TransactionDirection | None = TransactionDirection.INCOME,
    ) -> None:
        message = getattr(update, "effective_message", None) or update.message
        user = update.effective_user
        if message is None or user is None:
            return
        if (
            user.id != self._settings.admin_user_id
            and direction != TransactionDirection.INCOME
        ):
            await message.reply_text(texts.ADMIN_ONLY)
            return
        if self._bilancio is None:
            await message.reply_text(
                texts.PORTAL_UNAVAILABLE
            )
            return
        if user.id in self._busy_users:
            await message.reply_text(_BUSY_TEXT)
            return
        previous_state = await asyncio.to_thread(
            self._database.get_conversation_state,
            user.id,
        )
        if previous_state is not None:
            if not await self._is_manual_state(previous_state[1]):
                await message.reply_text(
                    texts.MANUAL_RECEIPT_ACTIVE
                )
                return
            await self.cancel_current(user.id)
            await message.reply_text(texts.MANUAL_PREVIOUS_CANCELLED)

        expense_id = uuid.uuid4().hex
        payload: dict[str, object] = {
            "manual_entry": True,
            "dry_run": await self._current_test_mode(),
            "direction": direction.value if direction is not None else None,
            "date": date.today().isoformat(),
            "merchant": _MANUAL_DEFAULT_DESCRIPTION,
            "total_amount": _MANUAL_DEFAULT_AMOUNT,
            "currency": "EUR",
            "confidence": 1.0,
        }
        if collect_data:
            payload.update(date=None, merchant=None, total_amount=None)
        await asyncio.to_thread(
            self._database.create_manual_expense,
            expense_id=expense_id,
            telegram_user_id=user.id,
            payload=payload,
        )
        if collect_data:
            await self._show_manual_form(
                message, user.id, "awaiting_receipt_data", {"expense_id": expense_id},
                payload, "date",
            )
            return
        await self._save_state(user.id, "awaiting_confirmation", {"expense_id": expense_id})
        await message.reply_text(
            self._manual_confirmation_text(payload),
            reply_markup=self._manual_confirmation_keyboard(),
        )

    async def receive_receipt(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.message
        user = update.effective_user
        if message is None or user is None:
            return
        if await asyncio.to_thread(
            self._database.get_conversation_state, user.id
        ) is not None:
            await message.reply_text(
                texts.RECEIPT_ALREADY_ACTIVE
            )
            return

        file_id, file_name, file_size = self._message_file(message)
        if file_id is None or file_name is None:
            await message.reply_text(texts.SEND_RECEIPT)
            return
        if file_size is not None and file_size > MAX_PENDING_RECEIPT_BYTES:
            await message.reply_text(texts.FILE_TOO_LARGE)
            return

        stage = "get_file"
        try:
            telegram_file = await context.bot.get_file(
                file_id, connect_timeout=30, read_timeout=120
            )
            stage = "download"
            file_bytes = bytes(
                await telegram_file.download_as_bytearray(
                    connect_timeout=30, read_timeout=120
                )
            )
            stage = "progress_message"
            progress_message = await message.reply_text(
                texts.RECEIPT_PROGRESS, connect_timeout=30, read_timeout=30
            )
        except NetworkError as error:
            logger.warning(
                "Receipt Telegram transfer failed at %s: %s",
                stage,
                type(error).__name__,
            )
            try:
                await message.reply_text(texts.RECEIPT_TRANSFER_FAILED)
            except TelegramError as notification_error:
                logger.warning(
                    "Could not notify receipt transfer failure: %s",
                    type(notification_error).__name__,
                )
            return

        try:
            pending = await asyncio.to_thread(
                self._pending_store.save,
                file_bytes,
                file_name,
            )
        except (OSError, ValueError) as error:
            logger.warning("Could not store incoming receipt: %s", type(error).__name__)
            await message.reply_text(
                texts.FILE_UNREADABLE
            )
            return

        dry_run = await self._current_test_mode()
        created = await asyncio.to_thread(
            self._database.create_expense,
            expense_id=pending.receipt_id,
            telegram_user_id=user.id,
            sha256=pending.sha256,
            payload={
                **ReceiptData(confidence=0).model_dump(mode="json"),
                "pending_path": str(pending.path),
                "source_name": file_name,
                "dry_run": dry_run,
            },
        )
        if not created:
            await asyncio.to_thread(self._pending_store.delete, pending.path)
            await message.reply_text(
                texts.RECEIPT_DUPLICATE
            )
            return

        await self._save_state(
            user.id, "awaiting_receipt_data", {"expense_id": pending.receipt_id}
        )
        try:
            if self._analyzer is None:
                raise LocalVisionError(
                    "Receipt analyzer is not configured",
                    code=VisionFailureCode.NOT_CONFIGURED,
                )
            async with asyncio.timeout(self._settings.local_vision_timeout_seconds):
                receipt = await self._analyze_receipt(file_bytes, file_name, progress_message)
        except (LocalVisionError, ReceiptParseError, ImagePreprocessingError, TimeoutError) as error:
            code = (
                error.code if isinstance(error, LocalVisionError)
                else VisionFailureCode.TIMEOUT if isinstance(error, TimeoutError)
                else VisionFailureCode.PREPROCESSING
                if isinstance(error, ImagePreprocessingError)
                else VisionFailureCode.INVALID_RECEIPT
            )
            status = error.status_code if isinstance(error, LocalVisionError) else None
            logger.warning(
                "Receipt analysis failed: code=%s status=%s", code.value, status
            )
            data = {
                **ReceiptData(confidence=0).model_dump(mode="json"),
                "pending_path": str(pending.path),
                "source_name": file_name,
                "dry_run": dry_run,
            }
            await asyncio.to_thread(
                self._database.update_expense_payload, pending.receipt_id, data
            )
            await self._save_state(
                user.id, "awaiting_receipt_data", {"expense_id": pending.receipt_id}
            )
            reply = texts.ANALYSIS_FAILED.format(prompt=_FIELD_PROMPTS["date"])
            diagnostic = texts.ai_admin_diagnostic(
                code.value, status, self._settings.local_vision_timeout_seconds
            )
            if user.id == self._settings.admin_user_id:
                reply = texts.AI_FAILURE_WITH_DIAGNOSTIC.format(
                    message=texts.ANALYSIS_FAILED.format(prompt="").rstrip(),
                    diagnostic=diagnostic,
                    prompt=_FIELD_PROMPTS["date"],
                )
            await message.reply_text(reply, reply_markup=self._navigation_keyboard(cancel=True))
            if user.id != self._settings.admin_user_id:
                await self._notify_admin(context, diagnostic)
            return

        data = {
            **receipt.model_dump(mode="json"),
            "pending_path": str(pending.path),
            "source_name": file_name,
            "dry_run": dry_run,
        }
        await asyncio.to_thread(
            self._database.update_expense_payload,
            pending.receipt_id,
            data,
        )
        state = "awaiting_receipt_data" if missing_required_fields(receipt) else "awaiting_confirmation"
        await self._save_state(user.id, state, {"expense_id": pending.receipt_id})

        missing = missing_required_fields(receipt)
        if missing:
            await message.reply_text(
                _FIELD_PROMPTS[missing[0]], reply_markup=self._navigation_keyboard(cancel=True)
            )
            return
        await self._offer_accounts(user.id, pending.receipt_id, data, message, context)

    async def _analyze_receipt(
        self, file_bytes: bytes, file_name: str, progress_message: Message
    ) -> ReceiptData:
        if self._analyzer is None:
            raise RuntimeError("Receipt analyzer is not configured")
        started = asyncio.get_running_loop().time()
        latest: AnalysisProgress | None = None
        updated = False

        def on_progress(progress: AnalysisProgress) -> None:
            nonlocal latest
            latest = progress

        async def edit_progress(text: str) -> None:
            try:
                await progress_message.edit_text(text, connect_timeout=10, read_timeout=10)
            except TelegramError as error:
                logger.warning("Could not update analysis progress: %s", type(error).__name__)

        async def monitor() -> None:
            nonlocal updated
            while True:
                await asyncio.sleep(_ANALYSIS_PROGRESS_INTERVAL_SECONDS)
                elapsed = int(asyncio.get_running_loop().time() - started)
                updated = True
                await edit_progress(_analysis_progress_text(latest, elapsed))

        monitor_task = asyncio.create_task(monitor())
        try:
            if isinstance(self._analyzer, ProgressReceiptAnalyzer):
                return await self._analyzer.analyze_with_progress(
                    file_bytes, file_name, on_progress=on_progress
                )
            return await self._analyzer.analyze(file_bytes, file_name)
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
            if updated:
                await edit_progress(texts.ANALYSIS_FINISHED)

    async def handle_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.message
        user = update.effective_user
        if message is None or user is None or message.text is None:
            return
        saved_state = await asyncio.to_thread(
            self._database.get_conversation_state, user.id
        )
        if saved_state is None:
            return
        state, state_data = saved_state
        expense_id = state_data.get("expense_id")
        if not isinstance(expense_id, str):
            raise ValueError("Conversation state has no expense identifier")
        expense = await asyncio.to_thread(self._database.get_expense, expense_id)
        if expense is None:
            await self._clear_state(user.id)
            await message.reply_text(
                texts.STALE_BUTTON
            )
            return

        if state == "manual_entry_data":
            try:
                updated = self._parse_manual_entry(expense.payload, message.text)
            except ValueError:
                await message.reply_text(
                    texts.MANUAL_FORMAT
                )
                return
            await asyncio.to_thread(
                self._database.update_expense_payload,
                expense_id,
                updated,
            )
            await self._offer_accounts(user.id, expense_id, updated, message, context)
            return
        if self._is_manual_form(state, state_data, expense.payload):
            await self._handle_manual_form_text(
                message, context, user.id, state, state_data, expense_id, expense.payload
            )
            return
        if state == "awaiting_receipt_data":
            field_name = self._next_missing_field(expense.payload)
        elif state == "awaiting_edit":
            field_name = state_data.get("edit_field")
            if field_name not in _EDIT_FIELDS:
                raise ValueError("Conversation state contains an unsupported edit field")
        elif state == "awaiting_notes":
            notes_text = message.text.strip()
            if len(notes_text) > _NOTES_MAX_LENGTH:
                await message.reply_text(
                    texts.NOTES_TOO_LONG.format(limit=_NOTES_MAX_LENGTH),
                    reply_markup=self._navigation_keyboard(cancel=True),
                )
                return
            progress = await message.reply_text(
                self._submission_progress_text(self._uses_dry_run(expense.payload))
            )
            await self._submit_expense(
                update,
                context,
                user.id,
                expense_id,
                expense.payload,
                state_data,
                notes_text or "-",
                progress_message=progress,
            )
            return
        else:
            await message.reply_text(
                texts.USE_BUTTONS
            )
            return

        try:
            updated = self._apply_user_value(expense.payload, field_name, message.text)
        except (ValueError, TypeError):
            if expense.payload.get("manual_entry") is True:
                prompt = _MANUAL_EDIT_PROMPTS.get(field_name, self._prompt_for(field_name))
            elif state == "awaiting_receipt_data":
                prompt = _FIELD_PROMPTS[field_name]
            else:
                prompt = self._prompt_for(field_name)
            await message.reply_text(prompt, reply_markup=self._navigation_keyboard(cancel=True))
            return
        await asyncio.to_thread(
            self._database.update_expense_payload,
            expense_id,
            updated,
        )
        receipt = self._receipt_from_payload(updated)
        missing = missing_required_fields(receipt)
        if missing:
            await self._save_state(user.id, "awaiting_receipt_data", state_data)
            await message.reply_text(
                _MANUAL_EDIT_PROMPTS[missing[0]]
                if updated.get("manual_entry") is True
                else _FIELD_PROMPTS[missing[0]],
                reply_markup=self._navigation_keyboard(cancel=True),
            )
            return
        await self._offer_accounts(user.id, expense_id, updated, message, context)

    async def handle_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        query = update.callback_query
        user = update.effective_user
        if query is None or user is None or query.data is None:
            return
        action, _, value = query.data.partition(":")
        if action != "receipt":
            await query.answer()
            return
        if value == "noop":
            await query.answer()
            return
        if user.id in self._busy_users:
            await query.answer(_BUSY_TEXT)
            return
        saved_state = await asyncio.to_thread(
            self._database.get_conversation_state, user.id
        )
        if saved_state is None:
            await query.answer(texts.STALE_BUTTON)
            return
        state, state_data = saved_state
        command = value.split(":", 1)[0]
        allowed_states = _CALLBACK_STATES.get(command)
        if command != "cancel" and (allowed_states is None or state not in allowed_states):
            await query.answer(_STALE_CHOICE_TEXT)
            return
        await query.answer()
        self._busy_users.add(user.id)
        try:
            await self._dispatch_callback(
                update,
                context,
                query,
                user.id,
                state,
                state_data,
                value,
            )
        finally:
            self._busy_users.discard(user.id)

    async def _dispatch_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        query: CallbackQuery,
        user_id: int,
        state: str,
        state_data: dict[str, object],
        value: str,
    ) -> None:
        expense_id = state_data.get("expense_id")
        if not isinstance(expense_id, str):
            raise ValueError("Conversation state has no expense identifier")
        expense = await asyncio.to_thread(self._database.get_expense, expense_id)
        if expense is None:
            await self._clear_state(user_id)
            await query.edit_message_text(
                texts.STALE_BUTTON
            )
            return

        if value.startswith("cancel"):
            await self._cancel_pending(user_id, expense_id, expense.payload)
            await query.edit_message_text(texts.CANCELLED, reply_markup=self._navigation_keyboard())
        elif value.startswith("edit:"):
            field_name = value.split(":", 1)[1]
            if field_name not in _EDIT_FIELDS:
                await query.edit_message_text(texts.STALE_BUTTON)
                return
            if self._is_manual_form(state, state_data, expense.payload) and (
                field_name in _MANUAL_EDIT_PROMPTS
            ):
                await self._edit_manual_form(
                    query, user_id, "awaiting_edit",
                    {"expense_id": expense_id, "edit_field": field_name},
                    expense.payload, field_name,
                )
                return
            await self._save_state(
                user_id,
                "awaiting_edit",
                {"expense_id": expense_id, "edit_field": field_name},
            )
            await query.edit_message_text(
                _MANUAL_EDIT_PROMPTS[field_name]
                if expense.payload.get("manual_entry") is True
                and field_name in _MANUAL_EDIT_PROMPTS
                else self._prompt_for(field_name),
                reply_markup=self._navigation_keyboard(cancel=True),
            )
        elif value == "today" or value.startswith("day:"):
            if not self._is_manual_form(state, state_data, expense.payload):
                await query.edit_message_text(texts.STALE_BUTTON)
                return
            try:
                chosen = (
                    date.today()
                    if value == "today"
                    else date.fromisoformat(value.split(":", 1)[1])
                )
            except ValueError:
                await query.edit_message_text(texts.STALE_BUTTON)
                return
            if chosen > date.today():
                return
            updated = {**expense.payload, "date": chosen.isoformat()}
            await self._complete_form_field(
                query, context, user_id, expense_id, updated
            )
        elif value.startswith("cal:"):
            if (
                not self._is_manual_form(state, state_data, expense.payload)
                or self._form_field(state, state_data, expense.payload) != "date"
            ):
                await query.edit_message_text(texts.STALE_BUTTON)
                return
            try:
                shown = date.fromisoformat(value.split(":", 1)[1] + "-01")
            except ValueError:
                await query.edit_message_text(texts.STALE_BUTTON)
                return
            await self._edit_manual_form(
                query, user_id, state, state_data, expense.payload, "date",
                month=min(shown, _month_start(date.today())),
            )
        elif value.startswith("key:"):
            if (
                not self._is_manual_form(state, state_data, expense.payload)
                or self._form_field(state, state_data, expense.payload) != "total_amount"
            ):
                await query.edit_message_text(texts.STALE_BUTTON)
                return
            key = value.split(":", 1)[1]
            typed = state_data.get("amount_input")
            typed = typed if isinstance(typed, str) else ""
            if key == "ok":
                try:
                    updated = self._apply_user_value(expense.payload, "total_amount", typed)
                    self._receipt_from_payload(updated)
                except (ValueError, TypeError):
                    await self._edit_manual_form(
                        query, user_id, state, state_data, expense.payload,
                        "total_amount", invalid=True,
                    )
                    return
                await self._complete_form_field(
                    query, context, user_id, expense_id, updated
                )
                return
            try:
                typed_next = next_amount_input(typed, key)
            except ValueError:
                await query.edit_message_text(texts.STALE_BUTTON)
                return
            if typed_next == typed:
                return
            await self._edit_manual_form(
                query, user_id, state, {**state_data, "amount_input": typed_next},
                expense.payload, "total_amount",
            )
        elif value == "confirm":
            if self._bilancio is None:
                await query.edit_message_text(
                    texts.PORTAL_UNAVAILABLE_RECEIPT
                )
                return
            await self._show_progress(query, texts.ACCOUNTS_PROGRESS)
            await self._begin_portal_selection(
                user_id,
                expense_id,
                expense.payload,
                query,
                context,
            )
        elif value == "no_notes":
            await self._show_progress(
                query,
                self._submission_progress_text(self._uses_dry_run(expense.payload)),
            )
            await self._submit_expense(
                update, context, user_id, expense_id, expense.payload, state_data, "-"
            )
        elif value == "manual_save":
            if expense.payload.get("manual_entry") is not True:
                await query.edit_message_text(texts.STALE_BUTTON)
                return
            notes_text = state_data.get("portal_notes")
            await self._show_progress(
                query,
                self._submission_progress_text(self._uses_dry_run(expense.payload)),
            )
            await self._submit_expense(
                update,
                context,
                user_id,
                expense_id,
                expense.payload,
                state_data,
                notes_text if isinstance(notes_text, str) else "-",
            )
        elif value.startswith("direction:"):
            await self._select_direction(
                user_id,
                expense_id,
                expense.payload,
                state,
                state_data,
                value.split(":", 1)[1],
                query,
                context,
            )
        elif value.startswith("voice:"):
            await self._select_accounting_voice(
                user_id,
                expense_id,
                expense.payload,
                state,
                state_data,
                value.split(":", 1)[1],
                query,
                context,
            )
        elif value.startswith("choose:"):
            await self._select_portal_option(
                update,
                user_id,
                expense_id,
                expense.payload,
                state,
                state_data,
                value.split(":", 1)[1],
                query,
                context,
            )
        elif value == "more_types":
            await self._show_more_types(state_data, query)
        elif value == "no_category":
            await self._complete_portal_selection(
                update,
                user_id,
                expense_id,
                expense.payload,
                state_data,
                query,
                context,
            )

    async def _show_progress(self, query: CallbackQuery, text: str) -> None:
        try:
            await query.edit_message_text(text)
        except TelegramError:
            logger.warning("Could not show Telegram progress message")

    def _uses_dry_run(self, payload: dict[str, object]) -> bool:
        value = payload.get("dry_run", self._settings.dry_run)
        if not isinstance(value, bool):
            raise ValueError("Expense mode must be a boolean")
        return value

    async def _current_test_mode(self) -> bool:
        return await asyncio.to_thread(
            self._database.get_test_mode, default=self._settings.dry_run
        )

    def _mode_text(self, payload: dict[str, object]) -> str:
        return texts.MODE_TRIAL if self._uses_dry_run(payload) else texts.MODE_REAL

    @staticmethod
    def _is_manual_form(
        state: str, state_data: dict[str, object], payload: dict[str, object]
    ) -> bool:
        if payload.get("manual_entry") is not True or state not in _FORM_STATES:
            return False
        return state != "awaiting_edit" or state_data.get("edit_field") in _MANUAL_EDIT_PROMPTS

    def _manual_form_text(
        self,
        payload: dict[str, object],
        field_name: str | None,
        *,
        invalid: bool = False,
        amount_input: object = None,
    ) -> str:
        return texts.manual_form(
            payload,
            income_only=payload.get("direction") == TransactionDirection.INCOME.value,
            prompt=_MANUAL_EDIT_PROMPTS.get(field_name) if field_name else None,
            mode=self._mode_text(payload),
            invalid=invalid,
            amount_input=(
                amount_input
                if field_name == "total_amount" and isinstance(amount_input, str)
                else None
            ),
        )

    @staticmethod
    def _manual_form_keyboard(
        field_name: str | None,
        payload: dict[str, object] | None = None,
        *,
        month: date | None = None,
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        if field_name == "date":
            today = date.today()
            if month is None:
                try:
                    current = date.fromisoformat(str((payload or {}).get("date")))
                except ValueError:
                    current = today
                month = min(current, today)
            rows.append([
                InlineKeyboardButton(texts.TODAY_BUTTON, callback_data="receipt:today"),
                InlineKeyboardButton(
                    texts.YESTERDAY_BUTTON,
                    callback_data=f"receipt:day:{(today - timedelta(days=1)).isoformat()}",
                ),
            ])
            rows.extend(_calendar_rows(month, today))
        elif field_name == "total_amount":
            rows.extend(_amount_keypad_rows())
        rows.extend([
            [
                InlineKeyboardButton(texts.DATE_BUTTON, callback_data="receipt:edit:date"),
                InlineKeyboardButton(texts.DESCRIPTION_BUTTON, callback_data="receipt:edit:merchant"),
                InlineKeyboardButton(texts.AMOUNT_BUTTON, callback_data="receipt:edit:total_amount"),
            ],
            [InlineKeyboardButton(texts.CANCEL_BUTTON, callback_data="receipt:cancel")],
        ])
        return InlineKeyboardMarkup(rows)

    def _form_field(
        self, state: str, state_data: dict[str, object], payload: dict[str, object]
    ) -> str | None:
        if state == "awaiting_edit":
            field_name = state_data.get("edit_field")
            return field_name if isinstance(field_name, str) else None
        return self._next_missing_field(payload)

    async def _complete_form_field(
        self,
        query: CallbackQuery,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
        expense_id: str,
        updated: dict[str, object],
    ) -> None:
        await asyncio.to_thread(self._database.update_expense_payload, expense_id, updated)
        missing = missing_required_fields(self._receipt_from_payload(updated))
        if missing:
            await self._edit_manual_form(
                query, user_id, "awaiting_receipt_data", {"expense_id": expense_id},
                updated, missing[0],
            )
            return
        await query.edit_message_text(
            texts.MANUAL_FORM_DONE.format(details=texts.operation_details(updated, {}))
        )
        await self._offer_accounts(user_id, expense_id, updated, query.message, context)

    @staticmethod
    def _form_data(
        state_data: dict[str, object], chat_id: object, message_id: object
    ) -> dict[str, object]:
        data = {key: value for key, value in state_data.items() if key not in _FORM_KEYS}
        if isinstance(chat_id, int) and isinstance(message_id, int):
            data.update(form_chat_id=chat_id, form_message_id=message_id)
        return data

    async def _show_manual_form(
        self,
        message: Message,
        user_id: int,
        state: str,
        state_data: dict[str, object],
        payload: dict[str, object],
        field_name: str | None,
        *,
        invalid: bool = False,
    ) -> None:
        sent = await message.reply_text(
            self._manual_form_text(
                payload, field_name, invalid=invalid,
                amount_input=state_data.get("amount_input"),
            ),
            reply_markup=self._manual_form_keyboard(field_name, payload),
        )
        await self._save_state(
            user_id,
            state,
            self._form_data(
                state_data, getattr(sent, "chat_id", None), getattr(sent, "message_id", None)
            ),
        )

    async def _edit_manual_form(
        self,
        query: CallbackQuery,
        user_id: int,
        state: str,
        state_data: dict[str, object],
        payload: dict[str, object],
        field_name: str,
        *,
        month: date | None = None,
        invalid: bool = False,
    ) -> None:
        form = query.message
        await self._save_state(
            user_id,
            state,
            self._form_data(
                state_data, getattr(form, "chat_id", None), getattr(form, "message_id", None)
            ),
        )
        try:
            await query.edit_message_text(
                self._manual_form_text(
                    payload, field_name, invalid=invalid,
                    amount_input=state_data.get("amount_input"),
                ),
                reply_markup=self._manual_form_keyboard(field_name, payload, month=month),
            )
        except BadRequest as error:
            if "not modified" not in str(error).lower():
                raise

    async def _handle_manual_form_text(
        self,
        message: Message,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
        state: str,
        state_data: dict[str, object],
        expense_id: str,
        payload: dict[str, object],
    ) -> None:
        field_name = (
            str(state_data["edit_field"])
            if state == "awaiting_edit"
            else self._next_missing_field(payload)
        )
        # Remove the typed answer and the old form so the buttons stay at the bottom.
        obsolete = [
            (getattr(message, "chat_id", None), getattr(message, "message_id", None)),
            (state_data.get("form_chat_id"), state_data.get("form_message_id")),
        ]
        try:
            updated = self._apply_user_value(payload, field_name, message.text or "")
            self._receipt_from_payload(updated)
        except (ValueError, TypeError):
            await self._show_manual_form(
                message, user_id, state, state_data, payload, field_name, invalid=True
            )
        else:
            await asyncio.to_thread(self._database.update_expense_payload, expense_id, updated)
            missing = missing_required_fields(self._receipt_from_payload(updated))
            if missing:
                await self._show_manual_form(
                    message, user_id, "awaiting_receipt_data", {"expense_id": expense_id},
                    updated, missing[0],
                )
            else:
                await self._offer_accounts(user_id, expense_id, updated, message, context)
        await self._delete_messages(context, obsolete)

    @staticmethod
    async def _delete_messages(
        context: ContextTypes.DEFAULT_TYPE, references: list[tuple[object, object]]
    ) -> None:
        bot = getattr(context, "bot", None)
        if bot is None:
            return
        for chat_id, message_id in references:
            if not isinstance(chat_id, int) or not isinstance(message_id, int):
                continue
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except TelegramError as error:
                logger.info("Could not delete Telegram message: %s", type(error).__name__)

    @staticmethod
    async def _selection_reply(
        target: CallbackQuery | Message,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup,
    ) -> None:
        if isinstance(target, Message) or hasattr(target, "reply_text"):
            await target.reply_text(text, reply_markup=reply_markup)
        else:
            await target.edit_message_text(text, reply_markup=reply_markup)

    async def _offer_accounts(
        self,
        user_id: int,
        expense_id: str,
        payload: dict[str, object],
        message: Message,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        receipt = self._receipt_from_payload(payload)
        summary = texts.receipt_confirmation(payload, receipt.confidence)
        summary += "\n\n" + self._mode_text(payload)
        await message.reply_text(summary)
        if self._bilancio is None:
            await self._save_state(user_id, "awaiting_confirmation", {"expense_id": expense_id})
            await message.reply_text(
                texts.PORTAL_UNAVAILABLE_RECEIPT,
                reply_markup=self._manual_confirmation_keyboard(),
            )
            return
        await self._begin_portal_selection(user_id, expense_id, payload, message, context)

    def _submission_progress_text(self, dry_run: bool) -> str:
        if dry_run:
            return texts.TRIAL_PROGRESS
        return (
            texts.SAVE_PROGRESS
        )

    async def _is_manual_state(self, state_data: dict[str, object]) -> bool:
        expense_id = state_data.get("expense_id")
        if not isinstance(expense_id, str):
            return True
        expense = await asyncio.to_thread(self._database.get_expense, expense_id)
        return expense is None or expense.payload.get("manual_entry") is True

    async def _portal_read_failed(
        self,
        user_id: int,
        expense_id: str,
        payload: dict[str, object],
        query: CallbackQuery | Message,
        subject: str,
    ) -> None:
        await self._save_state(
            user_id,
            "awaiting_confirmation",
            {"expense_id": expense_id},
        )
        manual = payload.get("manual_entry") is True
        await self._selection_reply(
            query,
            (texts.PORTAL_MANUAL_READ_FAILED if manual else texts.PORTAL_READ_FAILED).format(
                subject=subject
            ),
            reply_markup=(
                self._manual_confirmation_keyboard() if manual
                else self._confirmation_keyboard()
            ),
        )

    async def _begin_portal_selection(
        self,
        user_id: int,
        expense_id: str,
        payload: dict[str, object],
        query: CallbackQuery | Message,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if self._bilancio is None:
            raise RuntimeError("Bilancio client is not configured")
        transaction_date = self._payload_date(payload)
        try:
            options = await self._bilancio.list_accounts(
                transaction_date=transaction_date
            )
        except BilancioClientError as error:
            logger.error("Could not load portal accounts: %s", type(error).__name__)
            debug_path = Path(self._settings.data_path) / "debug"
            await self._notify_admin(
                context,
                texts.ADMIN_ACCOUNTS_FAILED.format(folder=debug_path),
                error,
            )
            await self._portal_read_failed(
                user_id,
                expense_id,
                payload,
                query,
                texts.ACCOUNTS_SUBJECT,
            )
            return
        if not options:
            logger.warning("The portal returned no available accounts")
            await self._portal_read_failed(
                user_id,
                expense_id,
                payload,
                query,
                texts.ACCOUNTS_SUBJECT,
            )
            return
        data: dict[str, object] = {
            "expense_id": expense_id,
            "direction": (
                payload.get("direction", TransactionDirection.INCOME.value)
                if payload.get("manual_entry") is True
                else TransactionDirection.EXPENSE.value
            ),
            "transaction_date": transaction_date.isoformat(),
            "options": self._serialize_options(options),
        }
        await self._save_state(user_id, "awaiting_account", data)
        edit_rows = [
            [
                InlineKeyboardButton(texts.EDIT_DATE_BUTTON, callback_data="receipt:edit:date"),
                InlineKeyboardButton(texts.EDIT_AMOUNT_BUTTON, callback_data="receipt:edit:total_amount"),
                InlineKeyboardButton(texts.DESCRIPTION_BUTTON, callback_data="receipt:edit:merchant"),
            ],
        ]
        if payload.get("manual_entry") is not True:
            edit_rows.append([
                InlineKeyboardButton(texts.EDIT_CATEGORY_BUTTON, callback_data="receipt:edit:suggested_category")
            ])
        edit_rows.append([InlineKeyboardButton(texts.CANCEL_BUTTON, callback_data="receipt:cancel")])
        await self._selection_reply(
            query,
            texts.ACCOUNT_PROMPT + "\n\n" + self._mode_text(payload),
            reply_markup=self._option_keyboard(options, extra_rows=edit_rows),
        )

    async def _select_direction(
        self,
        user_id: int,
        expense_id: str,
        payload: dict[str, object],
        state: str,
        state_data: dict[str, object],
        value: str,
        query: CallbackQuery,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if (
            state != "awaiting_direction"
            or state_data.get("expense_id") != expense_id
            or self._bilancio is None
        ):
            return
        if value not in {direction.value for direction in TransactionDirection}:
            await query.edit_message_text(texts.CHOOSE_DIRECTION)
            return
        stored = await asyncio.to_thread(self._database.get_expense, expense_id)
        manual = payload.get("manual_entry") is True and user_id == self._settings.admin_user_id
        if value == TransactionDirection.EXPENSE.value and not manual and (
            stored is None
            or not await asyncio.to_thread(has_receipt_file, payload, stored.archive_path)
        ):
            logger.warning("Expense direction rejected: receipt file is missing")
            await query.edit_message_text(
                texts.EXPENSE_RECEIPT_REQUIRED,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(texts.INCOME, callback_data="receipt:direction:entrata")],
                    [InlineKeyboardButton(texts.CANCEL_BUTTON, callback_data="receipt:cancel")],
                ]),
            )
            return
        state_data["direction"] = value
        await self._save_state(user_id, "awaiting_accounting_voice", state_data)
        await self._select_accounting_voice(
            user_id,
            expense_id,
            payload,
            "awaiting_accounting_voice",
            state_data,
            "Voci Scout",
            query,
            context,
        )

    async def _select_accounting_voice(
        self,
        user_id: int,
        expense_id: str,
        payload: dict[str, object],
        state: str,
        state_data: dict[str, object],
        accounting_voice: str,
        query: CallbackQuery,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if (
            state != "awaiting_accounting_voice"
            or state_data.get("expense_id") != expense_id
            or self._bilancio is None
        ):
            await query.edit_message_text(texts.STALE_BUTTON)
            return
        if accounting_voice not in {"Voci Scout", "Voci Ministero"}:
            await query.edit_message_text(texts.STALE_BUTTON)
            return
        direction_value = state_data.get("direction")
        if not isinstance(direction_value, str):
            await query.edit_message_text(texts.CHOOSE_DIRECTION)
            return
        account = self._option_from(state_data, "account")
        direction = TransactionDirection(direction_value)
        transaction_date = self._state_date(state_data)
        await self._show_progress(
            query,
            texts.TYPE_PROGRESS,
        )
        try:
            options = await self._bilancio.list_transaction_types(
                account,
                direction,
                transaction_date=transaction_date,
                accounting_voice=accounting_voice,
            )
        except BilancioClientError as error:
            logger.error("Could not load portal transaction types: %s", type(error).__name__)
            debug_path = Path(self._settings.data_path) / "debug"
            await self._notify_admin(
                context,
                texts.ADMIN_TYPES_FAILED.format(folder=debug_path),
                error,
            )
            await self._portal_read_failed(
                user_id,
                expense_id,
                payload,
                query,
                texts.TYPES_SUBJECT,
            )
            return
        if not options:
            await self._portal_read_failed(
                user_id,
                expense_id,
                payload,
                query,
                texts.TYPES_SUBJECT,
            )
            return
        state_data.update(
            accounting_voice=accounting_voice,
            options=self._serialize_options(options),
        )
        usage = await asyncio.to_thread(
            self._database.transaction_type_usage,
            account.label,
            direction.value,
        )
        top_indices = self._top_type_indices(options, usage)
        state_data["top_indices"] = top_indices
        await self._save_state(user_id, "awaiting_transaction_type", state_data)
        hidden = len(options) - len(top_indices)
        title = (
            texts.TYPE_POPULAR
            if hidden
            else texts.TYPE_ALL
        )
        extra_rows = (
            [[InlineKeyboardButton(
                texts.MORE_TYPES_BUTTON.format(count=hidden),
                callback_data="receipt:more_types",
            )]]
            if hidden
            else []
        )
        await query.edit_message_text(
            title,
            reply_markup=self._option_keyboard(
                options,
                indices=top_indices,
                extra_rows=extra_rows,
            ),
        )

    @staticmethod
    def _top_type_indices(
        options: list[PortalOption] | tuple[PortalOption, ...],
        usage: dict[str, int],
    ) -> list[int]:
        if len(options) <= _TOP_TYPES_COUNT + 1:
            return list(range(len(options)))
        used = sorted(
            (index for index, option in enumerate(options) if usage.get(option.label)),
            key=lambda index: (-usage[options[index].label], index),
        )[:_TOP_TYPES_COUNT]
        for index in range(len(options)):
            if len(used) >= _TOP_TYPES_COUNT:
                break
            if index not in used:
                used.append(index)
        return used

    async def _show_more_types(
        self,
        state_data: dict[str, object],
        query: CallbackQuery,
    ) -> None:
        options = self._deserialize_options(state_data.get("options"))
        top = state_data.get("top_indices")
        shown = set(top) if isinstance(top, list) else set()
        await query.edit_message_text(
            texts.TYPE_MORE,
            reply_markup=self._option_keyboard(
                options,
                indices=[index for index in range(len(options)) if index not in shown],
                extra_rows=[[InlineKeyboardButton(texts.CANCEL_BUTTON, callback_data="receipt:cancel")]],
            ),
        )

    async def _select_portal_option(
        self,
        update: Update,
        user_id: int,
        expense_id: str,
        payload: dict[str, object],
        state: str,
        state_data: dict[str, object],
        index_text: str,
        query: CallbackQuery,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if self._bilancio is None or state_data.get("expense_id") != expense_id:
            return
        if state in {"awaiting_unit", "awaiting_category"}:
            await self._complete_portal_selection(
                update,
                user_id,
                expense_id,
                payload,
                state_data,
                query,
                context,
            )
            return
        try:
            index = int(index_text)
            options = self._deserialize_options(state_data.get("options"))
            if index < 0:
                raise IndexError
            selected = options[index]
        except (TypeError, ValueError, IndexError):
            await query.edit_message_text(texts.STALE_BUTTON)
            return
        account = self._option_from(state_data, "account") if state != "awaiting_account" else selected
        direction_value = state_data.get("direction")
        direction = (
            TransactionDirection(direction_value)
            if isinstance(direction_value, str)
            else None
        )

        if state == "awaiting_account":
            state_data.update(account=self._serialize_option(selected))
            await self._save_state(user_id, "awaiting_direction", state_data)
            if direction is not None:
                await self._select_direction(
                    user_id, expense_id, payload, "awaiting_direction",
                    state_data, direction.value, query, context,
                )
                return
            stored = await asyncio.to_thread(self._database.get_expense, expense_id)
            has_receipt = stored is not None and await asyncio.to_thread(
                has_receipt_file, payload, stored.archive_path
            )
            directions = [
                InlineKeyboardButton(texts.INCOME, callback_data="receipt:direction:entrata")
            ]
            manual = payload.get("manual_entry") is True and user_id == self._settings.admin_user_id
            if has_receipt or manual:
                directions.append(
                    InlineKeyboardButton(texts.EXPENSE, callback_data="receipt:direction:uscita")
                )
            keyboard = InlineKeyboardMarkup(
                [
                    directions,
                    [InlineKeyboardButton(texts.CANCEL_BUTTON, callback_data="receipt:cancel")],
                ]
            )
            await query.edit_message_text(
                texts.DIRECTION_PROMPT if has_receipt or manual else texts.INCOME_ONLY_PROMPT,
                reply_markup=keyboard,
            )
            return
        if direction is None:
            await query.edit_message_text(texts.CHOOSE_DIRECTION)
            return
        if state != "awaiting_transaction_type":
            await query.edit_message_text(texts.STALE_BUTTON)
            return

        state_data.update(transaction_type=self._serialize_option(selected))
        unit = default_unit_for_account(account)
        state_data.update(
            unit=self._serialize_option(unit) if unit is not None else None,
            category=None,
            portal_notes=None,
        )
        await self._ask_for_notes(user_id, payload, state_data, query)

    async def _complete_portal_selection(
        self,
        update: Update,
        user_id: int,
        expense_id: str,
        payload: dict[str, object],
        state_data: dict[str, object],
        query: CallbackQuery,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        account = self._option_from(state_data, "account")
        unit = default_unit_for_account(account)
        state_data.update(
            unit=self._serialize_option(unit) if unit is not None else None,
            category=None,
            portal_notes=None,
        )
        await self._ask_for_notes(user_id, payload, state_data, query)

    async def _submit_expense(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
        expense_id: str,
        payload: dict[str, object],
        state_data: dict[str, object],
        notes_text: str,
        *,
        progress_message: Message | None = None,
    ) -> None:
        message = getattr(update, "effective_message", None) or update.message
        if message is None:
            return
        query = getattr(update, "callback_query", None)

        async def send(text: str) -> None:
            # Replace the "⏳" progress message so the outcome takes its place.
            if query is not None:
                try:
                    await query.edit_message_text(text, reply_markup=self._navigation_keyboard())
                    return
                except TelegramError:
                    logger.warning("Could not replace Telegram progress message")
            elif progress_message is not None and hasattr(progress_message, "edit_text"):
                try:
                    await progress_message.edit_text(text, reply_markup=self._navigation_keyboard())
                    return
                except TelegramError:
                    logger.warning("Could not replace Telegram progress message")
            await message.reply_text(text, reply_markup=self._navigation_keyboard())

        if self._bilancio is None:
            await send(
                texts.PORTAL_UNAVAILABLE_RECEIPT
            )
            return
        try:
            receipt = self._receipt_from_payload(payload)
            account = self._option_from(state_data, "account")
            transaction_type = self._option_from(state_data, "transaction_type")
            unit_data = state_data.get("unit")
            unit = (
                PortalOption(**unit_data)
                if isinstance(unit_data, dict)
                else default_unit_for_account(account)
            )
            category_data = state_data.get("category")
            category = (
                PortalOption(**category_data)
                if isinstance(category_data, dict)
                else None
            )
            direction_value = state_data.get("direction")
            if not isinstance(direction_value, str):
                raise ValueError("Transaction direction is missing")
            direction = TransactionDirection(direction_value)
            if receipt.date is None or receipt.total_amount is None or receipt.merchant is None:
                raise ValueError("Required receipt fields are missing")

            expense = await asyncio.to_thread(self._database.get_expense, expense_id)
            if expense is None:
                raise KeyError("Expense no longer exists")
            if (
                payload.get("manual_entry") is True
                and user_id != self._settings.admin_user_id
                and direction != TransactionDirection.INCOME
            ):
                logger.warning("Manual submission rejected: administrator required")
                await send(texts.ADMIN_ONLY)
                return
            if (
                direction == TransactionDirection.EXPENSE
                and payload.get("manual_entry") is not True
                and not await asyncio.to_thread(
                    has_receipt_file, payload, expense.archive_path
                )
            ):
                logger.warning("Expense submission rejected: receipt file is missing")
                await asyncio.to_thread(
                    self._database.update_expense_status, expense_id, "failed"
                )
                await self._clear_state(user_id)
                await send(texts.EXPENSE_RECEIPT_REQUIRED)
                return
            if payload.get("manual_entry") is True and not isinstance(payload.get("pending_path"), str):
                await asyncio.to_thread(
                    self._database.update_expense_status,
                    expense_id,
                    "confirmed",
                )
            elif expense.archive_path is None:
                archived = await asyncio.to_thread(
                    self._archive.archive,
                    Path(str(payload["pending_path"])),
                    transaction_date=receipt.date,
                    merchant=receipt.merchant,
                    account=account.label,
                    amount=receipt.total_amount,
                )
                await asyncio.to_thread(
                    self._database.update_expense_status,
                    expense_id,
                    "confirmed",
                    str(archived.path),
                )

            portal_expense = PortalExpense(
                transaction_date=receipt.date,
                description=receipt.merchant,
                amount=receipt.total_amount,
                direction=direction,
                account=account,
                transaction_type=transaction_type,
                unit=unit,
                category=category,
                notes=None if notes_text == "-" else notes_text,
                accounting_voice=str(state_data.get("accounting_voice", "Voci Scout")),
            )
            payload.update(
                dry_run=self._uses_dry_run(payload),
                account=self._serialize_option(account),
                direction=direction.value,
                transaction_type=self._serialize_option(transaction_type),
                unit=self._serialize_option(unit) if unit is not None else None,
                category=(
                    self._serialize_option(category)
                    if category is not None
                    else None
                ),
                accounting_voice=portal_expense.accounting_voice,
                portal_notes=portal_expense.notes,
            )
            state_data["portal_notes"] = portal_expense.notes
            await asyncio.to_thread(
                self._database.update_expense_payload,
                expense_id,
                payload,
            )
            dry_run = self._uses_dry_run(payload)
            result = await self._bilancio.create_expense(
                portal_expense,
                dry_run=dry_run,
            )
            if not result.verified:
                raise BilancioClientError("Portal result could not be verified")
            if dry_run and not result.dry_run:
                await asyncio.to_thread(
                    self._database.update_expense_status,
                    expense_id,
                    "failed",
                )
                await self._clear_state(user_id)
                await send(
                    texts.TRIAL_UNCONFIRMED
                )
                return
        except (BilancioClientError, OSError, ValueError, KeyError) as error:
            logger.error("Expense submission failed: %s", type(error).__name__)
            debug_path = Path(self._settings.data_path) / "debug"
            if payload.get("manual_entry") is True:
                await asyncio.to_thread(
                    self._database.update_expense_status,
                    expense_id,
                    "failed",
                )
                await self._clear_state(user_id)
                await send(
                    texts.MANUAL_FAILED.format(details=self._operation_details(payload, state_data))
                )
                await self._notify_admin(
                    context,
                    texts.DIAGNOSTIC_DETAILS.format(path=debug_path),
                    error if isinstance(error, BilancioClientError) else None,
                )
                return
            await self._notify_admin(
                context,
                texts.ADMIN_UNVERIFIED.format(folder=debug_path),
                error if isinstance(error, BilancioClientError) else None,
            )
            next_attempt = (
                datetime.now(timezone.utc) + timedelta(minutes=5)
            ).isoformat(timespec="seconds")
            queued = False
            try:
                await asyncio.to_thread(
                    self._database.enqueue_retry,
                    expense_id,
                    next_attempt_at=next_attempt,
                    error_code="portal_submission_failed",
                )
                queued = True
            except (KeyError, ValueError):
                logger.error("Could not queue failed expense")
                await asyncio.to_thread(
                    self._database.update_expense_status,
                    expense_id,
                    "failed",
                )
            if queued:
                await self._clear_state(user_id)
                stored = await asyncio.to_thread(
                    self._database.get_expense,
                    expense_id,
                )
                pending_path = payload.get("pending_path")
                if (
                    stored is not None
                    and stored.archive_path is not None
                    and isinstance(pending_path, str)
                ):
                    await asyncio.to_thread(
                        self._pending_store.delete,
                        pending_path,
                    )
                await send(
                    texts.SUBMISSION_QUEUED
                )
            else:
                await self._clear_state(user_id)
                await send(
                    texts.SUBMISSION_FAILED
                )
            return

        status = "dry_run" if result.dry_run else "submitted"
        await asyncio.to_thread(
            self._database.update_expense_status,
            expense_id,
            status,
        )
        await asyncio.to_thread(self._database.remove_retry, expense_id)
        pending_path = payload.get("pending_path")
        if isinstance(pending_path, str):
            await asyncio.to_thread(self._pending_store.delete, pending_path)
        await self._clear_state(user_id)
        details = self._operation_details(
            payload,
            state_data,
            unit_left_empty=result.unit_left_empty,
        )
        already_present = result.confirmation_id == "existing-entry"
        if result.dry_run:
            title = texts.TRIAL_SAVED
        elif already_present:
            title = (
                texts.ALREADY_PRESENT
            )
        else:
            title = texts.RECEIPT_SAVED
        await send(f"{title}\n\n{details}")

    async def _ask_for_notes(
        self,
        user_id: int,
        payload: dict[str, object],
        state_data: dict[str, object],
        query: CallbackQuery,
    ) -> None:
        await self._save_state(user_id, "awaiting_notes", state_data)
        await query.edit_message_text(
            texts.NOTES_PROMPT.format(
                details=self._operation_details(payload, state_data),
                mode=self._mode_text(payload),
            ),
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton(texts.NOTES_SKIP_BUTTON, callback_data="receipt:no_notes")],
                    [InlineKeyboardButton(texts.CANCEL_BUTTON, callback_data="receipt:cancel")],
                ]
            ),
        )

    async def _notify_admin(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        message: str,
        error: BilancioClientError | None = None,
    ) -> None:
        diagnostic_path = error.diagnostic_path if error is not None else None
        if diagnostic_path:
            message = texts.ADMIN_DIAGNOSTIC.format(message=message, filename=Path(diagnostic_path).name)
        try:
            await context.bot.send_message(
                chat_id=self._settings.admin_user_id,
                text=message,
            )
        except TelegramError:
            logger.error("Could not send Telegram admin portal notification")

    async def _save_state(
        self,
        user_id: int,
        state: str,
        data: dict[str, object],
    ) -> None:
        await asyncio.to_thread(
            self._database.save_conversation_state,
            user_id,
            state,
            data,
        )

    async def _clear_state(self, user_id: int) -> None:
        await asyncio.to_thread(self._database.delete_conversation_state, user_id)

    async def cancel_current(self, user_id: int) -> bool:
        saved_state = await asyncio.to_thread(
            self._database.get_conversation_state,
            user_id,
        )
        if saved_state is None:
            return False
        _, state_data = saved_state
        expense_id = state_data.get("expense_id")
        if isinstance(expense_id, str):
            expense = await asyncio.to_thread(
                self._database.get_expense,
                expense_id,
            )
            if expense is not None:
                await self._cancel_pending(user_id, expense_id, expense.payload)
                return True
        await self._clear_state(user_id)
        return True

    async def _cancel_pending(
        self,
        user_id: int,
        expense_id: str,
        payload: dict[str, object],
    ) -> None:
        pending_path = payload.get("pending_path")
        if isinstance(pending_path, str):
            await asyncio.to_thread(self._pending_store.delete, pending_path)
        deleted = await asyncio.to_thread(self._database.delete_expense, expense_id)
        if not deleted:
            current = await asyncio.to_thread(
                self._database.get_expense,
                expense_id,
            )
            if current is not None and current.status in {
                "confirmed",
                "queued",
                "failed",
                "cancelled",
            }:
                await asyncio.to_thread(
                    self._database.update_expense_status,
                    expense_id,
                    "cancelled",
                )
                await asyncio.to_thread(self._database.remove_retry, expense_id)
        await self._clear_state(user_id)

    @staticmethod
    def _message_file(message: object) -> tuple[str | None, str | None, int | None]:
        photo = getattr(message, "photo", None)
        if photo:
            largest = photo[-1]
            return largest.file_id, "receipt.jpg", largest.file_size
        document = getattr(message, "document", None)
        if document is not None:
            if document.mime_type != "application/pdf":
                return None, None, document.file_size
            filename = document.file_name or "receipt.pdf"
            if Path(filename).suffix.lower() != ".pdf":
                return None, None, document.file_size
            return document.file_id, filename, document.file_size
        return None, None, None

    @classmethod
    def _parse_manual_entry(
        cls,
        payload: dict[str, object],
        raw_value: str,
    ) -> dict[str, object]:
        parts = [part.strip() for part in raw_value.split("|")]
        if len(parts) != 3:
            raise ValueError("Manual expense needs date, description, and amount")
        updated = dict(payload)
        for field_name, value in zip(
            ("date", "merchant", "total_amount"),
            parts,
            strict=True,
        ):
            updated = cls._apply_user_value(updated, field_name, value)
        updated.update(
            currency="EUR",
            confidence=1.0,
            manual_entry=True,
        )
        cls._receipt_from_payload(updated)
        return updated

    def _manual_confirmation_text(self, payload: dict[str, object]) -> str:
        return texts.MANUAL_CONFIRMATION.format(details=texts.operation_details(payload, {}))

    @staticmethod
    def _navigation_keyboard(*, cancel: bool = False) -> InlineKeyboardMarkup | None:
        if not cancel:
            return None
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton(texts.CANCEL_BUTTON, callback_data="receipt:cancel")]]
        )

    @staticmethod
    def _manual_confirmation_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(texts.MANUAL_CONFIRM_BUTTON, callback_data="receipt:confirm")],
                [
                    InlineKeyboardButton(texts.DATE_BUTTON, callback_data="receipt:edit:date"),
                    InlineKeyboardButton(texts.AMOUNT_BUTTON, callback_data="receipt:edit:total_amount"),
                ],
                [InlineKeyboardButton(texts.DESCRIPTION_BUTTON, callback_data="receipt:edit:merchant")],
                [InlineKeyboardButton(texts.CANCEL_BUTTON, callback_data="receipt:cancel")],
            ]
        )

    @staticmethod
    def _operation_details(
        payload: dict[str, object],
        state_data: dict[str, object],
        *,
        unit_left_empty: bool = False,
    ) -> str:
        return texts.operation_details(payload, state_data, unit_left_empty=unit_left_empty)

    @staticmethod
    def _confirmation_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(texts.RECEIPT_CONFIRM_BUTTON, callback_data="receipt:confirm")],
                [
                    InlineKeyboardButton(texts.EDIT_DATE_BUTTON, callback_data="receipt:edit:date"),
                    InlineKeyboardButton(texts.EDIT_AMOUNT_BUTTON, callback_data="receipt:edit:total_amount"),
                ],
                [
                    InlineKeyboardButton(texts.EDIT_MERCHANT_BUTTON, callback_data="receipt:edit:merchant"),
                    InlineKeyboardButton(texts.EDIT_CATEGORY_BUTTON, callback_data="receipt:edit:suggested_category"),
                ],
                [InlineKeyboardButton(texts.CANCEL_BUTTON, callback_data="receipt:cancel")],
            ]
        )

    @staticmethod
    def _option_keyboard(
        options: list[PortalOption] | tuple[PortalOption, ...],
        *,
        include_no_category: bool = False,
        indices: list[int] | None = None,
        extra_rows: list[list[InlineKeyboardButton]] | None = None,
    ) -> InlineKeyboardMarkup:
        selected = indices if indices is not None else range(len(options))
        rows = [
            [InlineKeyboardButton(options[index].label[:60], callback_data=f"receipt:choose:{index}")]
            for index in selected
        ]
        rows.extend(extra_rows or [])
        if include_no_category:
            rows.append(
                [
                    InlineKeyboardButton(
                        texts.NO_CATEGORY_BUTTON,
                        callback_data="receipt:no_category",
                    )
                ]
            )
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def _serialize_option(option: PortalOption) -> dict[str, str]:
        return {"value": option.value, "label": option.label}

    @classmethod
    def _serialize_options(
        cls,
        options: list[PortalOption] | tuple[PortalOption, ...],
    ) -> list[dict[str, str]]:
        return [cls._serialize_option(option) for option in options]

    @staticmethod
    def _deserialize_options(value: object) -> list[PortalOption]:
        if not isinstance(value, list):
            raise ValueError("Portal options are missing")
        options: list[PortalOption] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("Portal option is invalid")
            options.append(PortalOption(**item))
        return options

    @staticmethod
    def _option_from(data: dict[str, object], key: str) -> PortalOption:
        value = data.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"Portal selection is missing: {key}")
        return PortalOption(**value)

    @staticmethod
    def _payload_date(payload: dict[str, object]) -> date:
        value = payload.get("date")
        if not isinstance(value, str):
            raise ValueError("Expense payload has no transaction date")
        return date.fromisoformat(value)

    @classmethod
    def _state_date(cls, state_data: dict[str, object]) -> date:
        value = state_data.get("transaction_date")
        if not isinstance(value, str):
            raise ValueError("Conversation state has no transaction date")
        return date.fromisoformat(value)

    @staticmethod
    def _next_missing_field(payload: dict[str, object]) -> str:
        for field_name in ("date", "merchant", "total_amount"):
            if payload.get(field_name) is None:
                return field_name
        return "total_amount"

    @staticmethod
    def _receipt_from_payload(payload: dict[str, object]) -> ReceiptData:
        receipt_fields = {
            field_name: payload[field_name]
            for field_name in ReceiptData.model_fields
            if field_name in payload
        }
        return ReceiptData.model_validate(receipt_fields)

    @staticmethod
    def _prompt_for(field_name: str) -> str:
        return _EDIT_PROMPTS[field_name]

    @staticmethod
    def _apply_user_value(
        payload: dict[str, object],
        field_name: str,
        raw_value: str,
    ) -> dict[str, object]:
        value = raw_value.strip()
        if not value:
            raise ValueError("A value is required")
        updated = dict(payload)
        if field_name == "date":
            updated[field_name] = parse_italian_date(value).isoformat()
            return updated
        if field_name == "total_amount":
            normalized = value.replace(" ", "")
            if "," in normalized:
                normalized = normalized.replace(".", "").replace(",", ".")
            try:
                amount = Decimal(normalized)
            except InvalidOperation as error:
                raise ValueError("Amount is invalid") from error
            if not amount.is_finite() or amount <= 0:
                raise ValueError("Amount must be positive")
            updated[field_name] = str(amount)
            return updated
        if field_name == "suggested_category" and value == "-":
            updated[field_name] = None
            return updated
        updated[field_name] = value
        return updated
