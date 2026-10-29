import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from pathlib import Path

from telegram.ext import ApplicationHandlerStop

from bot.application import (
    ADMIN_COMMANDS,
    HELP_TEXT,
    USER_COMMANDS,
    _format_recent_expenses,
    authorize_update,
    cancel_command,
    configure_bot_menu,
    help_command,
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

    async def test_preload_reads_every_account_direction_without_submission(self) -> None:
        accounts = [PortalOption("a", "Cassa Co.ca"), PortalOption("b", "Cassa EG")]
        client = SimpleNamespace(
            list_accounts=AsyncMock(return_value=accounts),
            list_transaction_types=AsyncMock(
                side_effect=[[], BilancioClientError("Unavailable"), [], []]
            ),
            create_expense=AsyncMock(),
        )

        loaded = await preload_portal_options(client)

        self.assertEqual(loaded, 3)
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

    async def test_help_lists_registered_commands_in_italian(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())

        await help_command(SimpleNamespace(message=message), SimpleNamespace())

        self.assertEqual(message.reply_text.await_args.args[0], HELP_TEXT)
        self.assertIn("/ultime", HELP_TEXT)
        self.assertIn("/testspesa", HELP_TEXT)

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
