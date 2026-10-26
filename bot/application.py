from __future__ import annotations

import logging
from collections.abc import Sequence

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from bot.access import is_authorized_user
from bot.receipt_workflow import ReceiptAnalyzer, ReceiptWorkflow
from config import Settings
from db.database import Database, StoredExpense
from storage.archive import FileArchive
from storage.pending import PendingReceiptStore
from bilancio.client import BilancioClient

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Comandi disponibili:\n"
    "/start - inizia a usare il bot\n"
    "/help - mostra questo aiuto\n"
    "/ultime - mostra le ultime 10 spese\n"
    "/annulla - annulla l’operazione in corso"
)


def create_application(
    settings: Settings,
    database: Database,
    *,
    analyzer: ReceiptAnalyzer | None = None,
    bilancio_client: BilancioClient | None = None,
) -> Application:
    application = Application.builder().token(settings.bot_token).build()
    workflow = ReceiptWorkflow(
        settings=settings,
        database=database,
        analyzer=analyzer,
        pending_store=PendingReceiptStore("/data/pending"),
        archive=FileArchive(settings.storage_path),
        bilancio_client=bilancio_client,
    )
    application.bot_data["settings"] = settings
    application.bot_data["database"] = database
    application.bot_data["receipt_workflow"] = workflow
    application.add_handler(TypeHandler(Update, authorize_update), group=-1)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("annulla", cancel_command))
    application.add_handler(CommandHandler("ultime", latest_expenses_command))
    application.add_handler(
        MessageHandler(
            filters.PHOTO | filters.Document.PDF,
            receive_receipt,
        )
    )
    application.add_handler(
        CallbackQueryHandler(receipt_callback, pattern=r"^receipt:")
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, receipt_text)
    )
    return application


async def authorize_update(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    user = update.effective_user
    user_id = user.id if user is not None else None
    if not is_authorized_user(user_id, settings.allowed_user_ids):
        logger.warning("Ignored unauthorized Telegram update")
        raise ApplicationHandlerStop


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "Ciao! Inviami la foto o il PDF di uno scontrino. "
        "Prima di registrare una spesa, ti mostrerò i dati da controllare."
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is not None:
        await update.message.reply_text(HELP_TEXT)


async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None or update.effective_user is None:
        return
    workflow: ReceiptWorkflow = context.application.bot_data["receipt_workflow"]
    was_cancelled = await workflow.cancel_current(update.effective_user.id)
    if was_cancelled:
        await update.message.reply_text("Operazione annullata.")
    else:
        await update.message.reply_text("Non c’è un’operazione in corso da annullare.")


async def receive_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    workflow: ReceiptWorkflow = context.application.bot_data["receipt_workflow"]
    await workflow.receive_receipt(update, context)


async def receipt_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    workflow: ReceiptWorkflow = context.application.bot_data["receipt_workflow"]
    await workflow.handle_text(update, context)


async def receipt_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    workflow: ReceiptWorkflow = context.application.bot_data["receipt_workflow"]
    await workflow.handle_callback(update, context)


async def latest_expenses_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None or update.effective_user is None:
        return
    database: Database = context.application.bot_data["database"]
    expenses = database.list_recent_expenses(update.effective_user.id, limit=10)
    if not expenses:
        await update.message.reply_text("Non ci sono ancora spese registrate.")
        return
    await update.message.reply_text(_format_recent_expenses(expenses))


def _format_recent_expenses(expenses: Sequence[StoredExpense]) -> str:
    lines = ["Ultime spese:"]
    for expense in expenses:
        payload = expense.payload
        transaction_date = _safe_field(payload.get("date"))
        merchant = _safe_field(payload.get("merchant"))
        amount = _safe_field(payload.get("total_amount"))
        currency = _safe_field(payload.get("currency"))
        account = _safe_field(payload.get("account"))
        lines.append(
            f"{transaction_date} · {merchant} · {amount} {currency} · {account}"
        )
    return "\n".join(lines)


def _safe_field(value: object) -> str:
    if value is None:
        return "—"
    return " ".join(str(value).split())[:80] or "—"


async def notify_admin(
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    message: str,
) -> None:
    try:
        await context.bot.send_message(
            chat_id=settings.admin_user_id,
            text=message,
        )
    except TelegramError:
        logger.error("Failed to send Telegram admin notification")
