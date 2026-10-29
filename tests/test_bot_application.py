import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from pathlib import Path

from telegram.ext import ApplicationHandlerStop

from bot.application import (
    ADMIN_COMMANDS,
    ADMIN_HELP_TEXT,
    USER_COMMANDS,
    USER_HELP_TEXT,
    _format_preload_summary,
    _format_recent_expenses,
    authorize_update,
    cancel_command,
    clean_callback,
    clean_command,
    configure_bot_menu,
    help_command,
    latest_expenses_command,
    preload_options_job,
    preload_portal_options,
    queue_command,
    refresh_options_command,
    start_command,
    test_bilancio_command,
)
from bilancio.client import BilancioClientError, PortalOption, TransactionDirection
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
    def make_context(self, **bot_data: object):
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
        self.assertIn("Inviami", text)
        self.assertIn("scontrino", text)
        self.assertIn("SIMULATA", text)
        keyboard = message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(keyboard.keyboard[0][0].text, "/testspesa")
        admin_call = context.bot.set_my_commands.await_args
        self.assertEqual(admin_call.args[0], ADMIN_COMMANDS)
        self.assertEqual(admin_call.kwargs["scope"].chat_id, 12345)

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
        self.assertNotIn(
            "/testspesa", [button.text for row in keyboard.keyboard for button in row]
        )
        text = message.reply_text.await_args.args[0]
        for admin_detail in ("/testspesa", "/pulisci", "/aggiornaopzioni", "SIMULATA", "REALE"):
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
        self.assertIn("Ti scrivo appena ho finito", message.reply_text.await_args.args[0])

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
        self.assertIn("Opzioni aggiornate: 1 casse", sent["text"])
        self.assertIn("Cassa Co.ca: entrate 4, uscite errore", sent["text"])

    def test_preload_summary_reports_unreadable_accounts(self) -> None:
        self.assertIn("non riuscito", _format_preload_summary(None))

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
        for user_id, expected in ((12345, ADMIN_HELP_TEXT), (67890, USER_HELP_TEXT)):
            message = SimpleNamespace(reply_text=AsyncMock())
            await help_command(
                SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id)),
                context,
            )
            self.assertEqual(message.reply_text.await_args.args[0], expected)
        self.assertIn("/ultime", USER_HELP_TEXT)
        for command in ("/testspesa", "/pulisci", "/aggiornaopzioni", "/testbilancio"):
            self.assertNotIn(command, USER_HELP_TEXT)
            self.assertIn(command, ADMIN_HELP_TEXT)

    async def test_clean_is_admin_only(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())
        await clean_command(
            SimpleNamespace(message=message, effective_user=SimpleNamespace(id=67890)),
            self.make_context(settings=make_settings()),
        )
        self.assertEqual(
            message.reply_text.await_args.args[0],
            "Comando riservato all’amministratore.",
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
            self.assertIn("Spese eliminate: 1", query.edit_message_text.await_args.args[0])

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
        self.assertEqual(query.edit_message_text.await_args.args[0], "Pulizia annullata.")

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
            self.assertIn("✅ registrata", text)
            self.assertIn("⏳ in coda, non registrata", text)
            self.assertNotIn("Spesa b", text)
            self.assertIn("Cassa Co.ca", text)
            self.assertNotIn("{", text)

    async def test_cancel_command_clears_saved_state(self) -> None:
        workflow = Mock()
        workflow.cancel_current = AsyncMock(return_value=True)
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=12345),
        )
        context = self.make_context(receipt_workflow=workflow)

        await cancel_command(update, context)

        workflow.cancel_current.assert_awaited_once_with(12345)
        self.assertEqual(
            message.reply_text.await_args.args[0],
            "Operazione annullata.",
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
            "Comando riservato all’amministratore.",
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
                "Non ci sono spese in coda.",
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


if __name__ == "__main__":
    unittest.main()
