from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import date, time as day_time, timezone
from pathlib import Path

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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

from bot import messages as texts
from bot.access import is_authorized_user
from bot.receipt_workflow import ReceiptAnalyzer, ReceiptWorkflow
from bot.retry_service import ExpenseRetryService
from bot.summary import (
    format_ledger_summary,
    reference_date_for,
    scout_year_name,
    scout_year_start,
)
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

USER_HELP_TEXT = (
    texts.USER_HELP
)
ADMIN_HELP_TEXT = (
    texts.ADMIN_HELP.format(user_help=USER_HELP_TEXT)
)

USER_COMMANDS = (
    BotCommand("start", texts.COMMAND_START),
    BotCommand("help", texts.COMMAND_HELP),
    BotCommand("ultime", texts.COMMAND_LATEST),
    BotCommand("riepilogo", texts.COMMAND_SUMMARY),
    BotCommand("annulla", texts.COMMAND_CANCEL),
    BotCommand("coda", texts.COMMAND_QUEUE),
    BotCommand("riprova", texts.COMMAND_RETRY),
)
ADMIN_COMMANDS = (
    *USER_COMMANDS,
    BotCommand("testspesa", texts.COMMAND_MANUAL),
    BotCommand("testbilancio", texts.COMMAND_HEALTH),
    BotCommand("aggiornaopzioni", texts.COMMAND_REFRESH),
    BotCommand("pulisci", texts.COMMAND_CLEAN),
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
    application.add_handler(CommandHandler("riepilogo", summary_command))
    application.add_handler(
        CallbackQueryHandler(summary_callback, pattern=r"^summary:")
    )
    application.add_handler(CommandHandler("coda", queue_command))
    application.add_handler(CommandHandler("riprova", retry_command))
    application.add_handler(CommandHandler("testspesa", manual_expense_command))
    application.add_handler(CommandHandler("testbilancio", test_bilancio_command))
    application.add_handler(CommandHandler("aggiornaopzioni", refresh_options_command))
    application.add_handler(CommandHandler("pulisci", clean_command))
    application.add_handler(
        CallbackQueryHandler(clean_callback, pattern=r"^admin:clean:")
    )
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
    rows = texts.USER_KEYBOARD
    if _is_admin(update, settings):
        rows = texts.ADMIN_KEYBOARD
        await context.bot.set_my_commands(
            ADMIN_COMMANDS,
            scope=BotCommandScopeChat(chat_id=settings.admin_user_id),
        )
        mode = texts.MODE_TRIAL if settings.dry_run else (
            texts.MODE_REAL
        )
        text = (
            texts.ADMIN_START.format(mode=f"{mode}\n{texts.MANUAL_MODE_NOTICE}")
        )
    else:
        text = (
            texts.USER_START
        )
    await update.message.reply_text(
        text,
        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True),
    )


def _is_admin(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    return user is not None and user.id == settings.admin_user_id


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None:
        return
    settings: Settings | None = context.application.bot_data.get("settings")
    is_admin = settings is not None and _is_admin(update, settings)
    await update.message.reply_text(ADMIN_HELP_TEXT if is_admin else USER_HELP_TEXT)


async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None or update.effective_user is None:
        return
    workflow: ReceiptWorkflow = context.application.bot_data["receipt_workflow"]
    was_cancelled = await workflow.cancel_current(update.effective_user.id)
    if was_cancelled:
        await update.message.reply_text(texts.CANCELLED)
    else:
        await update.message.reply_text(texts.NOTHING_TO_CANCEL)


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
    expenses = [
        expense
        for expense in await asyncio.to_thread(
            database.list_recent_expenses,
            update.effective_user.id,
            30,
        )
        if expense.status in _STATUS_LABELS
    ][:10]
    if not expenses:
        await update.message.reply_text(texts.LATEST_EMPTY)
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
        await update.message.reply_text(texts.QUEUE_EMPTY)
        return
    lines = [texts.QUEUE_HEADER]
    for expense in expenses:
        lines.append(texts.expense_row(expense.payload, expense.status, queued=True))
    lines.extend(("", texts.QUEUE_FOOTER))
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
            texts.PORTAL_UNAVAILABLE
        )
        return
    database: Database = context.application.bot_data["database"]
    requested = await asyncio.to_thread(
        database.request_retry_for_user,
        update.effective_user.id,
    )
    if requested == 0:
        await update.message.reply_text(texts.NOTHING_TO_RETRY)
        return
    summary = await service.process_due(
        context.bot,
        telegram_user_id=update.effective_user.id,
    )
    await update.message.reply_text(
        texts.RETRY_DONE_WITH_TRIALS.format(
            saved=summary.succeeded - summary.trials, trials=summary.trials,
            pending=summary.pending, failed=summary.failed,
        ) if summary.trials else texts.RETRY_DONE.format(
            saved=summary.succeeded, pending=summary.pending, failed=summary.failed,
        )
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
        await update.message.reply_text(texts.ADMIN_ONLY)
        return
    client: BilancioClient | None = context.application.bot_data["bilancio_client"]
    if client is None:
        await update.message.reply_text(texts.PORTAL_UNAVAILABLE)
        return
    await client.clear_options_cache()
    job_queue = getattr(context.application, "job_queue", None)
    if job_queue is not None:
        job_queue.run_once(
            preload_options_job,
            when=1,
            name="portal-options-manual-preload",
            data={"notify_chat_id": update.effective_user.id},
        )
    await update.message.reply_text(
        texts.REFRESH_PROGRESS
    )


_SUMMARY_RUNNING_KEY = "ledger_summary_running"
_SUMMARY_FAILED_TEXT = (
    texts.SUMMARY_FAILED
)


def _summary_progress_text(start_year: int) -> str:
    return texts.SUMMARY_PROGRESS


def _summary_keyboard(start_year: int, today: date) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            texts.PREVIOUS_YEAR.format(year=scout_year_name(start_year - 1)),
            callback_data=f"summary:{start_year - 1}",
        )
    ]
    if start_year < scout_year_start(today):
        buttons.append(
            InlineKeyboardButton(
                texts.NEXT_YEAR.format(year=scout_year_name(start_year + 1)),
                callback_data=f"summary:{start_year + 1}",
            )
        )
    return InlineKeyboardMarkup(
        [
            buttons,
            [InlineKeyboardButton(texts.REFRESH_BUTTON, callback_data=f"summary:{start_year}")],
            [InlineKeyboardButton(texts.DETAILS_BUTTON, callback_data=f"summary:details:{start_year}")],
        ]
    )


def _schedule_summary(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    message_id: int,
    start_year: int,
    details: bool = False,
) -> None:
    context.application.bot_data[_SUMMARY_RUNNING_KEY] = True
    context.application.job_queue.run_once(
        ledger_summary_job,
        when=0,
        name="ledger-summary",
        data={
            "chat_id": chat_id, "message_id": message_id, "start_year": start_year,
            **({"details": True} if details else {}),
        },
    )


async def summary_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None:
        return
    if context.application.bot_data.get("bilancio_client") is None:
        await update.message.reply_text(texts.PORTAL_UNAVAILABLE)
        return
    if context.application.bot_data.get(_SUMMARY_RUNNING_KEY):
        await update.message.reply_text(texts.SUMMARY_BUSY)
        return
    start_year = scout_year_start(date.today())
    progress = await update.message.reply_text(_summary_progress_text(start_year))
    _schedule_summary(
        context,
        chat_id=progress.chat_id,
        message_id=progress.message_id,
        start_year=start_year,
    )


async def summary_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if query is None or query.message is None:
        return
    current_start = scout_year_start(date.today())
    try:
        parts = str(query.data).split(":")
        details = len(parts) == 3 and parts[1] == "details"
        if not (len(parts) == 2 or details):
            raise ValueError("Invalid summary callback")
        start_year = int(parts[-1])
    except (IndexError, ValueError):
        await query.answer()
        return
    if not current_start - 20 <= start_year <= current_start:
        await query.answer()
        return
    if context.application.bot_data.get("bilancio_client") is None:
        await query.answer(texts.PORTAL_UNAVAILABLE)
        return
    if context.application.bot_data.get(_SUMMARY_RUNNING_KEY):
        await query.answer(texts.SUMMARY_BUSY)
        return
    await query.answer()
    await query.edit_message_text(_summary_progress_text(start_year))
    _schedule_summary(
        context,
        chat_id=query.message.chat_id,
        message_id=query.message.message_id,
        start_year=start_year,
        details=details,
    )


async def ledger_summary_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    chat_id: int = data["chat_id"]
    message_id: int = data["message_id"]
    start_year: int = data["start_year"]
    today = date.today()
    keyboard = _summary_keyboard(start_year, today)
    try:
        client: BilancioClient = context.application.bot_data["bilancio_client"]
        try:
            ledgers = await client.read_ledgers(
                transaction_date=reference_date_for(start_year, today)
            )
        except BilancioClientError as error:
            logger.warning("Ledger summary failed: %s", type(error).__name__)
            messages = [_SUMMARY_FAILED_TEXT]
        else:
            messages = format_ledger_summary(ledgers, start_year, details=data.get("details", False))
        for index, text in enumerate(messages):
            markup = keyboard if index == len(messages) - 1 else None
            if index == 0:
                await context.bot.edit_message_text(
                    text,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=markup,
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=markup,
                )
    except TelegramError:
        logger.error("Could not send ledger summary")
    finally:
        context.application.bot_data[_SUMMARY_RUNNING_KEY] = False

async def test_bilancio_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None or update.effective_user is None:
        return
    settings: Settings = context.application.bot_data["settings"]
    if update.effective_user.id != settings.admin_user_id:
        await update.message.reply_text(texts.ADMIN_ONLY)
        return
    client: BilancioClient | None = context.application.bot_data["bilancio_client"]
    if client is None:
        await update.message.reply_text(
            texts.PORTAL_UNAVAILABLE
        )
        return
    try:
        await client.health_check()
    except ManualPortalLoginRequired as error:
        logger.error("AGESCI manual login is required")
        debug_path = Path(settings.data_path) / "debug"
        await update.message.reply_text(
            texts.HEALTH_MANUAL.format(
                folder=debug_path,
                diagnostic_file=f" ({Path(error.diagnostic_path).name})" if error.diagnostic_path else "",
            )
        )
        return
    except BilancioClientError as error:
        logger.error("AGESCI health check failed: %s", type(error).__name__)
        debug_path = Path(settings.data_path) / "debug"
        await update.message.reply_text(
            texts.HEALTH_FAILED.format(folder=debug_path)
        )
        return
    await update.message.reply_text(
        texts.HEALTH_OK
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
    summary = await preload_portal_options(client)
    chat_id = data.get("notify_chat_id") if isinstance(data, dict) else None
    if isinstance(chat_id, int):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=_format_preload_summary(summary),
            )
        except TelegramError:
            logger.error("Could not send options refresh summary")


async def preload_portal_options(
    client: BilancioClient,
) -> list[tuple[str, TransactionDirection, int | None]] | None:
    """Read accounts and Voci Scout types so Telegram menus open from cache."""
    try:
        accounts = await client.list_accounts()
    except BilancioClientError as error:
        logger.warning("Portal options preload failed: %s", type(error).__name__)
        return None
    summary: list[tuple[str, TransactionDirection, int | None]] = []
    for account in accounts:
        for direction in TransactionDirection:
            try:
                types = await client.list_transaction_types(account, direction)
            except BilancioClientError as error:
                logger.warning(
                    "Portal transaction types preload failed: %s",
                    type(error).__name__,
                )
                summary.append((account.label, direction, None))
                continue
            summary.append((account.label, direction, len(types)))
    logger.info(
        "Portal options preloaded for %d account directions",
        sum(1 for item in summary if item[2] is not None),
    )
    return summary


def _format_preload_summary(
    summary: list[tuple[str, TransactionDirection, int | None]] | None,
) -> str:
    if summary is None:
        return (
            texts.REFRESH_FAILED
        )
    accounts: dict[str, dict[TransactionDirection, int | None]] = {}
    for label, direction, count in summary:
        accounts.setdefault(label, {})[direction] = count

    def describe(count: int | None) -> str:
        return texts.UNREAD_COUNT if count is None else str(count)

    lines = [texts.REFRESH_DONE.format(count=len(accounts))]
    for label, directions in accounts.items():
        lines.append(
            texts.REFRESH_ROW.format(account=label, income=describe(directions.get(TransactionDirection.INCOME)), expense=describe(directions.get(TransactionDirection.EXPENSE)))
        )
    if any(count is None for _, _, count in summary):
        lines.append(texts.REFRESH_PARTIAL)
    return "\n".join(lines)


async def clean_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None:
        return
    settings: Settings = context.application.bot_data["settings"]
    if not _is_admin(update, settings):
        await update.message.reply_text(texts.ADMIN_ONLY)
        return
    await update.message.reply_text(
        texts.CLEAN_PROMPT,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(texts.CLEAN_YES, callback_data="admin:clean:yes")],
                [InlineKeyboardButton(texts.CLEAN_NO, callback_data="admin:clean:no")],
            ]
        ),
    )


async def clean_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    settings: Settings = context.application.bot_data["settings"]
    if not _is_admin(update, settings):
        return
    if query.data != "admin:clean:yes":
        await query.edit_message_text(texts.CLEAN_CANCELLED)
        return
    database: Database = context.application.bot_data["database"]
    counts = await asyncio.to_thread(database.clear_history)
    data_path = Path(settings.data_path)
    removed_files = await asyncio.to_thread(
        _clear_runtime_files,
        data_path / "pending",
        data_path / "debug",
    )
    await asyncio.to_thread(_truncate_logs, data_path / "logs")
    logger.info("Local history cleared by the administrator")
    await query.edit_message_text(
        texts.CLEAN_DONE.format(expenses=counts['expenses'], pending=counts['retries'], files=removed_files)
    )


def _clear_runtime_files(*directories: Path) -> int:
    removed = 0
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.is_file():
                path.unlink()
                removed += 1
    return removed


def _truncate_logs(log_directory: Path) -> None:
    if not log_directory.is_dir():
        return
    for path in log_directory.glob("bot.log*"):
        if path.name == "bot.log":
            # The rotating handler keeps this file open, so empty it in place.
            with path.open("r+", encoding="utf-8") as handle:
                handle.truncate(0)
        elif path.is_file():
            path.unlink()


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
        texts.UNEXPECTED_ERROR,
    )


def _format_recent_expenses(expenses: Sequence[StoredExpense]) -> str:
    lines = [texts.LATEST_HEADER]
    for expense in expenses:
        lines.append(texts.expense_row(expense.payload, expense.status))
    return "\n".join(lines)


_STATUS_LABELS = {
    "submitted": texts.STATUS_SAVED,
    "dry_run": texts.STATUS_TRIAL,
    "queued": texts.STATUS_QUEUED,
    "failed": texts.STATUS_REVIEW,
    "confirmed": texts.STATUS_WORKING,
}


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
