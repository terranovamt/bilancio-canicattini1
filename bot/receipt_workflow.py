from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from collections.abc import Awaitable, Callable
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
    scout_year_label,
)
from bot import messages as texts
from config import Settings
from db.database import Database
from storage.archive import FileArchive
from storage.pending import MAX_PENDING_RECEIPT_BYTES, PendingReceiptStore, has_receipt_file
from vision.gemini_client import (
    AnalysisPhase,
    AnalysisProgress,
    GeminiReceiptAnalyzer,
    LocalVisionError,
    VisionFailureCode,
)
from vision.preprocess import ImagePreprocessingError
from vision.receipt_schema import (
    ReceiptData,
    ReceiptParseError,
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
}
_EDIT_FIELDS = {"date", "merchant", "total_amount"}
_MANUAL_EDIT_PROMPTS = {
    "date": texts.MANUAL_DATE,
    "merchant": texts.MANUAL_MERCHANT,
    "total_amount": texts.MANUAL_AMOUNT,
}
_TEXT_EDIT_FIELDS = ("date", "total_amount", "merchant")
_CALLBACK_STATES = {
    "confirm": {"awaiting_confirmation"},
    "edit": {"awaiting_confirmation", "awaiting_final_edit"},
    "save": {"awaiting_final_confirmation"},
    "modify": {"awaiting_final_confirmation"},
    "back": {
        "awaiting_edit",
        "awaiting_final_edit",
        "awaiting_notes",
        "awaiting_account",
        "awaiting_direction",
        "awaiting_transaction_type",
    },
    "clear_notes": {"awaiting_notes"},
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
    # Legacy note prompt of older versions: it now only returns to the final summary.
    "no_notes": {"awaiting_notes"},
    "more_types": {"awaiting_transaction_type"},
}
_SUMMARY_REFERENCE_COMMANDS = {"save", "modify", "no_notes"}
_FORM_REFERENCE_COMMANDS = {"confirm", "edit", "back", "clear_notes"}
# Keys describing the current step only; never part of the final selection snapshot.
_TRANSIENT_KEYS = (
    "final_edit",
    "edit_field",
    "return_to",
    "options",
    "top_indices",
    "input_field",
    "missing_fields",
    "review",
)
_STALE_CHOICE_TEXT = texts.STALE_BUTTON
_BUSY_TEXT = texts.BUSY


_MANUAL_DEFAULT_DESCRIPTION = texts.MANUAL_DESCRIPTION
_MANUAL_DEFAULT_AMOUNT = "0.10"
_TOP_TYPES_COUNT = 6
_ANALYSIS_PROGRESS_INTERVAL_SECONDS = 30
_NOTES_MAX_LENGTH = 500
_FORM_KEYS = {"form_chat_id", "form_message_id"}
_INPUT_FIELDS = ("merchant", "date", "total_amount")
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
    rendered = template.format(seconds=seconds, characters=characters)
    if progress is not None and progress.model:
        rendered += texts.GEMINI_MODEL_PROGRESS.format(model=progress.model)
    return rendered


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
                payload, "merchant",
            )
            return
        await self._show_initial_confirmation(message, user.id, expense_id, payload)

    async def receive_receipt(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        on_pending: Callable[[], Awaitable[None]] | None = None,
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
        if on_pending is not None:
            await on_pending()
        try:
            if self._analyzer is None:
                raise LocalVisionError(
                    "Receipt analyzer is not configured",
                    code=VisionFailureCode.NOT_CONFIGURED,
                )
            # Gemini owns its total budget so it can recover a validated partial result.
            timeout = None if isinstance(self._analyzer, GeminiReceiptAnalyzer) else self._settings.ai_timeout_seconds
            async with asyncio.timeout(timeout):
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
            reply = texts.ANALYSIS_FAILED.format(prompt="")
            diagnostic = texts.ai_admin_diagnostic(
                code.value, status, self._settings.ai_timeout_seconds
            )
            if user.id == self._settings.admin_user_id:
                reply = texts.AI_FAILURE_WITH_DIAGNOSTIC.format(
                    message=texts.ANALYSIS_FAILED.format(prompt="").rstrip(),
                    diagnostic=diagnostic,
                    prompt="",
                )
            await message.reply_text(reply, reply_markup=self._navigation_keyboard(cancel=True))
            await self._show_manual_form(
                message, user.id, "awaiting_receipt_data",
                {"expense_id": pending.receipt_id}, data, "merchant",
            )
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
        # Analysis values are proposals: only missing fields are asked as text, then
        # all values are shown together for one confirmation or a targeted correction.
        missing = [name for name in _INPUT_FIELDS if data.get(name) is None]
        if missing:
            await self._show_manual_form(
                message, user.id, "awaiting_receipt_data",
                {"expense_id": pending.receipt_id, "missing_fields": missing, "review": True},
                data, missing[0],
            )
            return
        await self._show_initial_confirmation(message, user.id, pending.receipt_id, data)

    async def _analyze_receipt(
        self, file_bytes: bytes, file_name: str, progress_message: Message
    ) -> ReceiptData:
        if self._analyzer is None:
            raise RuntimeError("Receipt analyzer is not configured")
        started = asyncio.get_running_loop().time()
        latest: AnalysisProgress | None = None
        updated = False
        notices: list[str] = []

        def on_progress(progress: AnalysisProgress) -> None:
            nonlocal latest
            latest = progress
            if progress.notice is not None:
                notice = texts.gemini_notice(
                    progress.notice, progress.model, progress.retry_at, self._settings.timezone
                )
                if notice not in notices:
                    notices.append(notice)

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
            if notices:
                try:
                    chunk = ""
                    for notice in notices:
                        if chunk and len(chunk) + len(notice) + 1 > 3800:
                            await progress_message.reply_text(chunk, connect_timeout=10, read_timeout=10)
                            chunk = ""
                        chunk = f"{chunk}\n{notice}" if chunk else notice
                    await progress_message.reply_text(chunk, connect_timeout=10, read_timeout=10)
                except TelegramError as error:
                    logger.warning("Could not send Gemini analysis notices: %s", type(error).__name__)

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
        if state == "awaiting_receipt_data":
            await self._handle_missing_field_text(
                message, context, user.id, state_data, expense_id, expense.payload
            )
        elif state == "awaiting_edit":
            await self._handle_edit_text(
                message, context, user.id, state_data, expense_id, expense.payload
            )
        elif state == "awaiting_notes":
            await self._handle_note_text(message, user.id, state_data, expense.payload)
        elif state == "submitting":
            await message.reply_text(_BUSY_TEXT)
        else:
            await message.reply_text(texts.USE_BUTTONS)

    async def _handle_missing_field_text(
        self,
        message: Message,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
        state_data: dict[str, object],
        expense_id: str,
        payload: dict[str, object],
    ) -> None:
        field_name = self._form_field("awaiting_receipt_data", state_data, payload)
        if field_name not in _INPUT_FIELDS:
            raise ValueError("Unsupported text field")
        try:
            updated = self._apply_user_value(payload, field_name, message.text or "")
            self._receipt_from_payload(updated)
        except (ValueError, TypeError):
            await self._show_manual_form(
                message, user_id, "awaiting_receipt_data", state_data, payload,
                field_name, invalid=True,
            )
            return
        await asyncio.to_thread(self._database.update_expense_payload, expense_id, updated)
        missing = state_data.get("missing_fields")
        requested = missing if isinstance(missing, list) else list(_INPUT_FIELDS)
        remaining = [
            name for name in _INPUT_FIELDS
            if name in requested and name != field_name and updated.get(name) is None
        ]
        review = state_data.get("review") is True
        if remaining:
            await self._show_manual_form(
                message, user_id, "awaiting_receipt_data",
                {"expense_id": expense_id, "missing_fields": remaining, "review": review},
                updated, remaining[0],
            )
            return
        if review:
            await self._show_initial_confirmation(message, user_id, expense_id, updated)
            return
        await self._offer_accounts(user_id, expense_id, updated, message, context)

    async def _handle_edit_text(
        self,
        message: Message,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
        state_data: dict[str, object],
        expense_id: str,
        payload: dict[str, object],
    ) -> None:
        field_name = state_data.get("edit_field")
        if not isinstance(field_name, str) or field_name not in _EDIT_FIELDS:
            # Legacy states (e.g. the removed AI category field) cannot be edited anymore.
            logger.warning("Ignoring unsupported edit field in conversation state")
            await message.reply_text(texts.STALE_BUTTON)
            return
        try:
            updated = self._apply_user_value(payload, field_name, message.text or "")
            self._receipt_from_payload(updated)
        except (ValueError, TypeError):
            await self._show_manual_form(
                message, user_id, "awaiting_edit", state_data, payload, field_name, invalid=True
            )
            return
        await asyncio.to_thread(self._database.update_expense_payload, expense_id, updated)
        if state_data.get("return_to") != "final":
            await self._show_initial_confirmation(message, user_id, expense_id, updated)
            return
        if field_name == "date":
            new_date = self._payload_date(updated)
            previous = state_data.get("transaction_date")
            if not isinstance(previous, str) or scout_year_label(
                date.fromisoformat(previous)
            ) != scout_year_label(new_date):
                # Portal options depend on the scout year: account and type must be chosen again.
                retained: dict[str, object] = {
                    "direction": state_data.get("direction"),
                    "portal_notes": state_data.get("portal_notes"),
                    "final_edit": True,
                }
                await self._begin_portal_selection(
                    user_id, expense_id, updated, message, context,
                    retained=retained, prefix=texts.DATE_YEAR_CHANGED,
                )
                return
            state_data["transaction_date"] = new_date.isoformat()
        await self._show_final_summary(user_id, updated, state_data, message)

    async def _handle_note_text(
        self,
        message: Message,
        user_id: int,
        state_data: dict[str, object],
        payload: dict[str, object],
    ) -> None:
        notes_text = (message.text or "").strip()
        if len(notes_text) > _NOTES_MAX_LENGTH:
            await message.reply_text(
                texts.NOTES_TOO_LONG.format(limit=_NOTES_MAX_LENGTH),
                reply_markup=self._navigation_keyboard(cancel=True),
            )
            return
        if not self._has_complete_selection(state_data):
            await message.reply_text(texts.USE_BUTTONS)
            return
        # A note only updates the summary: saving always requires the final «Accetta».
        state_data["portal_notes"] = notes_text or None
        await self._show_final_summary(user_id, payload, state_data, message)

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
        reference_key = (
            "summary_message_id" if command in _SUMMARY_REFERENCE_COMMANDS
            else "form_message_id" if command in _FORM_REFERENCE_COMMANDS
            else None
        )
        expected = state_data.get(reference_key) if reference_key else None
        if isinstance(expected, int) and getattr(query.message, "message_id", None) != expected:
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

        payload = expense.payload
        if value.startswith("cancel"):
            await self._cancel_pending(user_id, expense_id, payload)
            await query.edit_message_text(texts.CANCELLED, reply_markup=self._navigation_keyboard())
        elif value.startswith("edit:"):
            await self._start_field_edit(
                query, context, user_id, state, state_data, expense_id, payload,
                value.split(":", 1)[1],
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
                payload,
                query,
                context,
            )
        elif value == "save":
            if not self._has_complete_selection(state_data):
                await query.edit_message_text(texts.STALE_BUTTON)
                return
            # The pressed summary becomes the message that shows progress and outcome.
            summary = query.message
            chat_id = getattr(summary, "chat_id", None)
            message_id = getattr(summary, "message_id", None)
            if isinstance(chat_id, int) and isinstance(message_id, int):
                state_data.update(summary_chat_id=chat_id, summary_message_id=message_id)
            notes = state_data.get("portal_notes")
            await self._submit_expense(
                update, context, user_id, expense_id, payload, state_data,
                notes if isinstance(notes, str) else "",
            )
        elif value == "modify":
            await self._show_final_edit_menu(query, user_id, payload, state_data)
        elif value == "back":
            await self._go_back(query, user_id, state, state_data, expense_id, payload)
        elif value in {"clear_notes", "no_notes"}:
            if not self._has_complete_selection(state_data):
                await query.edit_message_text(texts.STALE_BUTTON)
                return
            if value == "clear_notes":
                state_data["portal_notes"] = None
            await self._show_final_summary(user_id, payload, state_data, query)
        elif value == "manual_save":
            if expense.payload.get("manual_entry") is not True:
                await query.edit_message_text(texts.STALE_BUTTON)
                return
            notes_text = state_data.get("portal_notes")
            await self._submit_expense(
                update,
                context,
                user_id,
                expense_id,
                expense.payload,
                state_data,
                notes_text if isinstance(notes_text, str) and notes_text != "-" else "",
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

    async def _start_field_edit(
        self,
        query: CallbackQuery,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
        state: str,
        state_data: dict[str, object],
        expense_id: str,
        payload: dict[str, object],
        field_name: str,
    ) -> None:
        if field_name in _TEXT_EDIT_FIELDS:
            if state == "awaiting_confirmation":
                data: dict[str, object] = {"expense_id": expense_id, "return_to": "initial"}
            else:
                data = self._selection_snapshot(state_data)
                data["return_to"] = "final"
            data["edit_field"] = field_name
            text = self._manual_form_text(
                payload, field_name, user_id=user_id,
                prompt=self._field_prompt(payload, field_name, "awaiting_edit", data),
            )
            await self._edit_panel(
                query, user_id, "awaiting_edit", data, text,
                self._manual_form_keyboard(back=True),
            )
            return
        if (
            state != "awaiting_final_edit"
            or self._bilancio is None
            or not self._has_complete_selection(state_data)
        ):
            await query.edit_message_text(texts.STALE_BUTTON)
            return
        data = self._selection_snapshot(state_data)
        data["final_edit"] = True
        if field_name == "account":
            await self._show_progress(query, texts.ACCOUNTS_PROGRESS)
            await self._show_account_choice(user_id, expense_id, payload, data, query, context)
        elif field_name == "transaction_type":
            voice = data.get("accounting_voice")
            await self._save_state(user_id, "awaiting_accounting_voice", data)
            await self._select_accounting_voice(
                user_id, expense_id, payload, "awaiting_accounting_voice", data,
                voice if voice in {"Voci Scout", "Voci Ministero"} else "Voci Scout",
                query, context,
            )
        elif field_name == "direction" and self._direction_selectable(payload, user_id):
            await self._save_state(user_id, "awaiting_direction", data)
            await self._show_direction_prompt(query, user_id, expense_id, payload, data)
        elif field_name == "notes":
            notes = data.get("portal_notes")
            current = (
                texts.NOTE_CURRENT.format(value=texts.safe_text(notes, _NOTES_MAX_LENGTH))
                if isinstance(notes, str) and notes.strip()
                else ""
            )
            rows: list[list[InlineKeyboardButton]] = []
            if current:
                rows.append([InlineKeyboardButton(
                    texts.CLEAR_NOTE_BUTTON, callback_data="receipt:clear_notes"
                )])
            rows.extend(self._navigation_rows(data))
            await self._edit_panel(
                query, user_id, "awaiting_notes", data,
                texts.NOTE_EDIT_PROMPT.format(limit=_NOTES_MAX_LENGTH, current=current),
                InlineKeyboardMarkup(rows),
            )
        else:
            await query.edit_message_text(texts.STALE_BUTTON)

    async def _go_back(
        self,
        query: CallbackQuery,
        user_id: int,
        state: str,
        state_data: dict[str, object],
        expense_id: str,
        payload: dict[str, object],
    ) -> None:
        if state == "awaiting_edit" and state_data.get("return_to") != "final":
            await self._show_initial_confirmation(query, user_id, expense_id, payload)
            return
        final_step = (
            state in {"awaiting_edit", "awaiting_final_edit", "awaiting_notes"}
            or state_data.get("final_edit") is True
        )
        if not final_step or not self._has_complete_selection(state_data):
            await query.edit_message_text(texts.STALE_BUTTON)
            return
        await self._show_final_summary(user_id, payload, state_data, query)

    async def _show_final_edit_menu(
        self,
        query: CallbackQuery,
        user_id: int,
        payload: dict[str, object],
        state_data: dict[str, object],
    ) -> None:
        if not self._has_complete_selection(state_data):
            await query.edit_message_text(texts.STALE_BUTTON)
            return
        data = self._selection_snapshot(state_data)
        rows = [
            [
                InlineKeyboardButton(texts.DATE_BUTTON, callback_data="receipt:edit:date"),
                InlineKeyboardButton(texts.AMOUNT_BUTTON, callback_data="receipt:edit:total_amount"),
            ],
            [InlineKeyboardButton(texts.DESCRIPTION_BUTTON, callback_data="receipt:edit:merchant")],
            [
                InlineKeyboardButton(texts.ACCOUNT_BUTTON, callback_data="receipt:edit:account"),
                InlineKeyboardButton(
                    texts.TYPE_BUTTON, callback_data="receipt:edit:transaction_type"
                ),
            ],
        ]
        # Unit and portal category are not chosen in the current path, so no buttons.
        if self._direction_selectable(payload, user_id):
            rows.append([InlineKeyboardButton(
                texts.DIRECTION_BUTTON, callback_data="receipt:edit:direction"
            )])
        rows.append([InlineKeyboardButton(texts.NOTE_BUTTON, callback_data="receipt:edit:notes")])
        rows.append([InlineKeyboardButton(
            texts.BACK_TO_SUMMARY_BUTTON, callback_data="receipt:back"
        )])
        rows.append([InlineKeyboardButton(texts.CANCEL_BUTTON, callback_data="receipt:cancel")])
        await self._edit_panel(
            query, user_id, "awaiting_final_edit", data,
            texts.FINAL_EDIT_PROMPT.format(details=self._operation_details(payload, data)),
            InlineKeyboardMarkup(rows),
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

    def _mode_text(self, payload: dict[str, object], user_id: int | None = None) -> str:
        return (
            texts.MODE_TRIAL
            if user_id == self._settings.admin_user_id and self._uses_dry_run(payload)
            else ""
        )

    @staticmethod
    def _field_prompt(
        payload: dict[str, object],
        field_name: str | None,
        state: str,
        state_data: dict[str, object],
    ) -> str:
        if not field_name:
            return ""
        manual = payload.get("manual_entry") is True
        if state == "awaiting_receipt_data":
            if state_data.get("review") is True and not manual:
                return _FIELD_PROMPTS.get(field_name, "")
            return _MANUAL_EDIT_PROMPTS.get(field_name, "")
        if manual:
            return _MANUAL_EDIT_PROMPTS.get(field_name, "")
        return _EDIT_PROMPTS.get(field_name, "")

    def _manual_form_text(
        self,
        payload: dict[str, object],
        field_name: str | None,
        *,
        invalid: bool = False,
        user_id: int | None = None,
        prompt: str | None = None,
    ) -> str:
        if prompt is None:
            prompt = _MANUAL_EDIT_PROMPTS.get(field_name, "") if field_name else ""
        text = prompt
        if invalid:
            text = texts.MANUAL_FORM_INVALID + "\n\n" + text
        value = payload.get(field_name) if field_name else None
        if value is not None:
            if field_name == "date":
                value = texts.date_text(value)
            elif field_name == "total_amount":
                value = texts.amount_text(payload)
            text += texts.FIELD_SUGGESTION.format(value=texts.safe_text(value))
        mode = self._mode_text(payload, user_id)
        return text + (f"\n\n{mode}" if mode else "")

    @staticmethod
    def _manual_form_keyboard(*, back: bool = False) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        if back:
            rows.append([InlineKeyboardButton(
                texts.BACK_TO_SUMMARY_BUTTON, callback_data="receipt:back"
            )])
        rows.append([InlineKeyboardButton(texts.CANCEL_BUTTON, callback_data="receipt:cancel")])
        return InlineKeyboardMarkup(rows)

    def _form_field(
        self, state: str, state_data: dict[str, object], payload: dict[str, object]
    ) -> str | None:
        if state == "awaiting_edit":
            field_name = state_data.get("edit_field")
            return field_name if isinstance(field_name, str) else None
        field_name = state_data.get("input_field")
        return field_name if isinstance(field_name, str) else self._next_missing_field(payload)

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
        data = {**state_data}
        if state == "awaiting_receipt_data":
            data["input_field"] = field_name
        await self._save_state(user_id, state, data)
        text = self._manual_form_text(
            payload, field_name, invalid=invalid, user_id=user_id,
            prompt=self._field_prompt(payload, field_name, state, data),
        )
        try:
            sent = await message.reply_text(
                text, reply_markup=self._manual_form_keyboard(back=state == "awaiting_edit")
            )
        except TelegramError as error:
            logger.warning("Telegram field prompt failed: %s", type(error).__name__)
            return
        await self._save_state(
            user_id,
            state,
            self._form_data(
                data, getattr(sent, "chat_id", None), getattr(sent, "message_id", None)
            ),
        )

    async def _edit_panel(
        self,
        query: CallbackQuery,
        user_id: int,
        state: str,
        state_data: dict[str, object],
        text: str,
        keyboard: InlineKeyboardMarkup,
    ) -> None:
        """Show a step in the pressed message, falling back to a new message."""
        form = query.message
        data = self._form_data(
            state_data, getattr(form, "chat_id", None), getattr(form, "message_id", None)
        )
        await self._save_state(user_id, state, data)
        try:
            await query.edit_message_text(text, reply_markup=keyboard)
        except BadRequest as error:
            if "not modified" in str(error).lower():
                return
            logger.warning("Telegram panel edit failed: %s", type(error).__name__)
        except TelegramError as error:
            logger.warning("Telegram panel edit failed: %s", type(error).__name__)
        else:
            return
        if form is None or not hasattr(form, "reply_text"):
            return
        sent = await form.reply_text(text, reply_markup=keyboard)
        await self._save_state(
            user_id,
            state,
            self._form_data(
                data, getattr(sent, "chat_id", None), getattr(sent, "message_id", None)
            ),
        )

    def _initial_confirmation(
        self, user_id: int, payload: dict[str, object]
    ) -> tuple[str, InlineKeyboardMarkup]:
        if payload.get("manual_entry") is True:
            text = self._manual_confirmation_text(payload)
            keyboard = self._manual_confirmation_keyboard()
        else:
            confidence = payload.get("confidence")
            text = texts.receipt_confirmation(
                payload,
                float(confidence) if isinstance(confidence, (int, float)) else 0.0,
                header=texts.RECEIPT_REVIEW,
            )
            keyboard = self._confirmation_keyboard()
        mode = self._mode_text(payload, user_id)
        return text + (f"\n\n{mode}" if mode else ""), keyboard

    async def _show_initial_confirmation(
        self,
        target: CallbackQuery | Message,
        user_id: int,
        expense_id: str,
        payload: dict[str, object],
    ) -> None:
        """Show all operation data once: confirm, or correct one field and come back."""
        text, keyboard = self._initial_confirmation(user_id, payload)
        data: dict[str, object] = {"expense_id": expense_id}
        if not isinstance(target, Message) and not hasattr(target, "reply_text"):
            await self._edit_panel(target, user_id, "awaiting_confirmation", data, text, keyboard)
            return
        await self._save_state(user_id, "awaiting_confirmation", data)
        sent = await target.reply_text(text, reply_markup=keyboard)
        await self._save_state(
            user_id,
            "awaiting_confirmation",
            self._form_data(
                data, getattr(sent, "chat_id", None), getattr(sent, "message_id", None)
            ),
        )

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
        mode = self._mode_text(payload, user_id)
        summary += f"\n\n{mode}" if mode else ""
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
        state_data: dict[str, object] | None = None,
    ) -> None:
        if (
            state_data is not None
            and state_data.get("final_edit") is True
            and self._has_complete_selection(state_data)
        ):
            # A failed read during a final edit keeps the previous complete selection.
            await self._show_final_summary(
                user_id, payload, state_data, query,
                prefix=texts.PORTAL_EDIT_READ_FAILED.format(subject=subject),
            )
            return
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
        *,
        retained: dict[str, object] | None = None,
        prefix: str = "",
    ) -> None:
        """Show the account list; ``retained`` keeps choices from a final edit."""
        if self._bilancio is None:
            raise RuntimeError("Bilancio client is not configured")
        transaction_date = self._payload_date(payload)
        notes = payload.get("portal_notes")
        data: dict[str, object] = {
            "expense_id": expense_id,
            "direction": (
                payload.get("direction", TransactionDirection.INCOME.value)
                if payload.get("manual_entry") is True
                else TransactionDirection.EXPENSE.value
            ),
            "portal_notes": notes if isinstance(notes, str) and notes else None,
        }
        if retained:
            data.update(
                {key: value for key, value in retained.items() if key not in _FORM_KEYS}
            )
        data["expense_id"] = expense_id
        data["transaction_date"] = transaction_date.isoformat()
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
                user_id, expense_id, payload, query, texts.ACCOUNTS_SUBJECT, data
            )
            return
        if not options:
            logger.warning("The portal returned no available accounts")
            await self._portal_read_failed(
                user_id, expense_id, payload, query, texts.ACCOUNTS_SUBJECT, data
            )
            return
        data["options"] = self._serialize_options(options)
        await self._save_state(user_id, "awaiting_account", data)
        mode = self._mode_text(payload, user_id)
        await self._selection_reply(
            query,
            prefix + texts.ACCOUNT_PROMPT + (f"\n\n{mode}" if mode else ""),
            reply_markup=self._option_keyboard(
                options, extra_rows=self._navigation_rows(data)
            ),
        )

    async def _show_account_choice(
        self,
        user_id: int,
        expense_id: str,
        payload: dict[str, object],
        state_data: dict[str, object],
        query: CallbackQuery,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        await self._begin_portal_selection(
            user_id, expense_id, payload, query, context, retained=state_data
        )

    async def _show_direction_prompt(
        self,
        query: CallbackQuery,
        user_id: int,
        expense_id: str,
        payload: dict[str, object],
        state_data: dict[str, object],
    ) -> None:
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
        keyboard = InlineKeyboardMarkup([directions, *self._navigation_rows(state_data)])
        await query.edit_message_text(
            texts.DIRECTION_PROMPT if has_receipt or manual else texts.INCOME_ONLY_PROMPT,
            reply_markup=keyboard,
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
        if (
            state_data.get("final_edit") is True
            and self._has_complete_selection(state_data)
            and state_data.get("direction") == value
        ):
            await self._show_final_summary(user_id, payload, state_data, query)
            return
        if state_data.get("direction") != value:
            # Portal types depend on the direction: the previous type is no longer valid.
            state_data.update(transaction_type=None, unit=None, category=None)
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
                user_id, expense_id, payload, query, texts.TYPES_SUBJECT, state_data
            )
            return
        if not options:
            await self._portal_read_failed(
                user_id, expense_id, payload, query, texts.TYPES_SUBJECT, state_data
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
        extra_rows.extend(self._navigation_rows(state_data))
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
                extra_rows=self._navigation_rows(state_data),
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
            chosen = self._serialize_option(selected)
            if (
                state_data.get("final_edit") is True
                and self._has_complete_selection(state_data)
                and state_data.get("account") == chosen
            ):
                await self._show_final_summary(user_id, payload, state_data, query)
                return
            if state_data.get("account") != chosen:
                # Types (and the default unit) depend on the account: choose them again.
                state_data.update(transaction_type=None, unit=None, category=None)
            state_data["account"] = chosen
            await self._save_state(user_id, "awaiting_direction", state_data)
            if direction is not None:
                await self._select_direction(
                    user_id, expense_id, payload, "awaiting_direction",
                    state_data, direction.value, query, context,
                )
                return
            await self._show_direction_prompt(query, user_id, expense_id, payload, state_data)
            return
        if direction is None:
            await query.edit_message_text(texts.CHOOSE_DIRECTION)
            return
        if state != "awaiting_transaction_type":
            await query.edit_message_text(texts.STALE_BUTTON)
            return

        state_data.update(transaction_type=self._serialize_option(selected))
        unit = default_unit_for_account(account)
        # The note is kept: it is only changed from the final «Modifica» menu.
        state_data.update(
            unit=self._serialize_option(unit) if unit is not None else None,
            category=None,
        )
        await self._show_final_summary(user_id, payload, state_data, query)

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
        if not isinstance(state_data.get("transaction_type"), dict):
            await query.edit_message_text(texts.STALE_BUTTON)
            return
        account = self._option_from(state_data, "account")
        unit = default_unit_for_account(account)
        state_data.update(
            unit=self._serialize_option(unit) if unit is not None else None,
            category=None,
        )
        await self._show_final_summary(user_id, payload, state_data, query)

    async def _submit_expense(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
        expense_id: str,
        payload: dict[str, object],
        state_data: dict[str, object],
        notes_text: str,
    ) -> None:
        message = getattr(update, "effective_message", None) or update.message
        if message is None:
            return
        async def send(text: str) -> None:
            details = self._operation_details(payload, state_data)
            if texts.DATE_LINE.format(value=texts.date_text(payload.get("date"))) not in text:
                text += "\n\n" + details
            await self._update_summary(update, context, user_id, payload, state_data, text)

        # Preserve the note and delivery target before any Telegram or portal operation.
        state_data["portal_notes"] = notes_text or None
        payload["portal_notes"] = notes_text or None
        await asyncio.to_thread(self._database.update_expense_payload, expense_id, payload)
        # A distinct state blocks new edits or a second «Accetta» while saving.
        await self._save_state(user_id, "submitting", state_data)
        await self._update_summary(
            update, context, user_id, payload, state_data,
            f"{texts.INSERTING}\n\n{self._operation_details(payload, state_data)}\n\n"
            f"{self._mode_text(payload, user_id)}",
        )

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
                    save_corrected=True,
                )
                if archived.correction_failed:
                    await self._notify_admin(context, texts.CORRECTED_COPY_FAILED)
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
                notes=notes_text or None,
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

        await asyncio.to_thread(
            self._database.record_verified_submission,
            expense_id,
            portal_expense,
            result,
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

    @staticmethod
    def _has_complete_selection(state_data: dict[str, object]) -> bool:
        return (
            isinstance(state_data.get("account"), dict)
            and isinstance(state_data.get("transaction_type"), dict)
            and isinstance(state_data.get("direction"), str)
            and isinstance(state_data.get("transaction_date"), str)
        )

    @staticmethod
    def _selection_snapshot(state_data: dict[str, object]) -> dict[str, object]:
        return {
            key: value for key, value in state_data.items() if key not in _TRANSIENT_KEYS
        }

    def _direction_selectable(self, payload: dict[str, object], user_id: int) -> bool:
        # Only the administrator /manual path lets the user choose the direction.
        return (
            payload.get("manual_entry") is True
            and payload.get("direction") is None
            and user_id == self._settings.admin_user_id
        )

    def _back_rows(self, state_data: dict[str, object]) -> list[list[InlineKeyboardButton]]:
        if state_data.get("final_edit") is True and self._has_complete_selection(state_data):
            return [[InlineKeyboardButton(
                texts.BACK_TO_SUMMARY_BUTTON, callback_data="receipt:back"
            )]]
        return []

    def _navigation_rows(self, state_data: dict[str, object]) -> list[list[InlineKeyboardButton]]:
        return [
            *self._back_rows(state_data),
            [InlineKeyboardButton(texts.CANCEL_BUTTON, callback_data="receipt:cancel")],
        ]

    def _final_summary_text(
        self, user_id: int, payload: dict[str, object], state_data: dict[str, object]
    ) -> str:
        details = self._operation_details(payload, state_data)
        notes = state_data.get("portal_notes")
        if not (isinstance(notes, str) and notes.strip()):
            details += "\n" + texts.NO_NOTES_LINE
        mode = self._mode_text(payload, user_id)
        return (
            f"{texts.FINAL_SUMMARY_TITLE}\n\n{details}\n\n"
            + (f"{mode}\n\n" if mode else "")
            + texts.FINAL_SUMMARY_HINT
        )

    @staticmethod
    def _final_summary_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(texts.FINAL_ACCEPT_BUTTON, callback_data="receipt:save")],
                [InlineKeyboardButton(texts.FINAL_MODIFY_BUTTON, callback_data="receipt:modify")],
                [InlineKeyboardButton(texts.CANCEL_BUTTON, callback_data="receipt:cancel")],
            ]
        )

    async def _show_final_summary(
        self,
        user_id: int,
        payload: dict[str, object],
        state_data: dict[str, object],
        target: CallbackQuery | Message,
        *,
        prefix: str = "",
    ) -> None:
        """Show the final summary; nothing is saved until «Accetta» is pressed."""
        data = self._selection_snapshot(state_data)
        for key in ("summary_chat_id", "summary_message_id", *_FORM_KEYS):
            data.pop(key, None)
        notes = data.get("portal_notes")
        data["portal_notes"] = notes if isinstance(notes, str) and notes.strip() else None
        expense_id = str(data["expense_id"])
        text = prefix + self._final_summary_text(user_id, payload, data)
        keyboard = self._final_summary_keyboard()
        await self._save_state(user_id, "awaiting_final_confirmation", data)
        shown: object | None = None
        if isinstance(target, Message) or hasattr(target, "reply_text"):
            try:
                shown = await target.reply_text(text, reply_markup=keyboard)
            except TelegramError as error:
                logger.warning("Telegram summary delivery failed: %s", type(error).__name__)
                return
        else:
            try:
                await target.edit_message_text(text, reply_markup=keyboard)
                shown = target.message
            except BadRequest as error:
                if "not modified" in str(error).lower():
                    shown = target.message
                else:
                    logger.warning("Telegram summary edit rejected: %s", type(error).__name__)
            except TelegramError as error:
                logger.warning("Telegram summary edit failed: %s", type(error).__name__)
            if shown is None:
                message = target.message
                if message is None or not hasattr(message, "reply_text"):
                    return
                try:
                    shown = await message.reply_text(text, reply_markup=keyboard)
                except TelegramError as error:
                    logger.warning(
                        "Telegram summary fallback failed: %s", type(error).__name__
                    )
                    return
        chat_id = getattr(shown, "chat_id", None)
        message_id = getattr(shown, "message_id", None)
        if isinstance(chat_id, int) and isinstance(message_id, int):
            data.update(
                summary_chat_id=chat_id, summary_message_id=message_id,
                form_chat_id=chat_id, form_message_id=message_id,
            )
        payload.update(
            summary_chat_id=data.get("summary_chat_id"),
            summary_message_id=data.get("summary_message_id"),
            telegram_summary_text=text,
            portal_notes=data["portal_notes"],
        )
        await asyncio.to_thread(self._database.update_expense_payload, expense_id, payload)
        await self._save_state(user_id, "awaiting_final_confirmation", data)

    async def _update_summary(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int,
        payload: dict[str, object], state_data: dict[str, object], text: str,
    ) -> None:
        """Edit the persisted summary; retain delivery data if Telegram is unavailable."""
        expense_id = str(state_data["expense_id"])
        payload.update(
            telegram_summary_text=text,
            summary_chat_id=state_data.get("summary_chat_id"),
            summary_message_id=state_data.get("summary_message_id"),
        )
        await asyncio.to_thread(self._database.update_expense_payload, expense_id, payload)
        bot = getattr(context, "bot", None)
        query = getattr(update, "callback_query", None)
        chat_id = state_data.get("summary_chat_id")
        message_id = state_data.get("summary_message_id")
        try:
            if bot is not None and isinstance(chat_id, int) and isinstance(message_id, int):
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id, text=text, reply_markup=None
                )
                return
            if query is not None:
                await query.edit_message_text(text, reply_markup=None)
                return
            logger.warning("Telegram summary reference is missing; using fallback")
        except BadRequest as error:
            if "not modified" in str(error).lower():
                return
            logger.warning("Telegram summary edit rejected: %s", type(error).__name__)
        except TelegramError as error:
            logger.warning("Telegram summary edit failed: %s", type(error).__name__)
        # A deleted/inaccessible summary requires a replacement, never a second submission.
        message = getattr(update, "effective_message", None) or getattr(update, "message", None)
        try:
            replacement = await message.reply_text(text, reply_markup=None)
        except TelegramError as error:
            logger.warning("Telegram summary fallback failed: %s", type(error).__name__)
            return
        chat_id = getattr(replacement, "chat_id", None)
        message_id = getattr(replacement, "message_id", None)
        if isinstance(chat_id, int) and isinstance(message_id, int):
            state_data.update(summary_chat_id=chat_id, summary_message_id=message_id)
            payload.update(summary_chat_id=chat_id, summary_message_id=message_id)
            await asyncio.to_thread(self._database.update_expense_payload, expense_id, payload)
            saved = await asyncio.to_thread(self._database.get_conversation_state, user_id)
            if saved is not None:
                await self._save_state(user_id, saved[0], state_data)

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
                    InlineKeyboardButton(texts.DATE_BUTTON, callback_data="receipt:edit:date"),
                    InlineKeyboardButton(texts.AMOUNT_BUTTON, callback_data="receipt:edit:total_amount"),
                ],
                [InlineKeyboardButton(texts.DESCRIPTION_BUTTON, callback_data="receipt:edit:merchant")],
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
        for field_name in _INPUT_FIELDS:
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
