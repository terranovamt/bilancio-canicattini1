from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Sequence
from datetime import date, datetime, time as day_time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    MessageEntity,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    BaseUpdateProcessor,
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
from bot.daily import daily_ledger_job, local_now
from bot.updates import (
    InstallOutcome,
    RunningVersion,
    UpdateStatusStore,
    UpdateTracking,
    describe_version,
    install_notice,
    pending_notices,
    progress_text,
    resolve_timezone,
    update_available,
)
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
from db.ledger_snapshot import compact_ledgers
from storage.archive import FileArchive
from storage.pending import PendingReceiptStore

logger = logging.getLogger(__name__)

USER_HELP_TEXT = texts.MENU_HELP
ADMIN_HELP_TEXT = texts.MENU_HELP + texts.MENU_ADMIN_HELP

USER_COMMANDS = (
    BotCommand("start", texts.COMMAND_START),
    BotCommand("help", texts.COMMAND_HELP),
    BotCommand("ultime", texts.COMMAND_LATEST),
    BotCommand("riepilogo", texts.COMMAND_SUMMARY),
    BotCommand("annulla", texts.COMMAND_CANCEL),
    BotCommand("coda", texts.COMMAND_QUEUE),
    BotCommand("riprova", texts.COMMAND_RETRY),
    BotCommand("entrata", texts.COMMAND_INCOME),
)
ADMIN_COMMANDS = (
    *USER_COMMANDS,
    BotCommand("manual", texts.COMMAND_MANUAL),
    BotCommand("testbilancio", texts.COMMAND_HEALTH),
    BotCommand("aggiornaopzioni", texts.COMMAND_REFRESH),
    BotCommand("aggiorna", texts.COMMAND_UPDATE),
    BotCommand("pulisci", texts.COMMAND_CLEAN),
)


class ResponsiveUpdateProcessor(BaseUpdateProcessor):
    def __init__(self) -> None:
        super().__init__(max_concurrent_updates=32)
        self._workflow_lock = asyncio.Lock()

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def do_process_update(
        self, update: object, coroutine: Awaitable[object]
    ) -> None:
        query = update.callback_query if isinstance(update, Update) else None
        if query is not None and query.data in {
            "menu:home", "menu:help", "menu:receipt", "menu:admin", "menu:version",
            "receipt:noop",
        }:
            await coroutine
            return
        message = update.message if isinstance(update, Update) else None
        if (
            message is not None
            and not message.entities
            and MENU_LABELS.get((message.text or "").strip()) in _READ_ONLY_ACTIONS
        ):
            await coroutine
            return
        if message is not None and message.text and message.entities:
            entity = message.entities[0]
            command = message.text.split(maxsplit=1)[0].split("@", 1)[0].lower()
            if (
                entity.type == MessageEntity.BOT_COMMAND
                and entity.offset == 0
                and command in {"/start", "/help"}
            ):
                await coroutine
                return
        # Keep state changes and submissions sequential, but let read-only help bypass them.
        async with self._workflow_lock:
            await coroutine


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
        .concurrent_updates(ResponsiveUpdateProcessor())
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
    application.bot_data["update_store"] = UpdateStatusStore(
        Path(settings.data_path) / "update"
    )
    application.bot_data["running_version"] = RunningVersion.from_env()
    if application.job_queue is None:
        raise RuntimeError("python-telegram-bot job queue is not available")
    if application.job_queue is not None:
        application.job_queue.run_daily(
            daily_ledger_job,
            time=day_time(hour=0, minute=0, tzinfo=ZoneInfo(settings.timezone)),
            name="portal-balances-daily",
        )
        application.job_queue.run_repeating(
            update_status_job,
            interval=60,
            first=20,
            name="nas-update-status",
        )
        application.job_queue.run_repeating(
            update_progress_job,
            interval=3,
            first=3,
            name="nas-update-progress",
        )
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
    application.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu:"))
    application.add_handler(CommandHandler("annulla", cancel_command))
    application.add_handler(CommandHandler("ultime", latest_expenses_command))
    application.add_handler(CommandHandler("riepilogo", summary_command))
    application.add_handler(
        CallbackQueryHandler(summary_callback, pattern=r"^summary:")
    )
    application.add_handler(CommandHandler("coda", queue_command))
    application.add_handler(CommandHandler("riprova", retry_command))
    application.add_handler(CommandHandler("entrata", manual_income_command))
    application.add_handler(CommandHandler("manual", manual_expense_command))
    application.add_handler(CommandHandler("testbilancio", test_bilancio_command))
    application.add_handler(CommandHandler("aggiornaopzioni", refresh_options_command))
    application.add_handler(CommandHandler("aggiorna", update_command))
    application.add_handler(
        CallbackQueryHandler(update_callback, pattern=r"^admin:update:")
    )
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
    application.add_handler(MessageHandler(filters.Text(list(MENU_LABELS)), menu_text))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, receipt_text)
    )
    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE
            & ~filters.TEXT
            & ~filters.PHOTO
            & ~filters.Document.PDF,
            unsupported_message,
        )
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
    if _is_admin(update, settings):
        await context.bot.set_my_commands(
            ADMIN_COMMANDS,
            scope=BotCommandScopeChat(chat_id=settings.admin_user_id),
        )
    await update.message.reply_text(
        _home_text(await _test_mode(context, settings), admin=_is_admin(update, settings)),
        reply_markup=_menu_keyboard(_is_admin(update, settings)),
    )


async def _test_mode(context: ContextTypes.DEFAULT_TYPE, settings: Settings) -> bool:
    database: Database = context.application.bot_data["database"]
    return await asyncio.to_thread(database.get_test_mode, default=settings.dry_run)


def _home_text(test_mode: bool, *, admin: bool = False) -> str:
    return texts.HOME_TEXT + (f"\n\n{texts.MODE_TRIAL}" if test_mode and admin else "")


def _navigation_rows(*, admin: bool = False) -> list[list[InlineKeyboardButton]]:
    # The main menu lives in the persistent reply keyboard; only admin tools need "Back".
    if admin:
        return [[InlineKeyboardButton(texts.BACK_BUTTON, callback_data="menu:admin")]]
    return []


def _navigation_keyboard(*, admin: bool = False) -> InlineKeyboardMarkup | None:
    rows = _navigation_rows(admin=admin)
    return InlineKeyboardMarkup(rows) if rows else None


def _menu_keyboard(admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [texts.MENU_RECEIPT_BUTTON, texts.MENU_INCOME_BUTTON],
        [texts.MENU_SUMMARY_BUTTON, texts.MENU_LATEST_BUTTON],
        [texts.MENU_QUEUE_BUTTON, texts.MENU_HELP_BUTTON],
        [texts.MENU_CANCEL_BUTTON],
    ]
    if admin:
        rows[-1].append(texts.MENU_ADMIN_BUTTON)
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=texts.MENU_PLACEHOLDER,
    )


MENU_LABELS = {
    texts.MENU_RECEIPT_BUTTON: "receipt",
    texts.MENU_INCOME_BUTTON: "income",
    texts.MENU_SUMMARY_BUTTON: "summary",
    texts.MENU_LATEST_BUTTON: "latest",
    texts.MENU_QUEUE_BUTTON: "queue",
    texts.MENU_HELP_BUTTON: "help",
    texts.MENU_CANCEL_BUTTON: "cancel",
    texts.MENU_ADMIN_BUTTON: "admin",
    texts.HOME_BUTTON: "home",
    texts.MENU_MANUAL_BUTTON: "manual",
    texts.MENU_TEST_ON_BUTTON: "test_on",
    texts.MENU_TEST_OFF_BUTTON: "test_off",
    texts.MENU_HEALTH_BUTTON: "health",
    texts.MENU_OPTIONS_BUTTON: "options",
    texts.MENU_VERSION_BUTTON: "version",
    texts.MENU_UPDATE_BUTTON: "update",
    texts.MENU_CLEAN_BUTTON: "clean",
}
_READ_ONLY_ACTIONS = {"home", "help", "receipt", "admin", "version"}
_ADMIN_ACTIONS = {
    "admin", "manual", "health", "options", "version", "update", "clean", "test_on", "test_off",
}


def _admin_keyboard(test_mode: bool = False) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        [texts.MENU_MANUAL_BUTTON],
        [texts.MENU_TEST_OFF_BUTTON if test_mode else texts.MENU_TEST_ON_BUTTON],
        [texts.MENU_HEALTH_BUTTON, texts.MENU_OPTIONS_BUTTON],
        [texts.MENU_VERSION_BUTTON, texts.MENU_UPDATE_BUTTON],
        [texts.MENU_CLEAN_BUTTON],
        [texts.HOME_BUTTON],
    ], resize_keyboard=True, is_persistent=True,
        input_field_placeholder=texts.MENU_PLACEHOLDER)


async def _edit_menu_screen(
    query: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup | None
) -> None:
    if (
        getattr(query.message, "text", None) == text
        and getattr(query.message, "reply_markup", None) == keyboard
    ):
        return
    await query.edit_message_text(text, reply_markup=keyboard)


async def _show_command_screen(
    update: Update, text: str, keyboard: InlineKeyboardMarkup | ReplyKeyboardMarkup | None
) -> None:
    if isinstance(keyboard, ReplyKeyboardMarkup):
        # Reply keyboards cannot be attached through an edit, including legacy callbacks.
        message = getattr(update, "effective_message", None) or update.message
        if message is not None:
            await message.reply_text(text, reply_markup=keyboard)
        return
    query = getattr(update, "callback_query", None)
    if query is not None:
        await _edit_menu_screen(query, text, keyboard)
        return
    if update.message is not None:
        await update.message.reply_text(text, reply_markup=keyboard)


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.message is None:
        return
    settings: Settings = context.application.bot_data["settings"]
    admin = _is_admin(update, settings)
    action = str(query.data).removeprefix("menu:")
    if action in _ADMIN_ACTIONS and not admin:
        await query.answer(texts.ADMIN_ONLY)
        return
    await query.answer()
    await _run_menu_action(update, context, action, admin)


async def menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None or message.text is None:
        return
    action = MENU_LABELS.get(message.text.strip())
    if action is None:
        return
    settings: Settings = context.application.bot_data["settings"]
    admin = _is_admin(update, settings)
    if action in _ADMIN_ACTIONS and not admin:
        await message.reply_text(texts.ADMIN_ONLY)
        return
    await _run_menu_action(update, context, action, admin)


async def _run_menu_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, admin: bool
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if action == "home":
        # Old inline menus point here: answer with a new message that restores the keyboard.
        message = getattr(update, "effective_message", None) or update.message
        if message is not None:
            await message.reply_text(
                _home_text(await _test_mode(context, settings), admin=admin),
                reply_markup=_menu_keyboard(admin),
            )
    elif action == "help":
        await _show_command_screen(
            update, ADMIN_HELP_TEXT if admin else USER_HELP_TEXT, None
        )
    elif action == "receipt":
        await _show_command_screen(update, texts.MENU_RECEIPT, None)
    elif action == "admin":
        test_mode = await _test_mode(context, settings)
        await _show_command_screen(
            update,
            texts.MENU_ADMIN + (f"\n\n{texts.MODE_TRIAL}" if test_mode else ""),
            _admin_keyboard(test_mode),
        )
    elif action in {"test_on", "test_off"}:
        database: Database = context.application.bot_data["database"]
        enabled = action == "test_on"
        try:
            await asyncio.to_thread(database.set_test_mode, enabled)
        except sqlite3.Error as error:
            logger.error("Could not change test mode: %s", type(error).__name__)
            await _show_command_screen(
                update, texts.MODE_CHANGE_FAILED, _navigation_keyboard(admin=True)
            )
            return
        logger.info("Test mode changed: enabled=%s", enabled)
        await _show_command_screen(
            update,
            texts.MODE_CHANGED.format(mode=texts.MODE_TRIAL) if enabled else texts.TEST_DISABLED,
            _admin_keyboard(enabled),
        )
    elif action == "version":
        status = await asyncio.to_thread(_version_status, context, settings)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(texts.MENU_UPDATE_BUTTON, callback_data="menu:update")],
            *_navigation_rows(admin=True),
        ])
        await _show_command_screen(
            update,
            status.replace("/aggiorna", "premi «Installa aggiornamento»"),
            keyboard,
        )
    else:
        handlers = {
            "manual": manual_expense_command, "income": manual_income_command,
            "latest": latest_expenses_command, "summary": summary_command,
            "queue": queue_command, "retry": retry_command,
            "cancel": cancel_command, "health": test_bilancio_command,
            "options": refresh_options_command, "update": update_command,
            "clean": clean_command,
        }
        handler = handlers.get(action)
        if handler is None:
            await _show_command_screen(update, texts.MENU_UNKNOWN, None)
            return
        await handler(update, context)


def _is_admin(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    return user is not None and user.id == settings.admin_user_id


def _update_store(context: ContextTypes.DEFAULT_TYPE, settings: Settings) -> UpdateStatusStore:
    store = context.application.bot_data.get("update_store")
    if isinstance(store, UpdateStatusStore):
        return store
    return UpdateStatusStore(Path(settings.data_path) / "update")


def _running_version(context: ContextTypes.DEFAULT_TYPE) -> RunningVersion:
    running = context.application.bot_data.get("running_version")
    return running if isinstance(running, RunningVersion) else RunningVersion.from_env()


def _version_status(context: ContextTypes.DEFAULT_TYPE, settings: Settings) -> str:
    store = _update_store(context, settings)
    return describe_version(
        _running_version(context),
        store.read_check(),
        store.read_install(),
        request_pending=store.has_request(),
        zone=resolve_timezone(settings.timezone),
    )


async def update_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = getattr(update, "effective_message", None) or update.message
    if message is None:
        return
    settings: Settings = context.application.bot_data["settings"]
    if not _is_admin(update, settings):
        await message.reply_text(texts.ADMIN_ONLY)
        return
    store = _update_store(context, settings)
    if await asyncio.to_thread(store.read_check) is None:
        await message.reply_text(texts.UPDATE_NOT_CONFIGURED, reply_markup=_navigation_keyboard(admin=True))
        return
    if await asyncio.to_thread(store.has_request):
        await message.reply_text(texts.UPDATE_ALREADY_REQUESTED, reply_markup=_navigation_keyboard(admin=True))
        return
    status = await asyncio.to_thread(_version_status, context, settings)
    await message.reply_text(
        texts.UPDATE_PROMPT.format(status=status),
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(texts.UPDATE_YES, callback_data="admin:update:yes")],
                [InlineKeyboardButton(texts.UPDATE_NO, callback_data="admin:update:no")],
                *_navigation_rows(admin=True),
            ]
        ),
    )


async def update_callback(
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
    if query.data != "admin:update:yes":
        await query.edit_message_text(texts.UPDATE_CANCELLED, reply_markup=_navigation_keyboard(admin=True))
        return
    store = _update_store(context, settings)
    message = getattr(query, "message", None)
    chat_id = getattr(message, "chat_id", None)
    message_id = getattr(message, "message_id", None)
    now = datetime.now(timezone.utc)
    try:
        # Track the message before the request exists, so the NAS always sees it.
        if isinstance(chat_id, int) and isinstance(message_id, int):
            await asyncio.to_thread(
                store.write_tracking,
                UpdateTracking(chat_id=chat_id, message_id=message_id, requested_at=now, shown=""),
            )
        await asyncio.to_thread(store.request_install, now)
    except OSError as error:
        logger.error("Could not record update request: %s", type(error).__name__)
        await asyncio.to_thread(_clear_tracking_quietly, store)
        await query.edit_message_text(texts.UPDATE_REQUEST_FAILED, reply_markup=_navigation_keyboard(admin=True))
        return
    logger.info("Update installation requested by the administrator")
    await query.edit_message_text(texts.UPDATE_REQUESTED, reply_markup=_navigation_keyboard(admin=True))


def _clear_tracking_quietly(store: UpdateStatusStore) -> None:
    try:
        store.clear_tracking()
    except OSError as error:
        logger.error("Could not clear update tracking: %s", type(error).__name__)


async def _edit_tracked_message(
    context: ContextTypes.DEFAULT_TYPE,
    tracking: UpdateTracking,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool | None:
    """Return True if edited, False if the message cannot be edited, None to retry."""
    try:
        await context.bot.edit_message_text(
            chat_id=tracking.chat_id,
            message_id=tracking.message_id,
            text=text,
            reply_markup=reply_markup,
        )
    except BadRequest as error:
        if "not modified" in str(error).lower():
            return True
        logger.info("Update progress message can no longer be edited")
        return False
    except TelegramError:
        logger.warning("Could not edit update progress message, retrying later")
        return None
    return True


async def _finish_tracked_update(
    context: ContextTypes.DEFAULT_TYPE,
    store: UpdateStatusStore,
    tracking: UpdateTracking,
    install: InstallOutcome,
) -> None:
    text = install_notice(install)
    keyboard = _navigation_keyboard(admin=True)
    edited = await _edit_tracked_message(context, tracking, text, keyboard)
    if edited is None:
        return
    if not edited:
        try:
            await context.bot.send_message(chat_id=tracking.chat_id, text=text, reply_markup=keyboard)
        except TelegramError:
            logger.error("Could not send update outcome")
            return
    try:
        notified = await asyncio.to_thread(store.read_notified)
        marker = {**notified, "install_finished_at": install.finished_at_raw}
        if install.revision:
            # The admin already knows this version: do not announce it again.
            marker["available_revision"] = install.revision
        await asyncio.to_thread(store.write_notified, marker)
        await asyncio.to_thread(store.clear_tracking)
    except OSError as error:
        logger.error("Could not store update outcome marker: %s", type(error).__name__)


async def update_progress_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Follow a requested installation by editing the admin's request message."""
    settings: Settings = context.application.bot_data["settings"]
    store = _update_store(context, settings)
    tracking = await asyncio.to_thread(store.read_tracking)
    if tracking is None:
        return
    install = await asyncio.to_thread(store.read_install)
    if install is not None and install.finished_at >= tracking.requested_at:
        await _finish_tracked_update(context, store, tracking, install)
        return
    progress = await asyncio.to_thread(store.read_progress)
    if (
        progress is None
        or progress.updated_at < tracking.requested_at
        or progress.updated_at_raw == tracking.shown
    ):
        return
    if await _edit_tracked_message(context, tracking, progress_text(progress)) is None:
        return
    logger.info("Update progress shown to the administrator: %s", progress.stage)
    try:
        await asyncio.to_thread(
            store.write_tracking,
            UpdateTracking(
                chat_id=tracking.chat_id,
                message_id=tracking.message_id,
                requested_at=tracking.requested_at,
                shown=progress.updated_at_raw,
            ),
        )
        await asyncio.to_thread(store.acknowledge_progress, progress)
    except OSError as error:
        logger.error("Could not store update progress marker: %s", type(error).__name__)


async def update_status_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store = _update_store(context, settings)
    if await asyncio.to_thread(store.read_tracking) is not None:
        # update_progress_job reports the outcome in the request message.
        return
    notices, marker = await asyncio.to_thread(
        pending_notices, store, _running_version(context)
    )
    if not notices:
        return
    check = await asyncio.to_thread(store.read_check)
    keyboard = (
        InlineKeyboardMarkup([
            [InlineKeyboardButton(texts.MENU_UPDATE_BUTTON, callback_data="menu:update")],
            [InlineKeyboardButton(texts.MENU_VERSION_BUTTON, callback_data="menu:version")],
        ])
        if update_available(_running_version(context), check)
        else None
    )
    try:
        await context.bot.send_message(
            chat_id=settings.admin_user_id,
            text="\n\n".join(notices),
            reply_markup=keyboard,
        )
    except TelegramError:
        logger.error("Could not send update status notification")
        return
    try:
        await asyncio.to_thread(store.write_notified, marker)
    except OSError as error:
        logger.error("Could not store update notification marker: %s", type(error).__name__)


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.message is None:
        return
    settings: Settings | None = context.application.bot_data.get("settings")
    is_admin = settings is not None and _is_admin(update, settings)
    await update.message.reply_text(
        ADMIN_HELP_TEXT if is_admin else USER_HELP_TEXT,
        reply_markup=_navigation_keyboard(),
    )


async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = getattr(update, "effective_message", None) or update.message
    if message is None or update.effective_user is None:
        return
    workflow: ReceiptWorkflow = context.application.bot_data["receipt_workflow"]
    was_cancelled = await workflow.cancel_current(update.effective_user.id)
    if was_cancelled:
        await message.reply_text(texts.CANCELLED, reply_markup=_navigation_keyboard())
    else:
        await message.reply_text(texts.NOTHING_TO_CANCEL, reply_markup=_navigation_keyboard())


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


async def unsupported_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.message
    if message is None:
        return
    try:
        await message.delete()
    except TelegramError as error:
        logger.info("Could not delete unsupported message: %s", type(error).__name__)
    await context.bot.send_message(
        chat_id=message.chat_id,
        text=texts.UNSUPPORTED_MESSAGE,
        reply_markup=_navigation_keyboard(),
    )


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
    message = getattr(update, "effective_message", None) or update.message
    if message is None or update.effective_user is None:
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
        await _show_command_screen(update, texts.LATEST_EMPTY, _navigation_keyboard())
        return
    await _show_command_screen(update, _format_recent_expenses(expenses), _navigation_keyboard())


async def queue_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = getattr(update, "effective_message", None) or update.message
    if message is None or update.effective_user is None:
        return
    database: Database = context.application.bot_data["database"]
    expenses = await asyncio.to_thread(
        database.list_retryable_expenses,
        update.effective_user.id,
    )
    if not expenses:
        await _show_command_screen(update, texts.QUEUE_EMPTY, _navigation_keyboard())
        return
    lines = [texts.QUEUE_HEADER]
    for expense in expenses:
        lines.append(texts.expense_row(expense.payload, expense.status, queued=True))
    lines.extend(("", texts.QUEUE_FOOTER))
    await _show_command_screen(
        update,
        "\n".join(lines),
        InlineKeyboardMarkup([
            [InlineKeyboardButton(texts.MENU_RETRY, callback_data="menu:retry")],
            *_navigation_rows(),
        ]),
    )


async def retry_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = getattr(update, "effective_message", None) or update.message
    if message is None or update.effective_user is None:
        return
    service: ExpenseRetryService | None = context.application.bot_data["retry_service"]
    if service is None:
        await message.reply_text(
            texts.PORTAL_UNAVAILABLE, reply_markup=_navigation_keyboard()
        )
        return
    database: Database = context.application.bot_data["database"]
    requested = await asyncio.to_thread(
        database.request_retry_for_user,
        update.effective_user.id,
    )
    if requested == 0:
        await message.reply_text(texts.NOTHING_TO_RETRY, reply_markup=_navigation_keyboard())
        return
    summary = await service.process_due(
        context.bot,
        telegram_user_id=update.effective_user.id,
    )
    await message.reply_text(
        texts.RETRY_DONE_WITH_TRIALS.format(
            saved=summary.succeeded - summary.trials, trials=summary.trials,
            pending=summary.pending, failed=summary.failed,
        ) if summary.trials else texts.RETRY_DONE.format(
            saved=summary.succeeded, pending=summary.pending, failed=summary.failed,
        ),
        reply_markup=_navigation_keyboard(),
    )


async def manual_expense_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    workflow: ReceiptWorkflow = context.application.bot_data["receipt_workflow"]
    await workflow.start_manual_expense(
        update, context, collect_data=True, direction=None
    )


async def manual_income_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    workflow: ReceiptWorkflow = context.application.bot_data["receipt_workflow"]
    await workflow.start_manual_expense(update, context, collect_data=True)


async def refresh_options_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = getattr(update, "effective_message", None) or update.message
    if message is None or update.effective_user is None:
        return
    settings: Settings = context.application.bot_data["settings"]
    if update.effective_user.id != settings.admin_user_id:
        await message.reply_text(texts.ADMIN_ONLY)
        return
    client: BilancioClient | None = context.application.bot_data["bilancio_client"]
    if client is None:
        await message.reply_text(texts.PORTAL_UNAVAILABLE, reply_markup=_navigation_keyboard(admin=True))
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
    await message.reply_text(
        texts.REFRESH_PROGRESS, reply_markup=_navigation_keyboard(admin=True)
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
            *_navigation_rows(),
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
    message = getattr(update, "effective_message", None) or update.message
    if message is None:
        return
    if context.application.bot_data.get(_SUMMARY_RUNNING_KEY):
        await message.reply_text(texts.SUMMARY_BUSY, reply_markup=_navigation_keyboard())
        return
    settings: Settings = context.application.bot_data["settings"]
    start_year = scout_year_start(local_now(settings.timezone).date())
    progress = await message.reply_text(_summary_progress_text(start_year), reply_markup=_navigation_keyboard())
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
    settings = context.application.bot_data.get("settings")
    current_start = scout_year_start(local_now(settings.timezone if settings else "Europe/Rome").date())
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
    settings = context.application.bot_data.get("settings")
    zone_name = settings.timezone if settings else "Europe/Rome"
    today = local_now(zone_name).date()
    database: Database = context.application.bot_data["database"]
    keyboard = _summary_keyboard(start_year, today)
    try:
        client: BilancioClient | None = context.application.bot_data.get("bilancio_client")
        try:
            if client is None:
                raise BilancioClientError("Portal client unavailable")
            ledgers = await client.read_ledgers(
                transaction_date=reference_date_for(start_year, today),
                include_entries=data.get("details", False),
            )
            compact = compact_ledgers(ledgers)
        except (BilancioClientError, ValueError, TypeError, OSError) as error:
            logger.warning("Ledger summary failed: %s", type(error).__name__)
            messages = [_SUMMARY_FAILED_TEXT]
            try:
                snapshot = await asyncio.to_thread(database.get_ledger_snapshot, start_year)
            except (sqlite3.Error, ValueError, KeyError, TypeError) as cache_error:
                logger.warning("Ledger snapshot unavailable: %s", type(cache_error).__name__)
                snapshot = None
            if snapshot is not None:
                read_at = snapshot.read_at.astimezone(ZoneInfo(zone_name))
                messages[0] += "\n\n" + texts.SUMMARY_CACHED.format(
                    day=read_at, clock=read_at, zone=zone_name,
                )
                messages.extend(format_ledger_summary(
                    snapshot.ledgers, start_year,
                    include_totals=bool(settings and chat_id == settings.admin_user_id),
                ))
        else:
            messages = format_ledger_summary(
                ledgers, start_year, details=data.get("details", False),
                include_totals=bool(settings and chat_id == settings.admin_user_id),
            )
            try:
                await asyncio.to_thread(
                    database.save_ledger_snapshot, start_year, local_now(zone_name), compact,
                )
            except (sqlite3.Error, OSError, ValueError) as error:
                logger.warning("Could not save ledger snapshot: %s", type(error).__name__)
                messages.append(texts.SUMMARY_CACHE_SAVE_FAILED)
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
    message = getattr(update, "effective_message", None) or update.message
    if message is None or update.effective_user is None:
        return
    settings: Settings = context.application.bot_data["settings"]
    if update.effective_user.id != settings.admin_user_id:
        await message.reply_text(texts.ADMIN_ONLY)
        return
    client: BilancioClient | None = context.application.bot_data["bilancio_client"]
    if client is None:
        await message.reply_text(
            texts.PORTAL_UNAVAILABLE, reply_markup=_navigation_keyboard(admin=True)
        )
        return
    try:
        await client.health_check()
    except ManualPortalLoginRequired as error:
        logger.error("AGESCI manual login is required")
        debug_path = Path(settings.data_path) / "debug"
        await message.reply_text(
            texts.HEALTH_MANUAL.format(
                folder=debug_path,
                diagnostic_file=f" ({Path(error.diagnostic_path).name})" if error.diagnostic_path else "",
            ),
            reply_markup=_navigation_keyboard(admin=True),
        )
        return
    except BilancioClientError as error:
        logger.error("AGESCI health check failed: %s", type(error).__name__)
        debug_path = Path(settings.data_path) / "debug"
        await message.reply_text(
            texts.HEALTH_FAILED.format(folder=debug_path), reply_markup=_navigation_keyboard(admin=True)
        )
        return
    await message.reply_text(
        texts.HEALTH_OK, reply_markup=_navigation_keyboard(admin=True)
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
            texts.REFRESH_ROW.format(account=texts.account_text(label), income=describe(directions.get(TransactionDirection.INCOME)), expense=describe(directions.get(TransactionDirection.EXPENSE)))
        )
    if any(count is None for _, _, count in summary):
        lines.append(texts.REFRESH_PARTIAL)
    return "\n".join(lines)


async def clean_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = getattr(update, "effective_message", None) or update.message
    if message is None:
        return
    settings: Settings = context.application.bot_data["settings"]
    if not _is_admin(update, settings):
        await message.reply_text(texts.ADMIN_ONLY)
        return
    await message.reply_text(
        texts.CLEAN_PROMPT,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(texts.CLEAN_YES, callback_data="admin:clean:yes")],
                [InlineKeyboardButton(texts.CLEAN_NO, callback_data="admin:clean:no")],
                *_navigation_rows(admin=True),
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
        await query.edit_message_text(texts.CLEAN_CANCELLED, reply_markup=_navigation_keyboard(admin=True))
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
        texts.CLEAN_DONE.format(expenses=counts['expenses'], pending=counts['retries'], files=removed_files),
        reply_markup=_navigation_keyboard(admin=True),
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
