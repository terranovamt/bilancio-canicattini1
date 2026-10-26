from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path

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
from bot.retry_service import ExpenseRetryService
from bilancio.client import BilancioClient, BilancioClientError
from bilancio.playwright_client import ManualPortalLoginRequired
from config import Settings
from db.database import Database, StoredExpense
from storage.archive import FileArchive
from storage.pending import PendingReceiptStore

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Comandi disponibili:\n"
    "/start - inizia a usare il bot\n"
    "/help - mostra questo aiuto\n"
    "/ultime - mostra le ultime 10 spese\n"
    "/annulla - annulla l’operazione in corso\n"
    "/coda - mostra le spese in attesa o da verificare\n"
    "/riprova - ritenta le spese in coda\n"
    "/testbilancio - controlla il portale (solo amministratore)"
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
        pending_store=PendingReceiptStore(Path(settings.data_path) / "pending"),
        archive=FileArchive(settings.storage_path),
        bilancio_client=bilancio_client,
    )
    application.bot_data["settings"] = settings
    application.bot_data["database"] = database
    application.bot_data["receipt_workflow"] = workflow
    application.bot_data["bilancio_client"] = bilancio_client
    application.bot_data["retry_service"] = None
    if bilancio_client is not None:
        retry_service = ExpenseRetryService(
            settings=settings,
            database=database,
            bilancio_client=bilancio_client,
            archive=FileArchive(settings.storage_path),
            pending_store=PendingReceiptStore(Path(settings.data_path) / "pending"),
        )
        application.bot_data["retry_service"] = retry_service
        if application.job_queue is None:
            raise RuntimeError("python-telegram-bot job queue is not available")
        application.job_queue.run_repeating(
            retry_queue_job,
            interval=300,
            first=30,
            name="expense-retry-queue",
        )
    application.add_handler(TypeHandler(Update, authorize_update), group=-1)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("annulla", cancel_command))
    application.add_handler(CommandHandler("ultime", latest_expenses_command))
    application.add_handler(CommandHandler("coda", queue_command))
    application.add_handler(CommandHandler("riprova", retry_command))
    application.add_handler(CommandHandler("testbilancio", test_bilancio_command))
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
    application.add_error_handler(telegram_error_handler)
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


async def queue_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None or update.effective_user is None:
        return
    database: Database = context.application.bot_data["database"]
    expenses = await asyncio.to_thread(
        database.list_retryable_expenses,
        update.effective_user.id,
    )
    if not expenses:
        await update.message.reply_text("Non ci sono spese in coda.")
        return
    lines = ["Spese da ritentare o verificare:"]
    for expense in expenses:
        lines.append(
            f"{_safe_field(expense.payload.get('date'))} · "
            f"{_safe_field(expense.payload.get('merchant'))} · "
            f"{_safe_field(expense.payload.get('total_amount'))} · "
            f"{'in coda' if expense.status == 'queued' else 'da verificare'}"
        )
    await update.message.reply_text("\n".join(lines))


async def retry_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None or update.effective_user is None:
        return
    service: ExpenseRetryService | None = context.application.bot_data["retry_service"]
    if service is None:
        await update.message.reply_text(
            "Il collegamento al portale non è ancora configurato."
        )
        return
    database: Database = context.application.bot_data["database"]
    requested = await asyncio.to_thread(
        database.request_retry_for_user,
        update.effective_user.id,
    )
    if requested == 0:
        await update.message.reply_text("Non ci sono spese da ritentare.")
        return
    summary = await service.process_due(
        context.bot,
        telegram_user_id=update.effective_user.id,
    )
    await update.message.reply_text(
        f"Tentativo completato: {summary.succeeded} riuscite, "
        f"{summary.pending} ancora in attesa, {summary.failed} da verificare."
    )


async def test_bilancio_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None or update.effective_user is None:
        return
    settings: Settings = context.application.bot_data["settings"]
    if update.effective_user.id != settings.admin_user_id:
        await update.message.reply_text("Comando riservato all’amministratore.")
        return
    client: BilancioClient | None = context.application.bot_data["bilancio_client"]
    if client is None:
        await update.message.reply_text(
            "Il collegamento al portale non è ancora configurato."
        )
        return
    try:
        await client.health_check()
    except ManualPortalLoginRequired as error:
        logger.error("AGESCI manual login is required")
        debug_path = Path(settings.data_path) / "debug"
        await update.message.reply_text(
            "Il portale richiede una verifica manuale, per esempio un codice "
            "aggiuntivo. Non ho inserito spese. La diagnostica, se presente, "
            f"è in {debug_path} ({Path(error.diagnostic_path).name if error.diagnostic_path else 'nessun file'})."
        )
        return
    except BilancioClientError as error:
        logger.error("AGESCI health check failed: %s", type(error).__name__)
        debug_path = Path(settings.data_path) / "debug"
        await update.message.reply_text(
            "Non riesco ad accedere al portale o ad aprire il modulo. "
            f"Non ho inserito spese. Controlla {debug_path} per la diagnostica."
        )
        return
    await update.message.reply_text(
        "Controllo completato: accesso e modulo disponibili. Non ho inserito spese."
    )


async def retry_queue_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    service: ExpenseRetryService | None = context.application.bot_data["retry_service"]
    if service is not None:
        await service.process_due(context.bot)


async def telegram_error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    error = context.error
    if error is not None:
        logger.error("Unhandled Telegram update error: %s", type(error).__name__)
    settings: Settings = context.application.bot_data["settings"]
    await notify_admin(
        context,
        settings,
        "Si è verificato un errore durante l’elaborazione di un messaggio.",
    )


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
