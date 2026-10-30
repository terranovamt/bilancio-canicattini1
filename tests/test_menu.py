import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram import Bot, InlineKeyboardMarkup, Update

from bot import application, messages
from bot.receipt_workflow import ReceiptWorkflow
from config import Settings
from db.database import Database
from storage.archive import FileArchive
from storage.pending import PendingReceiptStore


class InlineMenuTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = Settings.from_env({
            "BOT_TOKEN": "test-token-placeholder",
            "ALLOWED_USER_IDS": "12345,67890",
            "ADMIN_USER_ID": "12345",
            "DATA_PATH": str(self.root),
        })
        self.database = Database(self.root / "test.sqlite3")
        self.database.initialize()
        self.portal = SimpleNamespace(create_expense=AsyncMock())
        self.workflow = ReceiptWorkflow(
            settings=self.settings, database=self.database, analyzer=None,
            pending_store=PendingReceiptStore(self.root / "pending"),
            archive=FileArchive(self.root / "archive"), bilancio_client=self.portal,
        )
        self.context = SimpleNamespace(
            application=SimpleNamespace(bot_data={
                "settings": self.settings, "database": self.database,
                "receipt_workflow": self.workflow, "bilancio_client": self.portal,
            })
        )

    def update(self, action: str, user_id: int = 12345) -> SimpleNamespace:
        message = SimpleNamespace(reply_text=AsyncMock())
        query = SimpleNamespace(
            data=f"menu:{action}", message=message,
            answer=AsyncMock(), edit_message_text=AsyncMock(),
        )
        return SimpleNamespace(
            message=None, effective_message=message,
            effective_user=SimpleNamespace(id=user_id), callback_query=query,
        )

    @staticmethod
    def callback_values(markup: InlineKeyboardMarkup) -> list[str]:
        return [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]

    async def test_home_and_help_are_slash_free_and_preserve_pending_entry(self) -> None:
        update = self.update("manual")
        await application.menu_callback(update, self.context)
        state = self.database.get_conversation_state(12345)
        for action in ("home", "help", "receipt", "admin", "home"):
            with self.subTest(action=action):
                update = self.update(action)
                await application.menu_callback(update, self.context)
                query = update.callback_query
                query.answer.assert_awaited_once_with()
                rendered = query.edit_message_text.await_args
                self.assertNotIn("/", rendered.args[0])
                for row in rendered.kwargs["reply_markup"].inline_keyboard:
                    self.assertTrue(all("/" not in button.text for button in row))
                self.assertEqual(self.database.get_conversation_state(12345), state)
                self.portal.create_expense.assert_not_awaited()

    async def test_non_admin_cannot_forge_admin_buttons(self) -> None:
        for action in ("admin", "manual", "income", "health", "options", "version", "update", "clean", "test_on", "test_off"):
            with self.subTest(action=action):
                update = self.update(action, user_id=67890)
                await application.menu_callback(update, self.context)
                update.callback_query.answer.assert_awaited_once_with(messages.ADMIN_ONLY)
                update.callback_query.edit_message_text.assert_not_awaited()
                update.effective_message.reply_text.assert_not_awaited()
        self.assertIsNone(self.database.get_conversation_state(67890))
        self.portal.create_expense.assert_not_awaited()

    async def test_mode_is_real_by_default_and_persists_until_admin_changes_it(self) -> None:
        for action, expected in (("admin", False), ("test_on", True), ("test_on", True), ("test_off", False)):
            with self.subTest(action=action, expected=expected):
                update = self.update(action)
                await application.menu_callback(update, self.context)
                reopened = Database(self.root / "test.sqlite3")
                reopened.initialize()
                self.assertEqual(reopened.get_test_mode(default=False), expected)
                rendered = update.callback_query.edit_message_text.await_args
                self.assertIn(messages.MODE_TRIAL if expected else messages.MODE_REAL, rendered.args[0])
                self.assertIn(
                    "menu:test_off" if expected else "menu:test_on",
                    self.callback_values(rendered.kwargs["reply_markup"]),
                )
                home = self.update("home", user_id=67890)
                await application.menu_callback(home, self.context)
                self.assertIn(
                    messages.MODE_TRIAL if expected else messages.MODE_REAL,
                    home.callback_query.edit_message_text.await_args.args[0],
                )
        self.portal.create_expense.assert_not_awaited()

    async def test_mode_change_does_not_clear_active_entry(self) -> None:
        await application.menu_callback(self.update("manual"), self.context)
        before = self.database.get_conversation_state(12345)
        await application.menu_callback(self.update("test_on"), self.context)
        self.assertEqual(self.database.get_conversation_state(12345), before)
        stored = self.database.get_expense(before[1]["expense_id"])
        self.assertFalse(stored.payload["dry_run"])
        self.portal.create_expense.assert_not_awaited()

    async def test_explicit_real_choice_overrides_legacy_trial_configuration_after_restart(self) -> None:
        self.assertTrue(self.database.get_test_mode(default=True))
        await application.menu_callback(self.update("test_off"), self.context)
        reopened = Database(self.root / "test.sqlite3")
        reopened.initialize()
        self.assertFalse(reopened.get_test_mode(default=True))

    async def test_mode_change_failure_is_reported_without_claiming_success(self) -> None:
        update = self.update("test_on")
        with patch.object(self.database, "set_test_mode", side_effect=sqlite3.OperationalError("locked")):
            with self.assertLogs("bot.application", level="ERROR"):
                await application.menu_callback(update, self.context)
        self.assertEqual(
            update.callback_query.edit_message_text.await_args.args[0], messages.MODE_CHANGE_FAILED
        )
        self.assertFalse(self.database.get_test_mode(default=False))

    async def test_manual_buttons_start_real_workflow_without_saving(self) -> None:
        for action, direction in (("manual", None), ("income", "entrata")):
            with self.subTest(action=action):
                update = self.update(action)
                await application.menu_callback(update, self.context)
                state, data = self.database.get_conversation_state(12345)
                self.assertEqual(state, "awaiting_receipt_data")
                stored = self.database.get_expense(data["expense_id"])
                self.assertIsNone(stored.payload["total_amount"])
                self.assertEqual(stored.payload["direction"], direction)
                reply = update.effective_message.reply_text.await_args
                self.assertIn(messages.MANUAL_DATE, reply.args[0])
                self.assertNotRegex(reply.args[0], r"/[a-zA-Z]")
                self.assertEqual(
                    self.callback_values(reply.kwargs["reply_markup"]),
                    [
                        "receipt:today", "receipt:edit:date", "receipt:edit:merchant",
                        "receipt:edit:total_amount", "receipt:cancel", "menu:home",
                    ],
                )
                self.portal.create_expense.assert_not_awaited()
                await self.workflow.cancel_current(12345)

    async def test_each_action_routes_to_existing_command_with_original_sender(self) -> None:
        handlers = {
            "manual": "manual_expense_command", "income": "manual_income_command",
            "latest": "latest_expenses_command", "summary": "summary_command",
            "queue": "queue_command", "retry": "retry_command",
            "cancel": "cancel_command", "health": "test_bilancio_command",
            "options": "refresh_options_command", "update": "update_command",
            "clean": "clean_command",
        }
        for action, name in handlers.items():
            with self.subTest(action=action):
                update = self.update(action)
                with patch.object(application, name, new_callable=AsyncMock) as handler:
                    await application.menu_callback(update, self.context)
                    handler.assert_awaited_once_with(update, self.context)
                update.callback_query.answer.assert_awaited_once_with()

    async def test_cancel_from_menu_clears_entry_and_returns_navigation(self) -> None:
        await application.menu_callback(self.update("manual"), self.context)
        update = self.update("cancel")
        await application.menu_callback(update, self.context)
        self.assertIsNone(self.database.get_conversation_state(12345))
        reply = update.effective_message.reply_text.await_args
        self.assertEqual(reply.args[0], messages.CANCELLED)
        self.assertIn("menu:home", self.callback_values(reply.kwargs["reply_markup"]))

    async def test_admin_tools_and_summary_accept_callback_messages(self) -> None:
        self.portal.health_check = AsyncMock()
        self.portal.clear_options_cache = AsyncMock()
        self.context.application.job_queue = Mock()
        for action in ("health", "options", "summary", "clean", "update"):
            with self.subTest(action=action):
                update = self.update(action)
                update.effective_message.reply_text.return_value = SimpleNamespace(
                    chat_id=12345, message_id=2
                )
                await application.menu_callback(update, self.context)
                reply = update.effective_message.reply_text.await_args
                self.assertIsNotNone(reply)
                self.assertIn("menu:home", self.callback_values(reply.kwargs["reply_markup"]))
        self.portal.health_check.assert_awaited_once()
        self.portal.clear_options_cache.assert_awaited_once()
        self.portal.create_expense.assert_not_awaited()
        self.assertFalse((self.root / "update" / "request").exists())

    async def test_opening_clean_screen_does_not_clear_history(self) -> None:
        await application.menu_callback(self.update("manual"), self.context)
        before = self.database.get_conversation_state(12345)
        update = self.update("clean")
        await application.menu_callback(update, self.context)
        self.assertEqual(self.database.get_conversation_state(12345), before)
        buttons = self.callback_values(
            update.effective_message.reply_text.await_args.kwargs["reply_markup"]
        )
        self.assertIn("admin:clean:yes", buttons)
        self.assertIn("admin:clean:no", buttons)

    async def test_latest_and_queue_callbacks_work_without_command_message(self) -> None:
        for action in ("latest", "queue"):
            update = self.update(action)
            await application.menu_callback(update, self.context)
            reply = update.callback_query.edit_message_text.await_args
            self.assertIsNotNone(reply)
            self.assertIn("menu:home", self.callback_values(reply.kwargs["reply_markup"]))

    async def test_repeated_home_button_does_not_repeat_identical_edit(self) -> None:
        update = self.update("home")
        query = update.callback_query
        query.message.text = application._home_text(False)
        query.message.reply_markup = application._home_keyboard(True)
        await application.menu_callback(update, self.context)
        query.answer.assert_awaited_once_with()
        query.edit_message_text.assert_not_awaited()

    async def test_unknown_action_has_explicit_message_and_home_button(self) -> None:
        update = self.update("unknown")
        await application.menu_callback(update, self.context)
        reply = update.callback_query.edit_message_text.await_args
        self.assertEqual(reply.args[0], messages.MENU_UNKNOWN)
        self.assertEqual(self.callback_values(reply.kwargs["reply_markup"]), ["menu:home"])

    async def test_read_only_menu_bypasses_slow_work_but_actions_do_not(self) -> None:
        processor = application.ResponsiveUpdateProcessor()
        bot = Bot(self.settings.bot_token)
        entered = asyncio.Event()
        release = asyncio.Event()
        mutation = AsyncMock()

        async def slow() -> None:
            entered.set()
            await release.wait()

        def callback_update(action: str) -> Update:
            return Update.de_json({
                "update_id": 1,
                "callback_query": {
                    "id": "test", "chat_instance": "test",
                    "from": {"id": 12345, "is_bot": False, "first_name": "Test"},
                    "data": f"menu:{action}",
                    "message": {
                        "message_id": 1, "date": 0,
                        "chat": {"id": 12345, "type": "private"}, "text": "Menu",
                    },
                },
            }, bot)

        blocked = asyncio.create_task(processor.process_update(object(), slow()))
        await asyncio.wait_for(entered.wait(), timeout=1)
        queued = asyncio.create_task(processor.process_update(callback_update("manual"), mutation()))
        try:
            for action in ("home", "help", "receipt", "admin", "version"):
                response = AsyncMock()
                await asyncio.wait_for(
                    processor.process_update(callback_update(action), response()), timeout=0.5
                )
                response.assert_awaited_once()
                mutation.assert_not_awaited()
        finally:
            release.set()
            await asyncio.gather(blocked, queued)
        mutation.assert_awaited_once()
