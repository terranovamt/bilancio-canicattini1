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
        if await asyncio.to_thread(
            self._database.get_conversation_state,
            user.id,
        ) is not None:
            await message.reply_text(
                "C’è già un’operazione in corso. Usa /annulla prima di iniziare "
                "la prova manuale."
            )
            return

        expense_id = uuid.uuid4().hex
        await asyncio.to_thread(
            self._database.create_manual_expense,
            expense_id=expense_id,
            telegram_user_id=user.id,
            payload={"manual_entry": True},
        )
        await self._save_state(
            user.id,
            "manual_entry_data",
            {"expense_id": expense_id},
        )
        mode_notice = (
            "La modalità attuale è SIMULATA: il portale non salverà la spesa."
            if self._settings.dry_run
            else "La modalità attuale è REALE: dopo le scelte ti chiederò un’ultima "
            "conferma prima di inserirla nel portale."
        )
        await message.reply_text(
            "Prova manuale senza scontrino e senza analisi AI.\n"
            "Scrivi data, descrizione e importo separati da |.\n"
            "Esempio: 26/10/2026 | Prova Telegram | 1,00\n"
            f"{mode_notice}\n"
            "Per interrompere in qualsiasi momento usa /annulla."
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
                reply_markup=self._confirmation_keyboard(),
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
                reply_markup=self._confirmation_keyboard(),
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
        await query.answer()
        saved_state = await asyncio.to_thread(
            self._database.get_conversation_state, user.id
        )
        if saved_state is None:
            await query.edit_message_text("Questa operazione non è più attiva.")
            return
        state, state_data = saved_state
        expense_id = state_data.get("expense_id")
        if not isinstance(expense_id, str):
            raise ValueError("Conversation state has no expense identifier")
        expense = await asyncio.to_thread(self._database.get_expense, expense_id)
        if expense is None:
            await self._clear_state(user.id)
            await query.edit_message_text(
                "L’operazione non è più disponibile. Invia di nuovo lo scontrino."
            )
            return

        action, _, value = query.data.partition(":")
        if action != "receipt":
            return
        if value.startswith("cancel"):
            await self._cancel_pending(user.id, expense_id, expense.payload)
            await query.edit_message_text("Operazione annullata.")
        elif value.startswith("edit:"):
            field_name = value.split(":", 1)[1]
            if state != "awaiting_confirmation" or field_name not in _EDIT_FIELDS:
                await query.edit_message_text("Questa modifica non è disponibile.")
                return
            await self._save_state(
                user.id,
                "awaiting_edit",
                {"expense_id": expense_id, "edit_field": field_name},
            )
            await query.edit_message_text(self._prompt_for(field_name))
        elif value == "confirm":
            if state != "awaiting_confirmation":
                await query.edit_message_text("Controlla prima i dati richiesti.")
                return
            if self._bilancio is None:
                await query.edit_message_text(
                    "Il collegamento al portale non è ancora configurato. "
                    "Non è stato registrato nulla; lo scontrino resta in attesa."
                )
                return
            await self._begin_portal_selection(
                user.id,
                expense_id,
                expense.payload,
                query,
                context,
            )
        elif value == "manual_save":
            if (
                state != "awaiting_manual_final_confirmation"
                or expense.payload.get("manual_entry") is not True
            ):
                await query.edit_message_text("Questa conferma non è più disponibile.")
                return
            notes_text = state_data.get("portal_notes")
            await self._submit_expense(
                update,
                context,
                user.id,
                expense_id,
                expense.payload,
                state_data,
                notes_text if isinstance(notes_text, str) else "-",
            )
        elif value.startswith("direction:"):
            await self._select_direction(
                user.id,
                expense_id,
                state,
                state_data,
                value.split(":", 1)[1],
                query,
                context,
            )
        elif value.startswith("voice:"):
            await self._select_accounting_voice(
                user.id,
                expense_id,
                state,
                state_data,
                value.split(":", 1)[1],
                query,
                context,
            )
        elif value.startswith("choose:"):
            await self._select_portal_option(
                user.id,
                expense_id,
                expense.payload,
                state,
                state_data,
                value.split(":", 1)[1],
                query,
                context,
            )
        elif value == "no_category":
            if state != "awaiting_category":
                await query.edit_message_text("Questa scelta non è più disponibile.")
                return
            state_data["category"] = None
            await self._ask_for_notes(user.id, state_data)
            await query.edit_message_text("Scrivi una nota facoltativa oppure invia -.")

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
            await query.edit_message_text(
                "Non riesco a leggere le casse dal portale. Riprova più tardi."
            )
            return
        if not options:
            logger.warning("The portal returned no available accounts")
            await query.edit_message_text(
                "Il portale non mostra casse disponibili per questo utente."
            )
            return
        data: dict[str, object] = {
            "expense_id": expense_id,
            "transaction_date": transaction_date.isoformat(),
            "options": self._serialize_options(options),
        }
        await self._save_state(user_id, "awaiting_account", data)
        await query.edit_message_text(
            "Scegli la cassa:",
            reply_markup=self._option_keyboard(options),
        )

    async def _select_direction(
        self,
        user_id: int,
        expense_id: str,
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
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Voci Scout",
                        callback_data="receipt:voice:Voci Scout",
                    ),
                    InlineKeyboardButton(
                        "Voci Ministero",
                        callback_data="receipt:voice:Voci Ministero",
                    ),
                ]
            ]
        )
        await query.edit_message_text(
            "Scegli il tipo di voce:",
            reply_markup=keyboard,
        )

    async def _select_accounting_voice(
        self,
        user_id: int,
        expense_id: str,
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
            await query.edit_message_text(
                "Non riesco a leggere le tipologie dal portale. Riprova più tardi."
            )
            return
        if not options:
            await query.edit_message_text(
                "Il portale non mostra tipologie disponibili per questa scelta."
            )
            return
        state_data.update(
            accounting_voice=accounting_voice,
            options=self._serialize_options(options),
        )
        await self._save_state(user_id, "awaiting_transaction_type", state_data)
        await query.edit_message_text(
            "Scegli la tipologia:",
            reply_markup=self._option_keyboard(options),
        )

    async def _select_portal_option(
        self,
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
        transaction_date = self._state_date(state_data)
        accounting_voice = str(state_data.get("accounting_voice", "Voci Scout"))

        try:
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
                    "Vuoi registrare un’entrata o un’uscita?",
                    reply_markup=keyboard,
                )
                return
            if direction is None:
                await query.edit_message_text("Prima scegli entrata o uscita.")
                return
            transaction_type = self._option_from(state_data, "transaction_type") if state in {"awaiting_unit", "awaiting_category"} else selected
            if state == "awaiting_transaction_type":
                state_data.update(transaction_type=self._serialize_option(selected))
                next_options = await self._bilancio.list_units(
                    account,
                    direction,
                    selected,
                    transaction_date=transaction_date,
                    accounting_voice=accounting_voice,
                )
                next_state = "awaiting_unit"
                prompt = "Scegli l’unità:"
            elif state == "awaiting_unit":
                state_data.update(unit=self._serialize_option(selected))
                next_options = await self._bilancio.list_categories(
                    account,
                    direction,
                    transaction_type,
                    selected,
                    transaction_date=transaction_date,
                    accounting_voice=accounting_voice,
                )
                if not next_options:
                    state_data.update(category=None)
                    await self._ask_for_notes(user_id, state_data)
                    await query.edit_message_text(
                        "Il portale non mostra categorie. Scrivi una nota facoltativa "
                        "oppure invia -."
                    )
                    return
                next_state = "awaiting_category"
                prompt = "Scegli la categoria:"
            elif state == "awaiting_category":
                state_data.update(category=self._serialize_option(selected))
                await self._ask_for_notes(user_id, state_data)
                await query.edit_message_text(
                    "Scrivi una nota facoltativa oppure invia -."
                )
                return
            else:
                await query.edit_message_text("Questa scelta non è più disponibile.")
                return
        except BilancioClientError as error:
            logger.error("Could not load portal options: %s", type(error).__name__)
            debug_path = Path(self._settings.data_path) / "debug"
            await self._notify_admin(
                context,
                f"Non riesco a leggere le opzioni AGESCI. Controlla {debug_path} "
                "per eventuali schermate diagnostiche.",
                error,
            )
            await query.edit_message_text(
                "Non riesco a leggere le opzioni dal portale. Riprova più tardi."
            )
            return
        if not next_options:
            await query.edit_message_text(
                "Il portale non mostra opzioni disponibili per questa scelta."
            )
            return
        state_data["options"] = self._serialize_options(next_options)
        await self._save_state(user_id, next_state, state_data)
        await query.edit_message_text(
            prompt,
            reply_markup=self._option_keyboard(
                next_options,
                include_no_category=next_state == "awaiting_category",
            ),
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
        if self._bilancio is None:
            await message.reply_text(
                "Il collegamento al portale non è ancora configurato. Non è stato "
                "registrato nulla; lo scontrino resta in attesa."
            )
            return
        try:
            receipt = self._receipt_from_payload(payload)
            account = self._option_from(state_data, "account")
            transaction_type = self._option_from(state_data, "transaction_type")
            unit = self._option_from(state_data, "unit")
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
                unit=self._serialize_option(unit),
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
            result = await self._bilancio.create_expense(
                portal_expense,
                dry_run=self._settings.dry_run,
            )
            if not result.verified:
                raise BilancioClientError("Portal result could not be verified")
            if self._settings.dry_run and not result.dry_run:
                await asyncio.to_thread(
                    self._database.update_expense_status,
                    expense_id,
                    "failed",
                )
                await self._clear_state(user_id)
                await message.reply_text(
                    "Il portale non ha confermato la modalità di prova. Per sicurezza "
                    "non ripeterò l’operazione: avvisa l’amministratore e controlla "
                    "il portale prima di riprovare."
                )
                return
        except (BilancioClientError, OSError, ValueError, KeyError) as error:
            logger.error("Expense submission failed: %s", type(error).__name__)
            debug_path = Path(self._settings.data_path) / "debug"
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
                if payload.get("manual_entry") is True:
                    await message.reply_text(
                        "Non ho potuto verificare la registrazione manuale. "
                        "L’operazione è in coda per un nuovo tentativo."
                    )
                else:
                    await message.reply_text(
                        "Non ho potuto verificare la registrazione. La ricevuta è "
                        "conservata sul NAS e l’operazione è in coda per un nuovo tentativo."
                    )
            else:
                await self._clear_state(user_id)
                await message.reply_text(
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
        if result.dry_run:
            if payload.get("manual_entry") is True:
                await message.reply_text(
                    "Prova manuale completata: il portale non ha salvato la spesa."
                )
            else:
                await message.reply_text(
                    "Prova completata: il portale non ha salvato la spesa. "
                    "La ricevuta è stata archiviata sul NAS."
                )
        elif payload.get("manual_entry") is True:
            await message.reply_text(
                "Spesa manuale registrata e verificata nel portale. "
                "Non è stato archiviato uno scontrino."
            )
        else:
            await message.reply_text(
                "Spesa registrata e verificata nel portale. "
                "La ricevuta è stata archiviata sul NAS."
            )

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

    @staticmethod
    def _manual_confirmation_text(payload: dict[str, object]) -> str:
        return (
            "Controlla la spesa manuale:\n"
            f"Data: {payload['date']}\n"
            f"Descrizione: {payload['merchant']}\n"
            f"Importo: {payload['total_amount']} EUR\n\n"
            "Non verrà usata l’AI e non verrà archiviato uno scontrino. "
            "Dopo questa conferma sceglierai le opzioni del portale."
        )

    def _manual_final_confirmation_text(
        self,
        payload: dict[str, object],
        state_data: dict[str, object],
    ) -> str:
        account = self._option_from(state_data, "account").label
        transaction_type = self._option_from(
            state_data,
            "transaction_type",
        ).label
        unit = self._option_from(state_data, "unit").label
        category_data = state_data.get("category")
        category = (
            PortalOption(**category_data).label
            if isinstance(category_data, dict)
            else "nessuna"
        )
        direction = (
            "Entrata"
            if state_data.get("direction") == TransactionDirection.INCOME.value
            else "Uscita"
        )
        action = (
            "SIMULAZIONE: premendo il pulsante il portale NON salverà la spesa."
            if self._settings.dry_run
            else "ATTENZIONE: premendo il pulsante inserirai DAVVERO la spesa."
        )
        return (
            "Ultimo controllo prima di procedere:\n"
            f"Data: {payload['date']}\n"
            f"Descrizione: {payload['merchant']}\n"
            f"Importo: {payload['total_amount']} EUR\n"
            f"Cassa: {account}\n"
            f"Operazione: {direction}\n"
            f"Voci: {state_data.get('accounting_voice', 'Voci Scout')}\n"
            f"Tipologia: {transaction_type}\n"
            f"Unità: {unit}\n"
            f"Categoria: {category}\n"
            f"Note: {state_data.get('portal_notes') or 'nessuna'}\n\n"
            f"{action}"
        )

    def _manual_final_confirmation_keyboard(self) -> InlineKeyboardMarkup:
        button_text = (
            "Esegui prova simulata"
            if self._settings.dry_run
            else "CONFERMA INSERIMENTO REALE"
        )
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
    ) -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton(option.label[:60], callback_data=f"receipt:choose:{index}")]
            for index, option in enumerate(options)
        ]
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
