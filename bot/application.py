from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import time as day_time, timezone
from pathlib import Path

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    MenuButtonCommands,
    ReplyKeyboardMarkup,
    Update,
)
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
from bilancio.client import (
    BilancioClient,
    BilancioClientError,
    TransactionDirection,
)
from bilancio.playwright_client import ManualPortalLoginRequired
from config import Settings
from db.database import Database, StoredExpense
from storage.archive import FileArchive
from storage.pending import PendingReceiptStore

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "📋 Comandi disponibili\n\n"
    "/start - inizia a usare il bot\n"
    "/help - mostra questo aiuto\n"
    "/ultime - mostra le ultime 10 spese\n"
    "/annulla - annulla l’operazione in corso\n"
    "/coda - mostra le spese in attesa o da verificare\n"
    "/riprova - ritenta le spese in coda\n"
    "/testspesa - inserisce una spesa manuale (solo amministratore)\n"
    "/testbilancio - controlla il portale (solo amministratore)"
    "\n/aggiornaopzioni - aggiorna le scelte AGESCI (solo amministratore)"
)

USER_COMMANDS = (
    BotCommand("start", "Apri il menu del bot"),
    BotCommand("help", "Guida e comandi"),
    BotCommand("ultime", "Le tue ultime spese"),
    BotCommand("annulla", "Annulla l’operazione in corso"),
    BotCommand("coda", "Operazioni in attesa"),
    BotCommand("riprova", "Ritenta le operazioni in coda"),
)
ADMIN_COMMANDS = (
    *USER_COMMANDS,
    BotCommand("testspesa", "Inserisci una spesa senza foto"),
    BotCommand("testbilancio", "Verifica il collegamento AGESCI"),
    BotCommand("aggiornaopzioni", "Rileggi casse e tipologie dal portale"),
)


async def configure_bot_menu(application: Application) -> None:
    await application.bot.set_my_commands(USER_COMMANDS)
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())


def create_application(
    settings: Settings,
    database: Database,
    *,
    analyzer: ReceiptAnalyzer | None = None,
    bilancio_client: BilancioClient | None = None,
) -> Application:
    application = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(configure_bot_menu)
        .build()
    )
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
        application.job_queue.run_once(
            preload_options_job,
            when=10,
            name="portal-options-preload",
        )
        application.job_queue.run_daily(
            preload_options_job,
            time=day_time(hour=2, minute=30, tzinfo=timezone.utc),
            name="portal-options-nightly-refresh",
            data={"refresh": True},
        )
    application.add_handler(TypeHandler(Update, authorize_update), group=-1)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("annulla", cancel_command))
    application.add_handler(CommandHandler("ultime", latest_expenses_command))
    application.add_handler(CommandHandler("coda", queue_command))
    application.add_handler(CommandHandler("riprova", retry_command))
    application.add_handler(CommandHandler("testspesa", manual_expense_command))
    application.add_handler(CommandHandler("testbilancio", test_bilancio_command))
    application.add_handler(CommandHandler("aggiornaopzioni", refresh_options_command))
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
    settings: Settings = context.application.bot_data["settings"]
    rows = [["/ultime", "/coda"], ["/help", "/annulla"]]
    if update.effective_user is not None and update.effective_user.id == settings.admin_user_id:
        rows.insert(0, ["/testspesa", "/testbilancio"])
        await context.bot.set_my_commands(
            ADMIN_COMMANDS,
            scope=BotCommandScopeChat(chat_id=settings.admin_user_id),
        )
    mode = "🧪 Modalità SIMULATA: nessun salvataggio sul portale." if settings.dry_run else (
        "⚠️ Modalità REALE: la conferma finale può salvare sul portale."
    )
    await update.message.reply_text(
        "🧾 Benvenuto nel tuo archivio spese!\n\n"
        "📷 Inviami la foto o il PDF di uno scontrino.\n"
        "✍️ Per una spesa senza foto usa /testspesa (amministratore).\n"
        "Prima di procedere ti mostrerò i dati da controllare.\n\n"
        f"{mode}\n\n"
        "Scegli un comando qui sotto oppure scrivi / per aprire i suggerimenti.",
        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True),
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


async def manual_expense_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    workflow: ReceiptWorkflow = context.application.bot_data["receipt_workflow"]
    await workflow.start_manual_expense(update, context)


async def refresh_options_command(
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
        await update.message.reply_text("Il collegamento al portale non è configurato.")
        return
    await client.clear_options_cache()
    job_queue = getattr(context.application, "job_queue", None)
    if job_queue is not None:
        job_queue.run_once(
            preload_options_job,
            when=1,
            name="portal-options-manual-preload",
        )
    await update.message.reply_text(
        "🔄 Cache svuotata. Rileggo casse e tipologie dal portale in "
        "sottofondo: per qualche minuto le scelte potranno essere più lente. "
        "Nessuna spesa è stata inserita.\n"
        "Se avevi già aperto un elenco, usa /annulla e ricomincia."
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


async def preload_options_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    client: BilancioClient | None = context.application.bot_data["bilancio_client"]
    if client is None:
        return
    job = getattr(context, "job", None)
    data = getattr(job, "data", None)
    if isinstance(data, dict) and data.get("refresh") is True:
        await client.clear_options_cache()
    await preload_portal_options(client)


async def preload_portal_options(client: BilancioClient) -> int:
    """Read accounts and Voci Scout types so Telegram menus open from cache."""
    try:
        accounts = await client.list_accounts()
    except BilancioClientError as error:
        logger.warning("Portal options preload failed: %s", type(error).__name__)
        return 0
    loaded = 0
    for account in accounts:
        for direction in TransactionDirection:
            try:
                await client.list_transaction_types(account, direction)
            except BilancioClientError as error:
                logger.warning(
                    "Portal transaction types preload failed: %s",
                    type(error).__name__,
                )
                continue
            loaded += 1
    logger.info("Portal options preloaded for %d account directions", loaded)
    return loaded


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
