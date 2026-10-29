from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol
import uuid

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bilancio.client import (
    BilancioClient,
    BilancioClientError,
    PortalExpense,
    PortalOption,
    TransactionDirection,
    default_unit_for_account,
)
from config import Settings
from db.database import Database
from storage.archive import FileArchive
from storage.pending import MAX_PENDING_RECEIPT_BYTES, PendingReceiptStore
from vision.ollama_client import LocalVisionError
from vision.receipt_schema import (
    ReceiptData,
    ReceiptParseError,
    missing_required_fields,
)

logger = logging.getLogger(__name__)

_FIELD_PROMPTS = {
    "date": "Non riesco a leggere la data. Scrivila come GG/MM/AAAA.",
    "merchant": "Non riesco a leggere il negoziante. Scrivine il nome.",
    "total_amount": "Non riesco a leggere l’importo totale. Scrivilo, per esempio 12,50.",
}
_EDIT_FIELDS = {"date", "merchant", "total_amount", "suggested_category"}
_MANUAL_EDIT_PROMPTS = {
    "date": "📅 Scrivi la data dell’operazione (GG/MM/AAAA).",
    "merchant": "✏️ Scrivi la descrizione dell’operazione.",
    "total_amount": "💶 Scrivi l’importo, per esempio 12,50.",
}
_CALLBACK_STATES = {
    "confirm": {"awaiting_confirmation"},
    "edit": {"awaiting_confirmation"},
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
    "more_types": {"awaiting_transaction_type"},
}
_STALE_CHOICE_TEXT = "Questa scelta non è più attiva."
_BUSY_TEXT = "⏳ Sto ancora lavorando, attendi."


def _safe_text(value: str) -> str:
    return " ".join(value.split())[:200]
_MANUAL_DEFAULT_DESCRIPTION = "Prova Telegram"
_MANUAL_DEFAULT_AMOUNT = "0.10"
_TOP_TYPES_COUNT = 6


class ReceiptAnalyzer(Protocol):
    async def analyze(self, file_bytes: bytes, file_name: str) -> ReceiptData:
        """Analyze a receipt using the configured local vision model."""


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
    ) -> None:
        message = update.message
        user = update.effective_user
        if message is None or user is None:
            return
        if user.id != self._settings.admin_user_id:
            await message.reply_text("Comando riservato all’amministratore.")
            return
        if self._bilancio is None:
            await message.reply_text(
                "Il collegamento al portale non è configurato. Non ho creato "
                "alcuna spesa."
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
                    "C’è già uno scontrino in lavorazione. Completalo oppure usa "
                    "/annulla prima di iniziare la prova manuale."
                )
                return
            await self.cancel_current(user.id)
            await message.reply_text("Ho annullato la prova precedente rimasta aperta.")

        expense_id = uuid.uuid4().hex
        payload: dict[str, object] = {
            "manual_entry": True,
            "date": date.today().isoformat(),
            "merchant": _MANUAL_DEFAULT_DESCRIPTION,
            "total_amount": _MANUAL_DEFAULT_AMOUNT,
            "currency": "EUR",
            "confidence": 1.0,
        }
        await asyncio.to_thread(
            self._database.create_manual_expense,
            expense_id=expense_id,
            telegram_user_id=user.id,
            payload=payload,
        )
        await self._save_state(
            user.id,
            "awaiting_confirmation",
            {"expense_id": expense_id},
        )
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
        if self._analyzer is None:
            await message.reply_text(
                "L’analisi locale non è ancora configurata. Riprova più tardi."
            )
            return
        if await asyncio.to_thread(
            self._database.get_conversation_state, user.id
        ) is not None:
            await message.reply_text(
                "C’è già un’operazione in corso. Usa /annulla prima di inviare "
                "un altro scontrino."
            )
            return

        file_id, file_name, file_size = self._message_file(message)
        if file_id is None or file_name is None:
            await message.reply_text("Invia una foto o un documento PDF.")
            return
        if file_size is not None and file_size > MAX_PENDING_RECEIPT_BYTES:
            await message.reply_text("Il file supera il limite di 20 MB.")
            return

        try:
            telegram_file = await context.bot.get_file(file_id)
            file_bytes = bytes(await telegram_file.download_as_bytearray())
            pending = await asyncio.to_thread(
                self._pending_store.save,
                file_bytes,
                file_name,
            )
        except (OSError, ValueError) as error:
            logger.warning("Could not store incoming receipt: %s", type(error).__name__)
            await message.reply_text(
                "Non riesco a conservare questo file. Controlla che sia una foto "
                "o un PDF valido e riprova."
            )
            return

        created = await asyncio.to_thread(
            self._database.create_expense,
            expense_id=pending.receipt_id,
            telegram_user_id=user.id,
            sha256=pending.sha256,
            payload={"pending_path": str(pending.path), "source_name": file_name},
        )
        if not created:
            await asyncio.to_thread(self._pending_store.delete, pending.path)
            await message.reply_text(
                "Questo file è già stato ricevuto: non lo elaboro una seconda volta."
            )
            return

        await message.reply_text("Sto controllando lo scontrino in locale…")
        try:
            receipt = await self._analyzer.analyze(file_bytes, file_name)
        except (LocalVisionError, ReceiptParseError) as error:
            logger.warning("Receipt analysis failed: %s", type(error).__name__)
            await self._notify_admin(
                context,
                "L’analisi locale di una ricevuta non è riuscita.",
            )
            await asyncio.to_thread(self._database.delete_expense, pending.receipt_id)
            await asyncio.to_thread(self._pending_store.delete, pending.path)
            await message.reply_text(
                "Non sono riuscito ad analizzare lo scontrino. Il file non è stato "
                "inviato a servizi online; controlla che il modello locale sia "
                "disponibile e riprova."
            )
            return

        data = {
            **receipt.model_dump(mode="json"),
            "pending_path": str(pending.path),
            "source_name": file_name,
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
            await message.reply_text(_FIELD_PROMPTS[missing[0]])
            return
        await message.reply_text(
            self._confirmation_text(receipt),
            reply_markup=self._confirmation_keyboard(),
        )

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
                "L’operazione non è più disponibile. Invia di nuovo lo scontrino."
            )
            return

        if state == "manual_entry_data":
            try:
                updated = self._parse_manual_entry(expense.payload, message.text)
            except ValueError:
                await message.reply_text(
                    "Formato non valido. Scrivi data, descrizione e importo "
                    "separati da |, per esempio 26/10/2026 | Prova Telegram | 1,00."
                )
                return
            await asyncio.to_thread(
                self._database.update_expense_payload,
                expense_id,
                updated,
            )
            await self._save_state(user.id, "awaiting_confirmation", state_data)
            await message.reply_text(
                self._manual_confirmation_text(updated),
                reply_markup=self._manual_confirmation_keyboard(),
            )
            return
        if state == "awaiting_receipt_data":
            field_name = self._next_missing_field(expense.payload)
        elif state == "awaiting_edit":
            field_name = state_data.get("edit_field")
            if field_name not in _EDIT_FIELDS:
                raise ValueError("Conversation state contains an unsupported edit field")
        elif state == "awaiting_notes":
            if expense.payload.get("manual_entry") is True:
                state_data["portal_notes"] = (
                    None if message.text.strip() == "-" else message.text.strip()
                )
                await self._save_state(
                    user.id,
                    "awaiting_manual_final_confirmation",
                    state_data,
                )
                await message.reply_text(
                    self._manual_final_confirmation_text(
                        expense.payload,
                        state_data,
                    ),
                    reply_markup=self._manual_final_confirmation_keyboard(),
                )
                return
            await self._submit_expense(
                update,
                context,
                user.id,
                expense_id,
                expense.payload,
                state_data,
                message.text.strip(),
            )
            return
        else:
            await message.reply_text(
                "Usa i pulsanti del messaggio precedente oppure /annulla."
            )
            return

        try:
            updated = self._apply_user_value(expense.payload, field_name, message.text)
        except (ValueError, TypeError):
            await message.reply_text(self._prompt_for(field_name))
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
            await message.reply_text(_FIELD_PROMPTS[missing[0]])
            return
        await self._save_state(user.id, "awaiting_confirmation", state_data)
        if updated.get("manual_entry") is True:
            await message.reply_text(
                self._manual_confirmation_text(updated),
                reply_markup=self._manual_confirmation_keyboard(),
            )
            return
        await message.reply_text(
            self._confirmation_text(receipt),
            reply_markup=self._confirmation_keyboard(),
        )

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
        if user.id in self._busy_users:
            await query.answer(_BUSY_TEXT)
            return
        saved_state = await asyncio.to_thread(
            self._database.get_conversation_state, user.id
        )
        if saved_state is None:
            await query.answer("Questa operazione non è più attiva.")
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
                "L’operazione non è più disponibile. Invia di nuovo lo scontrino."
            )
            return

        if value.startswith("cancel"):
            await self._cancel_pending(user_id, expense_id, expense.payload)
            await query.edit_message_text("Operazione annullata.")
        elif value.startswith("edit:"):
            field_name = value.split(":", 1)[1]
            if field_name not in _EDIT_FIELDS:
                await query.edit_message_text("Questa modifica non è disponibile.")
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
                else self._prompt_for(field_name)
            )
        elif value == "confirm":
            if self._bilancio is None:
                await query.edit_message_text(
                    "Il collegamento al portale non è ancora configurato. "
                    "Non è stato registrato nulla; lo scontrino resta in attesa."
                )
                return
            await self._show_progress(query, "⏳ Leggo le casse dal portale AGESCI…")
            await self._begin_portal_selection(
                user_id,
                expense_id,
                expense.payload,
                query,
                context,
            )
        elif value == "manual_save":
            if expense.payload.get("manual_entry") is not True:
                await query.edit_message_text("Questa conferma non è più disponibile.")
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
        # Manual admin entries are always real; DRY_RUN applies to receipts.
        return self._settings.dry_run and payload.get("manual_entry") is not True

    def _submission_progress_text(self, dry_run: bool) -> str:
        if dry_run:
            return "⏳ Eseguo la prova simulata sul portale… Non premere altri pulsanti."
        return (
            "⏳ Registro l’operazione nel portale e verifico il salvataggio… "
            "Non premere altri pulsanti."
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
        query: CallbackQuery,
        subject: str,
    ) -> None:
        if payload.get("manual_entry") is True:
            await self._cancel_pending(user_id, expense_id, payload)
            await query.edit_message_text(
                f"Non riesco a leggere {subject} dal portale. Ho annullato la prova "
                "in automatico: non è stato inserito nulla. Riprova più tardi con "
                "/testspesa."
            )
            return
        await self._save_state(
            user_id,
            "awaiting_confirmation",
            {"expense_id": expense_id},
        )
        await query.edit_message_text(
            f"Non riesco a leggere {subject} dal portale. Non è stato inserito "
            "nulla e lo scontrino resta salvato: premi Conferma per riprovare "
            "oppure Annulla.",
            reply_markup=self._confirmation_keyboard(),
        )

    async def _begin_portal_selection(
        self,
        user_id: int,
        expense_id: str,
        payload: dict[str, object],
        query: CallbackQuery,
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
                f"Non riesco a leggere le casse AGESCI. Controlla {debug_path} "
                "per eventuali schermate diagnostiche.",
                error,
            )
            await self._portal_read_failed(
                user_id,
                expense_id,
                payload,
                query,
                "le casse",
            )
            return
        if not options:
            logger.warning("The portal returned no available accounts")
            await self._portal_read_failed(
                user_id,
                expense_id,
                payload,
                query,
                "casse disponibili",
            )
            return
        data: dict[str, object] = {
            "expense_id": expense_id,
            "transaction_date": transaction_date.isoformat(),
            "options": self._serialize_options(options),
        }
        await self._save_state(user_id, "awaiting_account", data)
        await query.edit_message_text(
            "🏦 1/3 · Scegli la cassa:",
            reply_markup=self._option_keyboard(options),
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
            await query.edit_message_text("Direzione non valida.")
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
            await query.edit_message_text("Questa scelta non è più disponibile.")
            return
        if accounting_voice not in {"Voci Scout", "Voci Ministero"}:
            await query.edit_message_text("Questa scelta non è valida.")
            return
        direction_value = state_data.get("direction")
        if not isinstance(direction_value, str):
            await query.edit_message_text("Prima scegli entrata o uscita.")
            return
        account = self._option_from(state_data, "account")
        direction = TransactionDirection(direction_value)
        transaction_date = self._state_date(state_data)
        await self._show_progress(
            query,
            f"⏳ Carico le tipologie di {direction.value} ({accounting_voice})… "
            "La prima volta può richiedere fino a un minuto. "
            "Non premere altri pulsanti.",
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
                f"Non riesco a leggere le tipologie AGESCI. Controlla {debug_path} "
                "per eventuali schermate diagnostiche.",
                error,
            )
            await self._portal_read_failed(
                user_id,
                expense_id,
                payload,
                query,
                "le tipologie",
            )
            return
        if not options:
            await self._portal_read_failed(
                user_id,
                expense_id,
                payload,
                query,
                "tipologie disponibili",
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
            "🏷️ 3/3 · Scegli la tipologia (le più usate):"
            if hidden
            else "🏷️ 3/3 · Scegli la tipologia:"
        )
        extra_rows = (
            [[InlineKeyboardButton(
                f"➕ Altre tipologie ({hidden})",
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
            "🏷️ 3/3 · Altre tipologie:",
            reply_markup=self._option_keyboard(
                options,
                indices=[index for index in range(len(options)) if index not in shown],
                extra_rows=[[InlineKeyboardButton("Annulla", callback_data="receipt:cancel")]],
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
            await query.edit_message_text("Questa opzione non è più disponibile.")
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
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Entrata", callback_data="receipt:direction:entrata"
                        ),
                        InlineKeyboardButton(
                            "Uscita", callback_data="receipt:direction:uscita"
                        ),
                    ]
                ]
            )
            await query.edit_message_text(
                "↔️ 2/3 · Entrata o uscita?",
                reply_markup=keyboard,
            )
            return
        if direction is None:
            await query.edit_message_text("Prima scegli entrata o uscita.")
            return
        if state != "awaiting_transaction_type":
            await query.edit_message_text("Questa scelta non è più disponibile.")
            return

        state_data.update(transaction_type=self._serialize_option(selected))
        unit = default_unit_for_account(account)
        state_data.update(
            unit=self._serialize_option(unit) if unit is not None else None,
            category=None,
            portal_notes=None,
        )
        if payload.get("manual_entry") is True:
            await self._save_state(
                user_id,
                "awaiting_manual_final_confirmation",
                state_data,
            )
            await query.edit_message_text(
                self._manual_final_confirmation_text(payload, state_data),
                reply_markup=self._manual_final_confirmation_keyboard(),
            )
            return
        await self._show_progress(query, self._submission_progress_text(self._uses_dry_run(payload)))
        await self._submit_expense(
            update,
            context,
            user_id,
            expense_id,
            payload,
            state_data,
            "-",
        )

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
        if payload.get("manual_entry") is True:
            await self._save_state(
                user_id,
                "awaiting_manual_final_confirmation",
                state_data,
            )
            await query.edit_message_text(
                self._manual_final_confirmation_text(payload, state_data),
                reply_markup=self._manual_final_confirmation_keyboard(),
            )
            return
        await self._show_progress(query, self._submission_progress_text(self._uses_dry_run(payload)))
        await self._submit_expense(
            update,
            context,
            user_id,
            expense_id,
            payload,
            state_data,
            "-",
        )

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
        query = getattr(update, "callback_query", None)

        async def send(text: str) -> None:
            # Replace the "⏳" progress message so the outcome takes its place.
            if query is not None:
                try:
                    await query.edit_message_text(text)
                    return
                except TelegramError:
                    logger.warning("Could not replace Telegram progress message")
            await message.reply_text(text)

        if self._bilancio is None:
            await send(
                "Il collegamento al portale non è ancora configurato. Non è stato "
                "registrato nulla; lo scontrino resta in attesa."
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
            if payload.get("manual_entry") is True:
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
                    "Il portale non ha confermato la modalità di prova. Per sicurezza "
                    "non ripeterò l’operazione: avvisa l’amministratore e controlla "
                    "il portale prima di riprovare."
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
                    "❌ Non sono riuscito a registrare e verificare la spesa manuale. "
                    "Non la riproverò in automatico.\n\n"
                    f"{self._operation_details(payload, state_data)}\n\n"
                    "Controlla nel portale se compare: se non c’è, ripeti /testspesa."
                    f"\nDiagnostica: {debug_path}"
                )
                return
            await self._notify_admin(
                context,
                "Non ho potuto verificare una registrazione AGESCI. "
                f"Controlla {debug_path} e /coda.",
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
                    "Non ho potuto verificare la registrazione. La ricevuta è "
                    "conservata sul NAS e l’operazione è in coda per un nuovo tentativo."
                )
            else:
                await self._clear_state(user_id)
                await send(
                    "Non ho potuto completare l’operazione. Lo scontrino resta "
                    "conservato; avvisa l’amministratore."
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
            title = "🧪 Prova completata: il portale NON ha salvato l’operazione."
        elif already_present:
            title = (
                "ℹ️ Operazione già presente nel portale: non l’ho inserita "
                "una seconda volta."
            )
        elif payload.get("manual_entry") is True:
            title = "✅ Operazione registrata e verificata nel portale."
        else:
            title = "✅ Spesa registrata e verificata nel portale."
        footer = (
            "Inserimento manuale: nessuno scontrino archiviato."
            if payload.get("manual_entry") is True
            else "La ricevuta è stata archiviata sul NAS."
        )
        await send(f"{title}\n\n{details}\n\n{footer}")

    async def _ask_for_notes(
        self,
        user_id: int,
        state_data: dict[str, object],
    ) -> None:
        await self._save_state(user_id, "awaiting_notes", state_data)

    async def _notify_admin(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        message: str,
        error: BilancioClientError | None = None,
    ) -> None:
        diagnostic_path = error.diagnostic_path if error is not None else None
        if diagnostic_path:
            message = f"{message} Diagnostica locale: {Path(diagnostic_path).name}."
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

    @staticmethod
    def _confirmation_text(receipt: ReceiptData) -> str:
        date_text = receipt.date.isoformat() if receipt.date else "mancante"
        amount_text = (
            f"{receipt.total_amount} {receipt.currency or ''}".strip()
            if receipt.total_amount is not None
            else "mancante"
        )
        merchant = receipt.merchant or "mancante"
        category = receipt.suggested_category or "non indicata"
        warning = (
            "\nLa lettura è incerta: controlla con attenzione."
            if receipt.confidence < 0.7
            else ""
        )
        return (
            "Controlla i dati letti:\n"
            f"Data: {date_text}\n"
            f"Negoziante: {merchant}\n"
            f"Importo: {amount_text}\n"
            f"Categoria suggerita: {category}\n"
            f"Affidabilità: {receipt.confidence:.0%}{warning}\n\n"
            "La ricevuta sarà conservata sul NAS, non allegata al portale."
        )

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
        transaction_date = payload["date"]
        if isinstance(transaction_date, str):
            try:
                transaction_date = date.fromisoformat(transaction_date).strftime("%d/%m/%Y")
            except ValueError:
                pass
        amount = str(payload["total_amount"]).replace(".", ",")
        return (
            "✍️ Spesa manuale · senza foto e senza AI\n\n"
            f"Data: {transaction_date}\n"
            f"Descrizione: {payload['merchant']}\n"
            f"Importo: {amount} EUR\n\n"
            "Premi Conferma per usare questi valori oppure modificali con i pulsanti.\n"
            "⚠️ Inserimento REALE nel portale: prima di salvare ti chiederò "
            "un’ultima conferma."
        )

    @staticmethod
    def _manual_confirmation_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ Conferma", callback_data="receipt:confirm")],
                [
                    InlineKeyboardButton("📅 Data", callback_data="receipt:edit:date"),
                    InlineKeyboardButton("💶 Importo", callback_data="receipt:edit:total_amount"),
                ],
                [InlineKeyboardButton("✏️ Descrizione", callback_data="receipt:edit:merchant")],
                [InlineKeyboardButton("Annulla", callback_data="receipt:cancel")],
            ]
        )

    def _manual_final_confirmation_text(
        self,
        payload: dict[str, object],
        state_data: dict[str, object],
    ) -> str:
        return (
            "Ultimo controllo prima di procedere:\n"
            f"{self._operation_details(payload, state_data)}\n\n"
            "ATTENZIONE: premendo il pulsante inserirai DAVVERO la spesa nel portale."
        )

    @staticmethod
    def _operation_details(
        payload: dict[str, object],
        state_data: dict[str, object],
        *,
        unit_left_empty: bool = False,
    ) -> str:
        def label(key: str) -> str:
            data = state_data.get(key)
            if isinstance(data, dict) and isinstance(data.get("label"), str):
                return _safe_text(data["label"])
            return "nessuna"

        transaction_date = payload.get("date")
        if isinstance(transaction_date, str):
            try:
                transaction_date = date.fromisoformat(transaction_date).strftime("%d/%m/%Y")
            except ValueError:
                pass
        amount = str(payload.get("total_amount", "?")).replace(".", ",")
        direction = (
            "Entrata"
            if state_data.get("direction") == TransactionDirection.INCOME.value
            else "Uscita"
        )
        unit = (
            "nessuna (il portale non ne offre per questo anno)"
            if unit_left_empty
            else label("unit")
        )
        notes = state_data.get("portal_notes")
        return (
            f"📅 Data: {transaction_date}\n"
            f"📝 Descrizione: {_safe_text(str(payload.get('merchant', '')))}\n"
            f"💶 Importo: {amount} EUR\n"
            f"🏦 Cassa: {label('account')}\n"
            f"↕️ Operazione: {direction}\n"
            f"📂 Voci: {state_data.get('accounting_voice', 'Voci Scout')}\n"
            f"🏷️ Tipologia: {label('transaction_type')}\n"
            f"👥 Unità: {unit}\n"
            f"🗂️ Categoria: {label('category')}\n"
            f"🗒️ Note: {_safe_text(notes) if isinstance(notes, str) and notes else 'nessuna'}"
        )

    @staticmethod
    def _manual_final_confirmation_keyboard() -> InlineKeyboardMarkup:
        button_text = "✅ CONFERMA INSERIMENTO REALE"
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        button_text,
                        callback_data="receipt:manual_save",
                    )
                ],
                [InlineKeyboardButton("Annulla", callback_data="receipt:cancel")],
            ]
        )

    @staticmethod
    def _confirmation_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Conferma", callback_data="receipt:confirm")],
                [
                    InlineKeyboardButton("Modifica data", callback_data="receipt:edit:date"),
                    InlineKeyboardButton("Modifica importo", callback_data="receipt:edit:total_amount"),
                ],
                [
                    InlineKeyboardButton("Modifica negoziante", callback_data="receipt:edit:merchant"),
                    InlineKeyboardButton("Cambia categoria", callback_data="receipt:edit:suggested_category"),
                ],
                [InlineKeyboardButton("Annulla", callback_data="receipt:cancel")],
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
                        "Senza categoria",
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
        if field_name == "suggested_category":
            return "Scrivi la categoria oppure - per non indicarla."
        return _FIELD_PROMPTS[field_name]

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
            for date_format in ("%d/%m/%Y", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(value, date_format).date()
                    updated[field_name] = parsed.isoformat()
                    return updated
                except ValueError:
                    continue
            raise ValueError("Date format is invalid")
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
