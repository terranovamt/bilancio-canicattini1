import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from pathlib import Path

from telegram import ReplyKeyboardMarkup, Update
from telegram.error import TimedOut
from telegram.ext import ApplicationHandlerStop

from bot.application import (
    ADMIN_COMMANDS,
    api_status_command,
    ADMIN_HELP_TEXT,
    USER_COMMANDS,
    USER_HELP_TEXT,
    _admin_keyboard,
    _format_preload_summary,
    _format_recent_expenses,
    authorize_update,
    cancel_command,
    clean_callback,
    clean_command,
    configure_bot_menu,
    create_application,
    help_command,
    latest_expenses_command,
    manual_income_command,
    manual_expense_command,
    menu_callback,
    ledger_summary_job,
    preload_options_job,
    preload_portal_options,
    queue_command,
    refresh_options_command,
    retry_command,
    start_command,
    summary_callback,
    summary_command,
    test_bilancio_command,
    update_callback,
    update_command,
    update_progress_job,
    update_status_job,
    unsupported_message,
)
from bot import messages as texts
from bot.updates import RunningVersion
from bot.summary import scout_year_name as scout_year_name_for, scout_year_start
from bot.retry_service import RetrySummary
from datetime import date
from decimal import Decimal
from bilancio.client import (
    AccountLedger,
    BilancioClientError,
    PortalOption,
    TransactionDirection,
)
from config import Settings
from db.database import Database, StoredExpense


def make_settings() -> Settings:
    return Settings.from_env(
        {
            "BOT_TOKEN": "test-token-placeholder",
            "ALLOWED_USER_IDS": "12345",
            "ADMIN_USER_ID": "12345",
        }
    )


class BotApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ledger_totals_are_admin_only_for_live_and_cached_summaries(self) -> None:
        from bilancio.client import AccountLedger

        sample = AccountLedger(
            account=PortalOption("A", "Cassa esempio"),
            opening_balance=Decimal("10"), total_income=Decimal("2"),
            total_expense=Decimal("3"), balance=Decimal("9"),
            entries=(), total_rows=0, complete=True, entries_available=False,
        )
        settings = make_settings()
        for user_id in (settings.admin_user_id, 67890):
            client = SimpleNamespace(read_ledgers=AsyncMock(return_value=[sample]))
            context = self.make_context(settings=settings, bilancio_client=client)
            context.bot = SimpleNamespace(edit_message_text=AsyncMock(), send_message=AsyncMock())
            context.job = SimpleNamespace(data={
                "chat_id": user_id, "message_id": 55,
                "start_year": scout_year_start(date.today()),
            })
            for cached in (False, True):
                if cached:
                    client.read_ledgers.side_effect = BilancioClientError("synthetic")
                await ledger_summary_job(context)
                rendered = context.bot.edit_message_text.await_args.args[0]
                rendered += "".join(call.kwargs["text"] for call in context.bot.send_message.await_args_list)
                self.assertEqual("Totale di tutte le casse" in rendered, user_id == settings.admin_user_id)
                if cached:
                    self.assertIn("Ultimi dati letti", rendered)
                context.bot.send_message.reset_mock()

    async def test_manual_command_collects_fields_and_offers_both_directions(self) -> None:
        workflow = SimpleNamespace(start_manual_expense=AsyncMock())
        context = self.make_context(receipt_workflow=workflow)
        update = SimpleNamespace()
        await manual_expense_command(update, context)
        workflow.start_manual_expense.assert_awaited_once_with(
            update, context, collect_data=True, direction=None
        )
        self.assertIn("manual", {command.command for command in ADMIN_COMMANDS})
        self.assertNotIn("testspesa", {command.command for command in ADMIN_COMMANDS})
        self.assertNotIn("manual", {command.command for command in USER_COMMANDS})
        self.assertIn("Inserisci a mano", ADMIN_HELP_TEXT)

    async def test_start_and_help_bypass_slow_work_but_mutations_stay_sequential(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            application = create_application(
                make_settings(), Database(Path(directory) / "test.sqlite3")
            )
            processor = application.update_processor
            entered = asyncio.Event()
            release = asyncio.Event()
            second = AsyncMock()

            async def slow() -> None:
                entered.set()
                await release.wait()

            first_task = asyncio.create_task(processor.process_update(object(), slow()))
            await asyncio.wait_for(entered.wait(), timeout=1)
            queued_task = asyncio.create_task(processor.process_update(object(), second()))
            try:
                for command in ("/start", "/help", "/start@example_bot", "/apistatus"):
                    update = Update.de_json(
                        {
                            "update_id": 1,
                            "message": {
                                "message_id": 1, "date": 0,
                                "chat": {"id": 12345, "type": "private"},
                                "from": {"id": 12345, "is_bot": False, "first_name": "Test"},
                                "text": command,
                                "entities": [{
                                    "type": "bot_command", "offset": 0,
                                    "length": len(command),
                                }],
                            },
                        },
                        application.bot,
                    )
                    response = AsyncMock()
                    context = self.make_context(settings=make_settings())
                    context.bot = SimpleNamespace(set_my_commands=AsyncMock())

                    async def quick() -> None:
                        await authorize_update(update, context)
                        if command == "/help":
                            await help_command(update, context)
                        elif command == "/apistatus":
                            await api_status_command(update, context)
                        else:
                            await start_command(update, context)

                    with patch.object(type(update.message), "reply_text", new=response):
                        await asyncio.wait_for(
                            processor.process_update(update, quick()), timeout=0.5
                        )
                    self.assertEqual(response.await_count, 1)
                    second.assert_not_awaited()

                blocked_update = Update.de_json(
                    {
                        "update_id": 2,
                        "message": {
                            "message_id": 2, "date": 0,
                            "chat": {"id": 99999, "type": "private"},
                            "from": {"id": 99999, "is_bot": False, "first_name": "Test"},
                            "text": "/start",
                            "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
                        },
                    },
                    application.bot,
                )
                with self.assertRaises(ApplicationHandlerStop):
                    await processor.process_update(
                        blocked_update,
                        authorize_update(
                            blocked_update, self.make_context(settings=make_settings())
                        ),
                    )
            finally:
                release.set()
                await asyncio.gather(first_task, queued_task)
            second.assert_awaited_once()

    async def test_income_command_collects_all_fields_and_is_available_to_users(self) -> None:
        workflow = SimpleNamespace(start_manual_expense=AsyncMock())
        context = self.make_context(receipt_workflow=workflow)
        update = SimpleNamespace()
        await manual_income_command(update, context)
        workflow.start_manual_expense.assert_awaited_once_with(
            update, context, collect_data=True
        )
        self.assertIn("entrata", {command.command for command in USER_COMMANDS})
        self.assertNotIn("manual", {command.command for command in USER_COMMANDS})
        self.assertIn("manual", {command.command for command in ADMIN_COMMANDS})

    def make_context(self, **bot_data: object):
        if "database" not in bot_data:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            database = Database(Path(temporary.name) / "test.sqlite3")
            database.initialize()
            bot_data["database"] = database
        return SimpleNamespace(application=SimpleNamespace(bot_data=bot_data))

    async def test_unauthorized_update_is_stopped_and_logged_without_user_id(self) -> None:
        context = self.make_context(settings=make_settings())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=99999))

        with self.assertLogs("bot.application", level="WARNING") as captured:
            with self.assertRaises(ApplicationHandlerStop):
                await authorize_update(update, context)

        self.assertNotIn("99999", "\n".join(captured.output))

    async def test_start_message_is_in_italian(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            message=message, effective_user=SimpleNamespace(id=12345)
        )

        context = self.make_context(settings=make_settings())
        context.bot = SimpleNamespace(set_my_commands=AsyncMock())
        await start_command(update, context)

        text = message.reply_text.await_args.args[0]
        self.assertIn("Mandami", text)
        self.assertIn("scontrino", text)
        self.assertNotIn("REALE", text)
        message.reply_text.assert_awaited_once()
        keyboard = message.reply_text.await_args.kwargs["reply_markup"]
        self.assertIsInstance(keyboard, ReplyKeyboardMarkup)
        self.assertTrue(keyboard.is_persistent)
        self.assertTrue(keyboard.resize_keyboard)
        buttons = [button for row in keyboard.keyboard for button in row]
        labels = [button.text for button in buttons]
        self.assertNotIn(texts.MENU_MANUAL_BUTTON, labels)
        self.assertIn(texts.MENU_INCOME_BUTTON, labels)
        self.assertIn(texts.MENU_ADMIN_BUTTON, labels)
        admin_buttons = [
            button.text
            for row in _admin_keyboard().keyboard
            for button in row
        ]
        self.assertEqual(admin_buttons[0], texts.MENU_MANUAL_BUTTON)
        self.assertNotIn(texts.MENU_INCOME_BUTTON, admin_buttons)
        self.assertFalse(any("/" in button.text for button in buttons))
        self.assertNotIn("/", text)
        admin_call = context.bot.set_my_commands.await_args
        self.assertEqual(admin_call.args[0], ADMIN_COMMANDS)
        self.assertEqual(admin_call.kwargs["scope"].chat_id, 12345)

    async def test_unsupported_content_is_deleted_and_user_is_guided(self) -> None:
        message = SimpleNamespace(chat_id=12345, delete=AsyncMock())
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        await unsupported_message(SimpleNamespace(message=message), context)
        message.delete.assert_awaited_once()
        sent = context.bot.send_message.await_args.kwargs
        self.assertEqual(sent["text"], texts.UNSUPPORTED_MESSAGE)
        self.assertEqual(sent["chat_id"], 12345)

        message.delete = AsyncMock(side_effect=TimedOut())
        with self.assertLogs("bot.application", level="INFO"):
            await unsupported_message(SimpleNamespace(message=message), context)
        self.assertEqual(context.bot.send_message.await_count, 2)

    async def test_stickers_are_routed_to_unsupported_handler(self) -> None:
        from telegram.ext import MessageHandler

        application = create_application(make_settings(), Mock())
        from telegram.ext import CommandHandler
        api_handlers = [
            handler for handler in application.handlers[0]
            if isinstance(handler, CommandHandler) and "apistatus" in handler.commands
        ]
        self.assertEqual(len(api_handlers), 1)
        self.assertIs(api_handlers[0].callback, api_status_command)
        handlers = [
            handler for handler in application.handlers[0]
            if isinstance(handler, MessageHandler) and handler.callback is unsupported_message
        ]
        self.assertEqual(len(handlers), 1)
        self.assertTrue(handlers[0].filters.check_update(Update.de_json({
            "update_id": 1,
            "message": {
                "message_id": 1, "date": 0,
                "chat": {"id": 1, "type": "private"},
                "sticker": {
                    "file_id": "x", "file_unique_id": "y", "width": 1, "height": 1,
                    "is_animated": False, "is_video": False, "type": "regular",
                },
            },
        }, None)))

    async def test_registers_slash_commands_without_requiring_known_admin_chat(self) -> None:
        bot = SimpleNamespace(
            set_my_commands=AsyncMock(), set_chat_menu_button=AsyncMock()
        )
        application = SimpleNamespace(bot=bot, bot_data={"settings": make_settings()})

        await configure_bot_menu(application)

        bot.set_my_commands.assert_awaited_once_with(USER_COMMANDS)
        bot.set_chat_menu_button.assert_awaited_once()

    async def test_non_admin_start_keyboard_hides_admin_commands(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        await start_command(
            SimpleNamespace(message=message, effective_user=SimpleNamespace(id=67890)),
            self.make_context(settings=make_settings()),
        )
        keyboard = message.reply_text.await_args.kwargs["reply_markup"]
        labels = [button.text for row in keyboard.keyboard for button in row]
        self.assertNotIn(texts.MENU_MANUAL_BUTTON, labels)
        self.assertIn(texts.MENU_INCOME_BUTTON, labels)
        self.assertNotIn(texts.MENU_ADMIN_BUTTON, labels)
        text = message.reply_text.await_args.args[0]
        self.assertNotIn("REALE", text)
        for admin_detail in ("/testspesa", "/pulisci", "/aggiornaopzioni", "SIMULATA"):
            self.assertNotIn(admin_detail, text)

    async def test_refresh_options_invalidates_cache_without_submission(self) -> None:
        client = SimpleNamespace(
            clear_options_cache=AsyncMock(), create_expense=AsyncMock()
        )
        message = SimpleNamespace(reply_text=AsyncMock())
        context = self.make_context(settings=make_settings(), bilancio_client=client)
        context.application.job_queue = Mock()
        await refresh_options_command(
            SimpleNamespace(message=message, effective_user=SimpleNamespace(id=12345)),
            context,
        )
        client.clear_options_cache.assert_awaited_once()
        client.create_expense.assert_not_awaited()
        context.application.job_queue.run_once.assert_called_once()
        self.assertEqual(
            context.application.job_queue.run_once.call_args.kwargs["data"],
            {"notify_chat_id": 12345},
        )
        self.assertIn("ti scrivo quando ho finito", message.reply_text.await_args.args[0])

    async def test_manual_refresh_reports_loaded_options(self) -> None:
        accounts = [PortalOption("a", "Cassa Co.ca")]
        client = SimpleNamespace(
            list_accounts=AsyncMock(return_value=accounts),
            list_transaction_types=AsyncMock(
                side_effect=[[PortalOption("t", "Tipo")] * 4, BilancioClientError("x")]
            ),
        )
        context = self.make_context(bilancio_client=client)
        context.job = SimpleNamespace(data={"notify_chat_id": 12345})
        context.bot = SimpleNamespace(send_message=AsyncMock())

        await preload_options_job(context)

        sent = context.bot.send_message.await_args.kwargs
        self.assertEqual(sent["chat_id"], 12345)
        self.assertIn("Fatto! Trovate 1 casse", sent["text"])
        self.assertIn("Cassa Co. ca: 4 entrate, da rileggere uscite", sent["text"])

    def test_preload_summary_reports_unreadable_accounts(self) -> None:
        self.assertIn("Non riesco a leggere le casse", _format_preload_summary(None))

    async def test_summary_command_reads_portal_in_background(self) -> None:
        progress = SimpleNamespace(chat_id=777, message_id=55)
        message = SimpleNamespace(reply_text=AsyncMock(return_value=progress))
        context = self.make_context(settings=make_settings(), bilancio_client=Mock())
        context.application.job_queue = Mock()

        await summary_command(
            SimpleNamespace(message=message, effective_user=SimpleNamespace(id=67890)),
            context,
        )

        self.assertIn("Non modifico nulla", message.reply_text.await_args.args[0])
        run_once = context.application.job_queue.run_once
        self.assertIs(run_once.call_args.args[0], ledger_summary_job)
        self.assertEqual(
            run_once.call_args.kwargs["data"],
            {"chat_id": 777, "message_id": 55, "start_year": scout_year_start(date.today())},
        )
        self.assertTrue(context.application.bot_data["ledger_summary_running"])

    async def test_summary_command_refuses_concurrent_reads(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        context = self.make_context(
            settings=make_settings(), bilancio_client=Mock(), ledger_summary_running=True
        )
        context.application.job_queue = Mock()

        await summary_command(SimpleNamespace(message=message), context)

        self.assertIn("già preparando", message.reply_text.await_args.args[0])
        context.application.job_queue.run_once.assert_not_called()

    async def test_summary_job_edits_progress_with_balances_and_year_buttons(self) -> None:
        start_year = scout_year_start(date.today())
        ledger = AccountLedger(
            account=PortalOption("Cassa RS", "Cassa RS"),
            opening_balance=Decimal("97.75"),
            total_income=Decimal("0"),
            total_expense=Decimal("0"),
            balance=Decimal("97.75"),
            entries=(),
            total_rows=1,
            complete=True,
        )
        client = SimpleNamespace(
            read_ledgers=AsyncMock(return_value=[ledger]), create_expense=AsyncMock()
        )
        context = self.make_context(bilancio_client=client, ledger_summary_running=True)
        context.job = SimpleNamespace(
            data={"chat_id": 777, "message_id": 55, "start_year": start_year}
        )
        context.bot = SimpleNamespace(edit_message_text=AsyncMock(), send_message=AsyncMock())

        await ledger_summary_job(context)

        client.create_expense.assert_not_awaited()
        self.assertEqual(
            client.read_ledgers.await_args.kwargs["transaction_date"], date.today()
        )
        self.assertFalse(client.read_ledgers.await_args.kwargs["include_entries"])
        edit = context.bot.edit_message_text.await_args
        self.assertIn("Cassa RS\n💰 Saldo: 97,75 €", edit.args[0])
        self.assertEqual((edit.kwargs["chat_id"], edit.kwargs["message_id"]), (777, 55))
        buttons = [b.callback_data for row in edit.kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertEqual(buttons, [
            f"summary:{start_year - 1}", f"summary:{start_year}",
            f"summary:details:{start_year}",
        ])
        self.assertFalse(context.application.bot_data["ledger_summary_running"])

    async def test_summary_job_reports_portal_errors_and_releases_lock(self) -> None:
        client = SimpleNamespace(read_ledgers=AsyncMock(side_effect=BilancioClientError("x")))
        context = self.make_context(bilancio_client=client, ledger_summary_running=True)
        start_year = scout_year_start(date.today()) - 1
        context.job = SimpleNamespace(
            data={"chat_id": 777, "message_id": 55, "start_year": start_year}
        )
        context.bot = SimpleNamespace(edit_message_text=AsyncMock(), send_message=AsyncMock())

        await ledger_summary_job(context)

        self.assertEqual(
            client.read_ledgers.await_args.kwargs["transaction_date"],
            date(start_year + 1, 9, 30),
        )
        edit = context.bot.edit_message_text.await_args
        self.assertIn("Non riesco a leggere i saldi", edit.args[0])
        buttons = [b.callback_data for row in edit.kwargs["reply_markup"].inline_keyboard for b in row]
        self.assertIn(f"summary:{start_year + 1}", buttons)
        self.assertFalse(context.application.bot_data["ledger_summary_running"])

    async def test_summary_callback_switches_to_previous_year(self) -> None:
        start_year = scout_year_start(date.today()) - 1
        query = SimpleNamespace(
            data=f"summary:{start_year}",
            message=SimpleNamespace(chat_id=777, message_id=55),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = self.make_context(bilancio_client=Mock())
        context.application.job_queue = Mock()

        await summary_callback(SimpleNamespace(callback_query=query), context)

        self.assertIn("Leggo i saldi dal portale", query.edit_message_text.await_args.args[0])
        self.assertEqual(
            context.application.job_queue.run_once.call_args.kwargs["data"]["start_year"],
            start_year,
        )

    async def test_summary_callback_ignores_future_years(self) -> None:
        query = SimpleNamespace(
            data=f"summary:{scout_year_start(date.today()) + 1}",
            message=SimpleNamespace(chat_id=777, message_id=55),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = self.make_context(bilancio_client=Mock())
        context.application.job_queue = Mock()

        await summary_callback(SimpleNamespace(callback_query=query), context)

        query.edit_message_text.assert_not_awaited()
        context.application.job_queue.run_once.assert_not_called()

    async def test_details_callback_requests_details_without_changing_year(self) -> None:
        start_year = scout_year_start(date.today()) - 1
        query = SimpleNamespace(
            data=f"summary:details:{start_year}",
            message=SimpleNamespace(chat_id=777, message_id=55),
            answer=AsyncMock(), edit_message_text=AsyncMock(),
        )
        context = self.make_context(bilancio_client=Mock())
        context.application.job_queue = Mock()
        await summary_callback(SimpleNamespace(callback_query=query), context)
        data = context.application.job_queue.run_once.call_args.kwargs["data"]
        self.assertEqual(data["start_year"], start_year)
        self.assertTrue(data["details"])

    async def test_details_job_shows_opening_balance(self) -> None:
        sample = AccountLedger(
            account=PortalOption("a", "Cassa esempio"),
            opening_balance=Decimal("10"),
            total_income=Decimal("0"), total_expense=Decimal("0"),
            balance=Decimal("10"), entries=(), total_rows=0, complete=True,
        )
        client = SimpleNamespace(read_ledgers=AsyncMock(return_value=[sample]))
        context = self.make_context(bilancio_client=client)
        context.job = SimpleNamespace(data={
            "chat_id": 777, "message_id": 55,
            "start_year": scout_year_start(date.today()), "details": True,
        })
        context.bot = SimpleNamespace(edit_message_text=AsyncMock(), send_message=AsyncMock())
        await ledger_summary_job(context)
        self.assertIn("Saldo iniziale: 10,00 €", context.bot.edit_message_text.await_args.args[0])
        self.assertTrue(client.read_ledgers.await_args.kwargs["include_entries"])

    async def test_retry_summary_does_not_call_simulations_real_saves(self) -> None:
        database = SimpleNamespace(request_retry_for_user=Mock(return_value=1))
        service = SimpleNamespace(process_due=AsyncMock(return_value=RetrySummary(1, 0, 0, 1)))
        context = self.make_context(database=database, retry_service=service)
        context.bot = Mock()
        message = SimpleNamespace(reply_text=AsyncMock())
        await retry_command(
            SimpleNamespace(message=message, effective_user=SimpleNamespace(id=12345)),
            context,
        )
        self.assertIn("0 salvate ✅, 1 in prova 🧪", message.reply_text.await_args.args[0])

    async def test_preload_reads_every_account_direction_without_submission(self) -> None:
        accounts = [PortalOption("a", "Cassa Co.ca"), PortalOption("b", "Cassa EG")]
        client = SimpleNamespace(
            list_accounts=AsyncMock(return_value=accounts),
            list_transaction_types=AsyncMock(
                side_effect=[[], BilancioClientError("Unavailable"), [], []]
            ),
            create_expense=AsyncMock(),
        )

        summary = await preload_portal_options(client)

        self.assertEqual(sum(1 for item in summary if item[2] is not None), 3)
        self.assertEqual(client.list_transaction_types.await_count, 4)
        requested = {
            (call.args[0].label, call.args[1])
            for call in client.list_transaction_types.await_args_list
        }
        self.assertEqual(
            requested,
            {
                (account.label, direction)
                for account in accounts
                for direction in TransactionDirection
            },
        )
        client.create_expense.assert_not_awaited()

    async def test_nightly_preload_refreshes_cache_first(self) -> None:
        client = SimpleNamespace(
            clear_options_cache=AsyncMock(),
            list_accounts=AsyncMock(return_value=[]),
        )
        context = self.make_context(bilancio_client=client)
        context.job = SimpleNamespace(data={"refresh": True})

        await preload_options_job(context)

        client.clear_options_cache.assert_awaited_once()
        client.list_accounts.assert_awaited_once()

    async def test_refresh_options_is_admin_only(self) -> None:
        client = SimpleNamespace(clear_options_cache=AsyncMock())
        message = SimpleNamespace(reply_text=AsyncMock())
        await refresh_options_command(
            SimpleNamespace(message=message, effective_user=SimpleNamespace(id=67890)),
            self.make_context(settings=make_settings(), bilancio_client=client),
        )
        client.clear_options_cache.assert_not_awaited()

    async def test_help_depends_on_role(self) -> None:
        context = self.make_context(settings=make_settings())
        for user_id, expected in (
            (12345, texts.MENU_HELP + texts.MENU_ADMIN_HELP),
            (67890, texts.MENU_HELP),
        ):
            message = SimpleNamespace(reply_text=AsyncMock())
            await help_command(
                SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id)),
                context,
            )
            self.assertEqual(message.reply_text.await_args.args[0], expected)
            self.assertNotIn("/", expected)
        self.assertIn("Ultime operazioni", USER_HELP_TEXT)
        self.assertNotIn("Inserisci a mano", USER_HELP_TEXT)
        self.assertIn("Inserisci a mano", ADMIN_HELP_TEXT)

    async def test_clean_is_admin_only(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        await clean_command(
            SimpleNamespace(message=message, effective_user=SimpleNamespace(id=67890)),
            self.make_context(settings=make_settings()),
        )
        self.assertEqual(
            message.reply_text.await_args.args[0],
            "Questo comando è riservato all'amministratore.",
        )

    async def test_clean_asks_confirmation_then_clears_local_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings.from_env(
                {
                    "BOT_TOKEN": "test-token-placeholder",
                    "ALLOWED_USER_IDS": "12345",
                    "ADMIN_USER_ID": "12345",
                    "DATA_PATH": str(root / "data"),
                    "STORAGE_PATH": str(root / "archive"),
                }
            )
            database = Database(root / "bot.sqlite3")
            database.initialize()
            database.create_manual_expense(
                expense_id="e1", telegram_user_id=12345, payload={"merchant": "x"}
            )
            for relative in ("pending/r.jpg", "debug/d.html", "logs/bot.log.1"):
                (root / "data" / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / "data" / relative).write_text("x", encoding="utf-8")
            (root / "data" / "logs" / "bot.log").write_text("old log", encoding="utf-8")
            (root / "archive").mkdir()
            (root / "archive" / "receipt.jpg").write_text("keep", encoding="utf-8")
            context = self.make_context(settings=settings, database=database)
            user = SimpleNamespace(id=12345)

            message = SimpleNamespace(reply_text=AsyncMock())
            await clean_command(SimpleNamespace(message=message, effective_user=user), context)
            markup = message.reply_text.await_args.kwargs["reply_markup"]
            self.assertEqual(markup.inline_keyboard[0][0].callback_data, "admin:clean:yes")
            self.assertIsNotNone(database.get_expense("e1"))

            query = SimpleNamespace(
                data="admin:clean:yes", answer=AsyncMock(), edit_message_text=AsyncMock()
            )
            await clean_callback(SimpleNamespace(callback_query=query, effective_user=user), context)

            self.assertIsNone(database.get_expense("e1"))
            self.assertFalse((root / "data" / "pending" / "r.jpg").exists())
            self.assertFalse((root / "data" / "debug" / "d.html").exists())
            self.assertFalse((root / "data" / "logs" / "bot.log.1").exists())
            self.assertEqual((root / "data" / "logs" / "bot.log").read_text(encoding="utf-8"), "")
            self.assertTrue((root / "archive" / "receipt.jpg").exists())
            self.assertIn("Eliminate 1 spese", query.edit_message_text.await_args.args[0])

    async def test_clean_can_be_declined(self) -> None:
        database = Mock()
        query = SimpleNamespace(
            data="admin:clean:no", answer=AsyncMock(), edit_message_text=AsyncMock()
        )
        await clean_callback(
            SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=12345)),
            self.make_context(settings=make_settings(), database=database),
        )
        database.clear_history.assert_not_called()
        self.assertEqual(query.edit_message_text.await_args.args[0], "Ok, non ho toccato niente.")

    async def test_latest_expenses_hide_unfinished_entries_and_show_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "bot.sqlite3")
            database.initialize()
            for expense_id, status in (("a", "submitted"), ("b", "cancelled"), ("c", "queued")):
                database.create_manual_expense(
                    expense_id=expense_id,
                    telegram_user_id=12345,
                    payload={"merchant": f"Spesa {expense_id}", "account": {"label": "Cassa Co.ca"}},
                )
                database.update_expense_status(expense_id, status)
            message = SimpleNamespace(reply_text=AsyncMock())
            await latest_expenses_command(
                SimpleNamespace(message=message, effective_user=SimpleNamespace(id=12345)),
                self.make_context(database=database),
            )
            text = message.reply_text.await_args.args[0]
            self.assertIn("✅", text)
            self.assertIn("in attesa di essere salvata", text)
            self.assertNotIn("Spesa b", text)
            self.assertIn("Cassa Co. ca", text)
            self.assertNotIn("{", text)

    async def test_cancel_command_clears_saved_state(self) -> None:
        workflow = Mock()
        workflow.cancel_current = AsyncMock(return_value=True)
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=12345),
        )
        context = self.make_context(receipt_workflow=workflow, settings=make_settings())

        await cancel_command(update, context)

        workflow.cancel_current.assert_awaited_once_with(12345)
        self.assertEqual(
            message.reply_text.await_args.args[0],
            "Ok, ho annullato. Quando vuoi, mandami un altro scontrino.",
        )

    async def test_portal_health_check_is_admin_only(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=67890),
        )
        context = self.make_context(settings=make_settings())

        await test_bilancio_command(update, context)

        self.assertEqual(
            message.reply_text.await_args.args[0],
            "Questo comando è riservato all'amministratore.",
        )

    async def test_queue_command_reports_empty_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "bot.sqlite3")
            database.initialize()
            message = SimpleNamespace(reply_text=AsyncMock())
            update = SimpleNamespace(
                message=message,
                effective_user=SimpleNamespace(id=12345),
            )
            context = self.make_context(database=database)

            await queue_command(update, context)

            self.assertEqual(
                message.reply_text.await_args.args[0],
                "Tutto a posto: non ci sono spese in attesa ✅",
            )

    def test_recent_expense_format_does_not_include_newlines_from_fields(self) -> None:
        expense = StoredExpense(
            expense_id="expense-1",
            telegram_user_id=12345,
            payload={
                "date": "2026-10-26",
                "merchant": "Shop\nInjected text",
                "total_amount": "12.50",
                "currency": "EUR",
                "account": "Cassa Co.ca",
            },
            status="submitted",
            archive_path=None,
            created_at="2026-10-26T12:00:00+00:00",
            updated_at="2026-10-26T12:00:00+00:00",
        )

        message = _format_recent_expenses([expense])

        self.assertEqual(message.count("\n"), 1)
        self.assertIn("Shop Injected text", message)


class UpdateCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name) / "data"
        self.update_dir = self.data / "update"
        self.update_dir.mkdir(parents=True)
        self.user = SimpleNamespace(id=12345)
        self.database = Database(self.data / "test.sqlite3")
        self.database.initialize()

    def settings(self, dry_run: str = "true") -> Settings:
        return Settings.from_env(
            {
                "BOT_TOKEN": "test-token-placeholder",
                "ALLOWED_USER_IDS": "12345,67890",
                "ADMIN_USER_ID": "12345",
                "DATA_PATH": str(self.data),
                "TZ": "UTC",
                "DRY_RUN": dry_run,
            }
        )

    def context(self, settings: Settings | None = None) -> SimpleNamespace:
        context = SimpleNamespace(
            application=SimpleNamespace(
                bot_data={
                    "settings": settings or self.settings(),
                    "database": self.database,
                    "running_version": RunningVersion(
                        revision="a" * 40, committed_at=None, subject="Attuale"
                    ),
                }
            )
        )
        context.bot = SimpleNamespace(set_my_commands=AsyncMock(), send_message=AsyncMock())
        return context

    def write_check(self, revision: str) -> None:
        (self.update_dir / "check").write_text(
            f"checked_at=2026-10-29T12:00:00Z\navailable_revision={revision}\n"
            "available_summary=bbbbbbb - Nuova\nerror=\n",
            encoding="utf-8",
        )

    async def test_admin_version_screen_shows_running_and_available_version(self) -> None:
        self.write_check("b" * 40)
        query = SimpleNamespace(
            data="menu:version", message=SimpleNamespace(),
            answer=AsyncMock(), edit_message_text=AsyncMock(),
        )
        await menu_callback(
            SimpleNamespace(callback_query=query, effective_user=self.user), self.context()
        )
        text = query.edit_message_text.await_args.args[0]
        self.assertIn("Versione attiva: aaaaaaa – Attuale", text)
        self.assertIn("Nuova versione disponibile: bbbbbbb - Nuova", text)
        self.assertNotIn("/aggiorna", text)

    async def test_non_admin_start_hides_version(self) -> None:
        self.write_check("b" * 40)
        message = SimpleNamespace(reply_text=AsyncMock())
        await start_command(
            SimpleNamespace(message=message, effective_user=SimpleNamespace(id=67890)),
            self.context(),
        )
        self.assertNotIn("Versione", message.reply_text.await_args.args[0])

    async def test_update_requires_confirmation_then_writes_request(self) -> None:
        self.write_check("b" * 40)
        context = self.context()
        message = SimpleNamespace(reply_text=AsyncMock())
        await update_command(SimpleNamespace(message=message, effective_user=self.user), context)
        markup = message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "admin:update:yes")
        self.assertFalse((self.update_dir / "request").exists())

        query = SimpleNamespace(
            data="admin:update:yes", answer=AsyncMock(), edit_message_text=AsyncMock()
        )
        await update_callback(SimpleNamespace(callback_query=query, effective_user=self.user), context)
        self.assertTrue((self.update_dir / "request").exists())
        self.assertEqual(query.edit_message_text.await_args.args[0], texts.UPDATE_REQUESTED)

    async def test_update_declined_writes_nothing(self) -> None:
        query = SimpleNamespace(
            data="admin:update:no", answer=AsyncMock(), edit_message_text=AsyncMock()
        )
        await update_callback(
            SimpleNamespace(callback_query=query, effective_user=self.user), self.context()
        )
        self.assertFalse((self.update_dir / "request").exists())

    async def test_update_is_admin_only_and_keeps_confirmation_in_live_mode(self) -> None:
        self.write_check("b" * 40)
        message = SimpleNamespace(reply_text=AsyncMock())
        await update_command(
            SimpleNamespace(message=message, effective_user=SimpleNamespace(id=67890)),
            self.context(),
        )
        self.assertEqual(message.reply_text.await_args.args[0], texts.ADMIN_ONLY)
        await update_command(
            SimpleNamespace(message=message, effective_user=self.user),
            self.context(self.settings(dry_run="false")),
        )
        self.assertIn("Vuoi installare", message.reply_text.await_args.args[0])
        self.assertFalse((self.update_dir / "request").exists())
        query = SimpleNamespace(
            data="admin:update:yes", answer=AsyncMock(), edit_message_text=AsyncMock()
        )
        await update_callback(
            SimpleNamespace(callback_query=query, effective_user=self.user),
            self.context(self.settings(dry_run="false")),
        )
        self.assertTrue((self.update_dir / "request").exists())

    async def test_update_without_nas_check_is_not_requested(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        await update_command(SimpleNamespace(message=message, effective_user=self.user), self.context())
        self.assertEqual(message.reply_text.await_args.args[0], texts.UPDATE_NOT_CONFIGURED)

    def write_progress(self, stage: str, updated_at: str, summary: str = "bbbbbbb - Nuova") -> None:
        (self.update_dir / "progress").write_text(
            f"stage={stage}\nupdated_at={updated_at}\nsummary={summary}\n", encoding="utf-8"
        )

    async def test_requested_update_edits_the_same_message_until_the_outcome(self) -> None:
        self.write_check("b" * 40)
        context = self.context()
        context.bot.edit_message_text = AsyncMock()
        query = SimpleNamespace(
            data="admin:update:yes", answer=AsyncMock(), edit_message_text=AsyncMock(),
            message=SimpleNamespace(chat_id=12345, message_id=77),
        )
        await update_callback(SimpleNamespace(callback_query=query, effective_user=self.user), context)
        self.assertTrue((self.update_dir / "tracking").is_file())
        self.assertTrue((self.update_dir / "request").is_file())

        # Progress written before the request belongs to an older update.
        self.write_progress("restart", "2020-01-01T00:00:00Z")
        await update_progress_job(context)
        context.bot.edit_message_text.assert_not_awaited()

        self.write_progress("download", "2099-01-01T00:00:00Z", summary="")
        await update_progress_job(context)
        await update_progress_job(context)
        context.bot.edit_message_text.assert_awaited_once()
        first = context.bot.edit_message_text.await_args.kwargs
        self.assertEqual((first["chat_id"], first["message_id"]), (12345, 77))
        self.assertEqual(first["text"], texts.UPDATE_PROGRESS["download"])
        self.assertIn(
            "ack=2099-01-01T00:00:00Z",
            (self.update_dir / "progress_ack").read_text(encoding="utf-8"),
        )

        self.write_progress("restart", "2099-01-01T00:05:00Z")
        await update_progress_job(context)
        restart = context.bot.edit_message_text.await_args.kwargs["text"]
        self.assertIn("Riavvio il bot con bbbbbbb - Nuova", restart)
        self.assertIn(
            "ack=2099-01-01T00:05:00Z",
            (self.update_dir / "progress_ack").read_text(encoding="utf-8"),
        )

        # While an update is followed, the periodic notice stays silent.
        await update_status_job(context)
        context.bot.send_message.assert_not_awaited()

        (self.update_dir / "install").write_text(
            "result=ok\nfinished_at=2099-01-01T00:06:00Z\nrevision=" + "b" * 40
            + "\nsummary=bbbbbbb - Nuova\nreason=\n",
            encoding="utf-8",
        )
        fresh_context = self.context()
        fresh_context.bot.edit_message_text = AsyncMock()
        await update_progress_job(fresh_context)
        final = fresh_context.bot.edit_message_text.await_args.kwargs
        self.assertEqual(final["text"], texts.UPDATE_DONE.format(summary="bbbbbbb - Nuova"))
        self.assertIsNotNone(final["reply_markup"])
        self.assertFalse((self.update_dir / "tracking").exists())
        await update_status_job(fresh_context)
        await update_progress_job(fresh_context)
        fresh_context.bot.send_message.assert_not_awaited()
        fresh_context.bot.edit_message_text.assert_awaited_once()

    async def test_outcome_is_sent_when_the_request_message_cannot_be_edited(self) -> None:
        from bot.updates import UpdateStatusStore, UpdateTracking
        from datetime import datetime, timezone
        from telegram.error import BadRequest

        UpdateStatusStore(self.update_dir).write_tracking(
            UpdateTracking(
                chat_id=12345, message_id=77,
                requested_at=datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc), shown="",
            )
        )
        (self.update_dir / "install").write_text(
            "result=skipped\nfinished_at=2026-10-29T12:10:00Z\nrevision=\nsummary=\n"
            "reason=up_to_date\n",
            encoding="utf-8",
        )
        context = self.context()
        context.bot.edit_message_text = AsyncMock(side_effect=BadRequest("Message to edit not found"))
        await update_progress_job(context)
        context.bot.send_message.assert_awaited_once()
        self.assertIn("Nessun aggiornamento", context.bot.send_message.await_args.kwargs["text"])
        self.assertFalse((self.update_dir / "tracking").exists())

    async def test_temporary_telegram_error_retries_progress_without_ack(self) -> None:
        from bot.updates import UpdateStatusStore, UpdateTracking
        from datetime import datetime, timezone

        UpdateStatusStore(self.update_dir).write_tracking(
            UpdateTracking(
                chat_id=12345, message_id=77,
                requested_at=datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc), shown="",
            )
        )
        self.write_progress("install", "2026-10-29T12:01:00Z")
        context = self.context()
        context.bot.edit_message_text = AsyncMock(side_effect=TimedOut("private detail"))
        await update_progress_job(context)
        self.assertFalse((self.update_dir / "progress_ack").exists())
        context.bot.edit_message_text.side_effect = None
        await update_progress_job(context)
        self.assertTrue((self.update_dir / "progress_ack").is_file())

    async def test_status_job_notifies_admin_once(self) -> None:
        (self.update_dir / "install").write_text(
            "result=rolled_back\nfinished_at=2026-10-29T12:30:00Z\nrevision=" + "b" * 40
            + "\nsummary=bbbbbbb - Nuova\nreason=start\n",
            encoding="utf-8",
        )
        context = self.context()
        await update_status_job(context)
        await update_status_job(context)
        context.bot.send_message.assert_awaited_once()
        kwargs = context.bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], 12345)
        self.assertIn("ripristinato quella precedente", kwargs["text"])

    async def test_git_update_notice_is_admin_only_once_per_revision_with_confirmation(self) -> None:
        self.write_check("b" * 40)
        context = self.context()
        await update_status_job(context)
        first = context.bot.send_message.await_args.kwargs
        self.assertEqual(first["chat_id"], self.user.id)
        self.assertIn("nuova versione", first["text"])
        buttons = first["reply_markup"].inline_keyboard
        self.assertEqual(buttons[0][0].text, texts.MENU_UPDATE_BUTTON)
        self.assertEqual(buttons[0][0].callback_data, "menu:update")
        self.assertFalse((self.update_dir / "request").exists())

        # A later NAS check and a fresh bot context must not repeat the same notice.
        await update_status_job(context)
        fresh_context = self.context()
        self.write_check("b" * 40)
        await update_status_job(fresh_context)
        context.bot.send_message.assert_awaited_once()
        fresh_context.bot.send_message.assert_not_awaited()

        self.write_check("c" * 40)
        await update_status_job(context)
        self.assertEqual(context.bot.send_message.await_count, 2)

        query = SimpleNamespace(
            data="menu:update", message=SimpleNamespace(reply_text=AsyncMock()),
            answer=AsyncMock(), edit_message_text=AsyncMock(),
        )
        await menu_callback(
            SimpleNamespace(
                callback_query=query, effective_user=self.user,
                effective_message=query.message, message=None,
            ),
            context,
        )
        confirmation = query.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(confirmation.inline_keyboard[0][0].callback_data, "admin:update:yes")
        self.assertFalse((self.update_dir / "request").exists())

    async def test_failed_notification_is_retried_without_exposing_telegram_error(self) -> None:
        self.write_check("b" * 40)
        context = self.context()
        context.bot.send_message.side_effect = TimedOut("private network detail")
        with self.assertLogs("bot.application", level="ERROR") as captured:
            await update_status_job(context)
        self.assertNotIn("private", "\n".join(captured.output))
        self.assertFalse((self.update_dir / "notified").exists())
        context.bot.send_message.side_effect = None
        await update_status_job(context)
        self.assertEqual(context.bot.send_message.await_count, 2)
        self.assertTrue((self.update_dir / "notified").is_file())
        self.assertFalse((self.update_dir / "request").exists())

    async def test_no_update_means_no_notification_or_installation(self) -> None:
        context = self.context()
        await update_status_job(context)
        self.write_check("a" * 40)
        await update_status_job(context)
        context.bot.send_message.assert_not_awaited()
        self.assertFalse((self.update_dir / "request").exists())

    def test_update_command_is_registered_for_admin_only(self) -> None:
        names = [command.command for command in ADMIN_COMMANDS]
        self.assertIn("aggiorna", names)
        self.assertNotIn("aggiorna", [command.command for command in USER_COMMANDS])


if __name__ == "__main__":
    unittest.main()
