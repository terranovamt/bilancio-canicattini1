import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from telegram.ext import ApplicationHandlerStop

from bot.application import (
    HELP_TEXT,
    _format_recent_expenses,
    authorize_update,
    cancel_command,
    help_command,
    start_command,
)
from config import Settings
from db.database import StoredExpense


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
        update = SimpleNamespace(message=message)

        await start_command(update, SimpleNamespace())

        text = message.reply_text.await_args.args[0]
        self.assertIn("Inviami", text)
        self.assertIn("scontrino", text)

    async def test_help_lists_registered_commands_in_italian(self) -> None:
        message = SimpleNamespace(reply_text=AsyncMock())

        await help_command(SimpleNamespace(message=message), SimpleNamespace())

        self.assertEqual(message.reply_text.await_args.args[0], HELP_TEXT)
        self.assertIn("/ultime", HELP_TEXT)

    async def test_cancel_command_clears_saved_state(self) -> None:
        database = Mock()
        database.delete_conversation_state.return_value = True
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            message=message,
            effective_user=SimpleNamespace(id=12345),
        )
        context = self.make_context(database=database)

        await cancel_command(update, context)

        database.delete_conversation_state.assert_called_once_with(12345)
        self.assertEqual(
            message.reply_text.await_args.args[0],
            "Operazione annullata.",
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
