import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from datetime import date, timedelta, datetime, timezone
from dataclasses import replace

from telegram import Bot, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update

from bot import application, messages
from bot.receipt_workflow import ReceiptWorkflow
from config import Settings
from db.database import Database
from db.gemini_quota import GeminiQuotaStore, day_window
from storage.archive import FileArchive
from storage.pending import PendingReceiptStore
from vision.receipt_schema import ReceiptData


class InlineMenuTests(unittest.IsolatedAsyncioTestCase):
    def test_paired_layouts_and_full_width_cancel_as_last_row(self) -> None:
        for admin in (False, True):
            for pending in (False, True):
                keyboard = application._menu_keyboard(admin, pending=pending).keyboard
                pairs = keyboard[:-1] if pending else keyboard
                self.assertTrue(all(len(row) == 2 for row in pairs))
                if pending:
                    self.assertEqual([b.text for b in keyboard[-1]], [messages.MENU_CANCEL_BUTTON])
                if admin:
                    self.assertEqual([b.text for b in pairs[-1]], [messages.MENU_ADMIN_BUTTON, messages.MENU_API_STATUS_BUTTON])
        for mode in (False, True):
            for pending in (False, True):
                keyboard = application._admin_keyboard(mode, pending=pending).keyboard
                pairs = keyboard[:-1] if pending else keyboard
                self.assertEqual(len(pairs), 4)
                self.assertTrue(all(len(row) == 2 for row in pairs))
                self.assertEqual([b.text for b in pairs[0]], [messages.MENU_MANUAL_BUTTON, messages.MENU_API_STATUS_BUTTON])
                self.assertEqual([b.text for b in pairs[1]], [
                    messages.MENU_VERSION_BUTTON, messages.MENU_TEST_OFF_BUTTON if mode else messages.MENU_TEST_ON_BUTTON
                ])
                self.assertEqual([b.text for b in pairs[-1]], [messages.MENU_CLEAN_BUTTON, messages.HOME_BUTTON])
                if pending:
                    self.assertEqual([b.text for b in keyboard[-1]], [messages.MENU_CANCEL_BUTTON])

    async def test_admin_screen_keeps_paired_layout_when_pending_state_changes(self) -> None:
        await application.menu_text(self.text_update(messages.MENU_ADMIN_BUTTON), self.context)
        await application.menu_text(self.text_update(messages.MENU_MANUAL_BUTTON), self.context)
        active = self.text_update("active")
        await application.sync_operation_menu(active, self.context)
        rows = active.message.reply_text.await_args.kwargs["reply_markup"].keyboard
        self.assertEqual([b.text for b in rows[-1]], [messages.MENU_CANCEL_BUTTON])
        self.assertEqual([b.text for b in rows[0]], [messages.MENU_MANUAL_BUTTON, messages.MENU_API_STATUS_BUTTON])
        self.database.delete_conversation_state(12345)
        done = self.text_update("done")
        await application.sync_operation_menu(done, self.context)
        rows = done.message.reply_text.await_args.kwargs["reply_markup"].keyboard
        self.assertTrue(all(len(row) == 2 for row in rows))
        self.assertNotIn(messages.MENU_CANCEL_BUTTON, [b.text for row in rows for b in row])
        self.assertEqual([b.text for b in rows[-1]], [messages.MENU_CLEAN_BUTTON, messages.HOME_BUTTON])

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

    def text_update(self, text: str, user_id: int = 12345) -> SimpleNamespace:
        message = SimpleNamespace(text=text, entities=(), reply_text=AsyncMock())
        return SimpleNamespace(
            message=message, effective_message=message,
            effective_user=SimpleNamespace(id=user_id), callback_query=None,
        )

    @staticmethod
    def callback_values(markup: InlineKeyboardMarkup | None) -> list[str]:
        if markup is None:
            return []
        return [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]

    @staticmethod
    def rendered(update: SimpleNamespace):
        edited = update.callback_query.edit_message_text.await_args
        return edited if edited is not None else update.effective_message.reply_text.await_args

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
                rendered = self.rendered(update)
                self.assertNotIn("/", rendered.args[0])
                markup = rendered.kwargs["reply_markup"]
                if action == "home":
                    self.assertIsInstance(markup, ReplyKeyboardMarkup)
                    query.edit_message_text.assert_not_awaited()
                elif isinstance(markup, InlineKeyboardMarkup):
                    for row in markup.inline_keyboard:
                        self.assertTrue(all("/" not in button.text for button in row))
                self.assertEqual(self.database.get_conversation_state(12345), state)
                self.portal.create_expense.assert_not_awaited()

    async def test_non_admin_cannot_forge_admin_buttons(self) -> None:
        for action in ("admin", "manual", "health", "options", "version", "update", "clean", "test_on", "test_off", "apistatus"):
            with self.subTest(action=action):
                update = self.update(action, user_id=67890)
                await application.menu_callback(update, self.context)
                update.callback_query.answer.assert_awaited_once_with(messages.ADMIN_ONLY)
                update.callback_query.edit_message_text.assert_not_awaited()
                update.effective_message.reply_text.assert_not_awaited()
        self.assertIsNone(self.database.get_conversation_state(67890))
        self.portal.create_expense.assert_not_awaited()

    async def test_api_status_command_and_button_are_admin_only_and_ignore_unknown_users(self) -> None:
        self.assertIn("apistatus", {command.command for command in application.ADMIN_COMMANDS})
        self.assertNotIn("apistatus", {command.command for command in application.USER_COMMANDS})
        labels = [b.text for row in application._admin_keyboard().keyboard for b in row]
        self.assertIn(messages.MENU_API_STATUS_BUTTON, labels)
        labels = [b.text for row in application._menu_keyboard(False).keyboard for b in row]
        self.assertNotIn(messages.MENU_API_STATUS_BUTTON, labels)
        for user_id in (67890, 99999):
            update = self.text_update("/apistatus", user_id)
            with patch("bot.application._api_status") as status:
                await application.api_status_command(update, self.context)
            status.assert_not_called()
            if user_id == 67890:
                update.message.reply_text.assert_awaited_once_with(messages.ADMIN_ONLY)
            else:
                update.message.reply_text.assert_not_awaited()
        forged = self.text_update(messages.MENU_API_STATUS_BUTTON, 67890)
        await application.menu_text(forged, self.context)
        forged.message.reply_text.assert_awaited_once_with(messages.ADMIN_ONLY)

    async def test_api_status_not_configured_legacy_and_storage_error_are_explicit(self) -> None:
        for settings, expected in (
            (self.settings, messages.API_STATUS_NOT_CONFIGURED),
            (replace(self.settings, gemini_api_key="synthetic-key", gemini_model="legacy"),
             messages.API_STATUS_LEGACY.format(model="legacy")),
        ):
            self.context.application.bot_data["settings"] = settings
            update = self.text_update("/apistatus")
            await application.api_status_command(update, self.context)
            self.assertEqual(update.message.reply_text.await_args.args[0], expected)
        update = self.text_update("/apistatus")
        with patch("bot.application._api_status", side_effect=sqlite3.OperationalError("synthetic")):
            await application.api_status_command(update, self.context)
        self.assertEqual(update.message.reply_text.await_args.args[0], messages.API_STATUS_FAILED)

    async def test_api_status_shows_usage_percentages_waits_without_changing_receipt_or_quota(self) -> None:
        await application.menu_callback(self.update("manual"), self.context)
        before = self.database.get_conversation_state(12345)
        settings = replace(
            self.settings, gemini_api_key="synthetic-key", gemini_models=("lite", "flash", "pro"),
            gemini_rpm_limits=(2, 10, 1), gemini_rpd_limits=(10, 100, 50),
        )
        self.context.application.bot_data["settings"] = settings
        now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
        stamp = now.timestamp()
        store = GeminiQuotaStore(self.root / "gemini.sqlite3")
        store.reserve("lite", 2, 10, 80, stamp)
        store.reserve("lite", 2, 10, 80, stamp)
        store.record_cooldown("flash", stamp, stamp + 90)
        store.record_cooldown("pro", stamp, None)
        for method in ("command", "text", "callback"):
            update = self.update("apistatus") if method == "callback" else self.text_update(messages.MENU_API_STATUS_BUTTON)
            with patch("bot.application.datetime", wraps=datetime) as clock:
                clock.now.return_value = now
                if method == "command":
                    await application.api_status_command(update, self.context)
                elif method == "text":
                    await application.menu_text(update, self.context)
                else:
                    await application.menu_callback(update, self.context)
            rendered = "\n".join(call.args[0] for call in update.effective_message.reply_text.await_args_list)
            self.assertIn("Minuto: 2/2 (100.0%)", rendered)
            self.assertIn("Giorno: 2/10 (20.0%)", rendered)
            self.assertIn("13:01:00", rendered)
            self.assertIn("13:01:30", rendered)
            self.assertIn("reset sconosciuto", rendered)
            self.assertIn("non consumo ufficiale Google", rendered)
            self.assertNotIn("synthetic-key", rendered)
            self.assertEqual(self.database.get_conversation_state(12345), before)
        counts = store.status(("lite",), (2,), (10,), stamp)[0]
        self.assertEqual(counts.daily_requests, 2)
        self.portal.create_expense.assert_not_awaited()

    def test_api_status_splits_large_reports_with_all_models(self) -> None:
        models = tuple(f"model-{i:02d}-" + "a" * 80 for i in range(24))
        settings = replace(
            self.settings, gemini_api_key="synthetic-key", gemini_models=models,
            gemini_rpm_limits=(10,) * 24, gemini_rpd_limits=(100,) * 24,
        )
        chunks = application._api_status(settings)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 3800 for chunk in chunks))
        self.assertTrue(all(model in "\n".join(chunks) for model in models))

    async def test_install_button_is_only_inside_version_screen_not_admin_keyboard(self) -> None:
        for mode in (True, False):
            labels = [button.text for row in application._admin_keyboard(mode).keyboard for button in row]
            self.assertIn(messages.MENU_VERSION_BUTTON, labels)
            self.assertNotIn(messages.MENU_UPDATE_BUTTON, labels)
        update = self.update("version")
        with patch("bot.application._version_status", return_value="Versione di prova"):
            await application.menu_callback(update, self.context)
        rendered = self.rendered(update)
        self.assertIn("menu:update", self.callback_values(rendered.kwargs["reply_markup"]))
        self.portal.create_expense.assert_not_awaited()

    async def test_non_admin_can_open_income_from_main_menu(self) -> None:
        update = self.update("income", user_id=67890)
        await application.menu_callback(update, self.context)
        update.callback_query.answer.assert_awaited_once_with()
        state, data = self.database.get_conversation_state(67890)
        self.assertEqual(state, "awaiting_receipt_data")
        self.assertEqual(self.database.get_expense(data["expense_id"]).payload["direction"], "entrata")
        self.portal.create_expense.assert_not_awaited()

    async def test_mode_is_real_by_default_and_persists_until_admin_changes_it(self) -> None:
        for action, expected in (("admin", False), ("test_on", True), ("test_on", True), ("test_off", False)):
            with self.subTest(action=action, expected=expected):
                update = self.update(action)
                await application.menu_callback(update, self.context)
                reopened = Database(self.root / "test.sqlite3")
                reopened.initialize()
                self.assertEqual(reopened.get_test_mode(default=False), expected)
                rendered = self.rendered(update)
                self.assertEqual(messages.MODE_TRIAL in rendered.args[0], expected)
                self.assertNotIn(messages.MODE_REAL, rendered.args[0])
                labels = [b.text for row in rendered.kwargs["reply_markup"].keyboard for b in row]
                self.assertIn(messages.MENU_TEST_OFF_BUTTON if expected else messages.MENU_TEST_ON_BUTTON, labels)
                home = self.update("home", user_id=67890)
                await application.menu_callback(home, self.context)
                self.assertNotIn(messages.MODE_TRIAL, home.effective_message.reply_text.await_args.args[0])
                self.assertNotIn(messages.MODE_REAL, home.effective_message.reply_text.await_args.args[0])
                admin_home = self.update("home")
                await application.menu_callback(admin_home, self.context)
                self.assertEqual(
                    messages.MODE_TRIAL in admin_home.effective_message.reply_text.await_args.args[0],
                    expected,
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
                self.assertIn(messages.MANUAL_MERCHANT, reply.args[0])
                self.assertNotRegex(reply.args[0], r"/[a-zA-Z]")
                buttons = self.callback_values(reply.kwargs["reply_markup"])
                self.assertEqual(buttons, ["receipt:cancel"])
                self.assertNotIn("menu:home", buttons)
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
        self.assertIsInstance(reply.kwargs["reply_markup"], ReplyKeyboardMarkup)
        self.assertNotIn(messages.MENU_CANCEL_BUTTON, [b.text for row in reply.kwargs["reply_markup"].keyboard for b in row])

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
                buttons = self.callback_values(reply.kwargs.get("reply_markup"))
                self.assertNotIn("menu:home", buttons)
                if action != "summary":
                    self.assertIn("menu:admin", buttons)
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
            self.assertNotIn("menu:home", self.callback_values(reply.kwargs.get("reply_markup")))

    async def test_repeated_admin_button_does_not_repeat_identical_edit(self) -> None:
        update = self.update("admin")
        query = update.callback_query
        query.message.text = messages.MENU_ADMIN
        query.message.reply_markup = application._admin_keyboard(False)
        await application.menu_callback(update, self.context)
        query.answer.assert_awaited_once_with()
        query.edit_message_text.assert_not_awaited()

    async def test_old_home_button_restores_persistent_menu_keyboard(self) -> None:
        for user_id, admin in ((12345, True), (67890, False)):
            with self.subTest(admin=admin):
                update = self.update("home", user_id=user_id)
                await application.menu_callback(update, self.context)
                markup = update.effective_message.reply_text.await_args.kwargs["reply_markup"]
                self.assertIsInstance(markup, ReplyKeyboardMarkup)
                self.assertTrue(markup.is_persistent)
                labels = [button.text for row in markup.keyboard for button in row]
                self.assertIn(messages.MENU_INCOME_BUTTON, labels)
                self.assertEqual(messages.MENU_ADMIN_BUTTON in labels, admin)

    async def test_menu_labels_route_text_to_actions(self) -> None:
        update = self.text_update(messages.MENU_HELP_BUTTON, user_id=67890)
        await application.menu_text(update, self.context)
        self.assertEqual(update.message.reply_text.await_args.args[0], application.USER_HELP_TEXT)

        update = self.text_update(messages.MENU_ADMIN_BUTTON, user_id=67890)
        await application.menu_text(update, self.context)
        update.message.reply_text.assert_awaited_once_with(messages.ADMIN_ONLY)

        update = self.text_update(messages.MENU_ADMIN_BUTTON)
        await application.menu_text(update, self.context)
        markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertIsInstance(markup, ReplyKeyboardMarkup)
        self.assertTrue(markup.is_persistent)
        labels = [b.text for row in markup.keyboard for b in row]
        self.assertIn(messages.MENU_MANUAL_BUTTON, labels)
        self.assertIn(messages.HOME_BUTTON, labels)
        self.assertNotIn(messages.MENU_RECEIPT_BUTTON, labels)

    async def test_all_admin_reply_labels_reject_forgery_without_mutating_state(self) -> None:
        await application.menu_text(self.text_update(messages.MENU_INCOME_BUTTON, 67890), self.context)
        before = self.database.get_conversation_state(67890)
        for label, action in application.MENU_LABELS.items():
            if action not in application._ADMIN_ACTIONS:
                continue
            with self.subTest(action=action):
                update = self.text_update(label, 67890)
                await application.menu_text(update, self.context)
                update.message.reply_text.assert_awaited_once_with(messages.ADMIN_ONLY)
                self.assertEqual(self.database.get_conversation_state(67890), before)
        self.portal.create_expense.assert_not_awaited()

    async def test_admin_reply_keyboard_returns_to_main_and_preserves_entry(self) -> None:
        await application.menu_text(self.text_update(messages.MENU_INCOME_BUTTON), self.context)
        before = self.database.get_conversation_state(12345)
        for label in (messages.MENU_ADMIN_BUTTON, messages.HOME_BUTTON):
            update = self.text_update(label)
            await application.menu_text(update, self.context)
            markup = update.message.reply_text.await_args.kwargs["reply_markup"]
            self.assertIsInstance(markup, ReplyKeyboardMarkup)
            self.assertTrue(markup.is_persistent)
            labels = [b.text for row in markup.keyboard for b in row]
            self.assertEqual(messages.HOME_BUTTON in labels, label == messages.MENU_ADMIN_BUTTON)
            self.assertEqual(messages.MENU_RECEIPT_BUTTON in labels, label == messages.HOME_BUTTON)
            self.assertEqual(self.database.get_conversation_state(12345), before)

    async def test_menu_label_during_form_is_not_taken_as_description(self) -> None:
        await application.menu_callback(self.update("income", user_id=67890), self.context)
        _, data = self.database.get_conversation_state(67890)
        update = self.text_update(messages.MENU_CANCEL_BUTTON, user_id=67890)
        await application.menu_text(update, self.context)
        self.assertIsNone(self.database.get_conversation_state(67890))
        self.assertEqual(update.message.reply_text.await_args.args[0], messages.CANCELLED)
        stored = self.database.get_expense(data["expense_id"])
        self.assertTrue(stored is None or stored.payload["merchant"] is None)

    async def test_cancel_button_tracks_pending_state_home_and_restart(self) -> None:
        def labels(update):
            return [button.text for row in update.message.reply_text.await_args.kwargs["reply_markup"].keyboard for button in row]

        for user_id in (12345, 67890):
            idle = self.text_update(messages.HOME_BUTTON, user_id)
            await application.menu_text(idle, self.context)
            self.assertNotIn(messages.MENU_CANCEL_BUTTON, labels(idle))
            await application.menu_text(self.text_update(messages.MENU_INCOME_BUTTON, user_id), self.context)
            active = self.text_update("pending", user_id)
            await application.sync_operation_menu(active, self.context)
            self.assertIn(messages.MENU_CANCEL_BUTTON, labels(active))
            same = self.text_update("still pending", user_id)
            await application.sync_operation_menu(same, self.context)
            same.message.reply_text.assert_not_awaited()
            self.context.application.bot_data.pop("operation_menu_visibility", None)
            home = self.text_update(messages.HOME_BUTTON, user_id)
            await application.menu_text(home, self.context)
            self.assertIn(messages.MENU_CANCEL_BUTTON, labels(home))
            cancel = self.text_update(messages.MENU_CANCEL_BUTTON, user_id)
            await application.menu_text(cancel, self.context)
            self.assertNotIn(messages.MENU_CANCEL_BUTTON, labels(cancel))
            await application.sync_operation_menu(cancel, self.context)
            cancel.message.reply_text.assert_awaited_once()

    async def test_menu_removes_cancel_after_state_completion_and_ignores_unknown_users(self) -> None:
        await application.menu_text(self.text_update(messages.MENU_INCOME_BUTTON), self.context)
        active = self.text_update("active")
        await application.sync_operation_menu(active, self.context)
        self.database.delete_conversation_state(12345)
        done = self.text_update("done")
        await application.sync_operation_menu(done, self.context)
        markup = done.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertNotIn(messages.MENU_CANCEL_BUTTON, [b.text for row in markup.keyboard for b in row])
        self.assertIn(messages.MENU_ADMIN_BUTTON, [b.text for row in markup.keyboard for b in row])
        unknown = self.text_update("pending", 99999)
        await application.sync_operation_menu(unknown, self.context)
        unknown.message.reply_text.assert_not_awaited()

    async def test_operation_menu_errors_are_explicit_and_failed_delivery_retries(self) -> None:
        update = self.text_update("active")
        with patch.object(self.database, "get_conversation_state", side_effect=sqlite3.OperationalError("synthetic")):
            await application.sync_operation_menu(update, self.context)
        update.message.reply_text.assert_awaited_once_with(messages.OPERATION_MENU_FAILED)
        await application.menu_text(self.text_update(messages.MENU_INCOME_BUTTON), self.context)
        from telegram.error import NetworkError
        failed = self.text_update("failed")
        failed.message.reply_text.side_effect = NetworkError("synthetic")
        await application.sync_operation_menu(failed, self.context)
        retry = self.text_update("retry")
        await application.sync_operation_menu(retry, self.context)
        retry.message.reply_text.assert_awaited_once()

    async def test_receipt_menu_shows_cancel_before_analysis_starts(self) -> None:
        self.workflow._analyzer = SimpleNamespace(analyze=AsyncMock(return_value=ReceiptData(confidence=0)))
        self.context.bot = SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(
            download_as_bytearray=AsyncMock(return_value=bytearray(b"synthetic-image"))
        )))
        update = self.text_update("")
        update.message.photo = [SimpleNamespace(file_id="synthetic", file_size=15)]
        update.message.document = None

        async def analyze(file_bytes, file_name):
            self.assertTrue(self.context.application.bot_data["operation_menu_visibility"][12345])
            rendered = [
                call.kwargs.get("reply_markup") for call in update.message.reply_text.await_args_list
            ]
            self.assertTrue(any(
                isinstance(markup, ReplyKeyboardMarkup)
                and messages.MENU_CANCEL_BUTTON in [b.text for row in markup.keyboard for b in row]
                for markup in rendered
            ))
            return ReceiptData(confidence=0)

        self.workflow._analyzer.analyze.side_effect = analyze
        await application.receive_receipt(update, self.context)

    async def test_unknown_action_has_explicit_message_and_home_button(self) -> None:
        update = self.update("unknown")
        await application.menu_callback(update, self.context)
        reply = update.callback_query.edit_message_text.await_args
        self.assertEqual(reply.args[0], messages.MENU_UNKNOWN)

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
            for action in ("home", "help", "receipt", "admin", "version", "apistatus"):
                response = AsyncMock()
                await asyncio.wait_for(
                    processor.process_update(callback_update(action), response()), timeout=0.5
                )
                response.assert_awaited_once()
                mutation.assert_not_awaited()
            for label in (messages.MENU_HELP_BUTTON, messages.MENU_RECEIPT_BUTTON, messages.MENU_API_STATUS_BUTTON):
                response = AsyncMock()
                text_update = Update.de_json({
                    "update_id": 3,
                    "message": {
                        "message_id": 3, "date": 0, "text": label,
                        "chat": {"id": 12345, "type": "private"},
                        "from": {"id": 12345, "is_bot": False, "first_name": "Test"},
                    },
                }, bot)
                await asyncio.wait_for(
                    processor.process_update(text_update, response()), timeout=0.5
                )
                response.assert_awaited_once()
        finally:
            release.set()
            await asyncio.gather(blocked, queued)
        mutation.assert_awaited_once()
