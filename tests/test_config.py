import unittest

from config import ConfigurationError, Settings


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environ = {
            "BOT_TOKEN": "test-token",
            "ALLOWED_USER_IDS": "12345, 67890",
            "ADMIN_USER_ID": "12345",
        }

    def test_loads_required_settings_and_defaults(self) -> None:
        settings = Settings.from_env(self.environ)

        self.assertEqual(settings.allowed_user_ids, frozenset({12345, 67890}))
        self.assertEqual(settings.admin_user_id, 12345)
        self.assertEqual(settings.data_path, "/data")
        self.assertEqual(settings.storage_path, "/scontrini")
        self.assertEqual(settings.timezone, "Europe/Rome")
        self.assertFalse(settings.dry_run)
        self.assertIsNone(settings.gemini_api_key)
        self.assertIsNone(settings.gemini_model)
        self.assertEqual(settings.ai_timeout_seconds, 600)
        self.assertIsNone(settings.anthropic_api_key)
        self.assertIsNone(settings.bilancio_url)
        self.assertIsNone(settings.playwright_browser_channel)

    def test_loads_custom_data_path(self) -> None:
        self.environ["DATA_PATH"] = "C:\\project\\local-data"

        self.assertEqual(
            Settings.from_env(self.environ).data_path,
            "C:\\project\\local-data",
        )

    def test_rejects_missing_bot_token(self) -> None:
        self.environ["BOT_TOKEN"] = ""

        with self.assertRaisesRegex(ConfigurationError, "BOT_TOKEN"):
            Settings.from_env(self.environ)

    def test_rejects_invalid_user_id(self) -> None:
        self.environ["ALLOWED_USER_IDS"] = "12345,abc"

        with self.assertRaisesRegex(ConfigurationError, "ALLOWED_USER_IDS"):
            Settings.from_env(self.environ)

    def test_rejects_admin_not_in_allowed_users(self) -> None:
        self.environ["ADMIN_USER_ID"] = "99999"

        with self.assertRaisesRegex(ConfigurationError, "ADMIN_USER_ID"):
            Settings.from_env(self.environ)

    def test_rejects_empty_entry_in_user_id_list(self) -> None:
        self.environ["ALLOWED_USER_IDS"] = "12345,"

        with self.assertRaisesRegex(ConfigurationError, "ALLOWED_USER_IDS"):
            Settings.from_env(self.environ)

    def test_parses_dry_run_false(self) -> None:
        self.environ["DRY_RUN"] = "false"

        self.assertFalse(Settings.from_env(self.environ).dry_run)

    def test_trial_requires_explicit_configuration(self) -> None:
        self.environ["DRY_RUN"] = "true"
        self.assertTrue(Settings.from_env(self.environ).dry_run)

    def test_rejects_invalid_boolean(self) -> None:
        self.environ["DRY_RUN"] = "sometimes"

        with self.assertRaisesRegex(ConfigurationError, "DRY_RUN"):
            Settings.from_env(self.environ)

    def test_requires_anthropic_key_and_model_together(self) -> None:
        self.environ["ANTHROPIC_API_KEY"] = "test-key"

        with self.assertRaisesRegex(ConfigurationError, "ANTHROPIC_API_KEY"):
            Settings.from_env(self.environ)

    def test_requires_bilancio_settings_together(self) -> None:
        self.environ["BILANCIO_URL"] = "https://example.invalid"

        with self.assertRaisesRegex(ConfigurationError, "BILANCIO_URL"):
            Settings.from_env(self.environ)

    def test_accepts_gemini_key_and_model(self) -> None:
        self.environ["GEMINI_API_KEY"] = "test-key"
        self.environ["GEMINI_MODEL"] = "gemini-1.5-flash"

        settings = Settings.from_env(self.environ)

        self.assertEqual(settings.gemini_api_key, "test-key")
        self.assertEqual(settings.gemini_model, "gemini-1.5-flash")

    def test_loads_ai_timeout_and_rejects_invalid_values(self) -> None:
        self.environ["GEMINI_TIMEOUT_SECONDS"] = "180"
        self.assertEqual(Settings.from_env(self.environ).ai_timeout_seconds, 180)
        self.environ["GEMINI_TIMEOUT_SECONDS"] = "600"
        self.assertEqual(
            Settings.from_env(self.environ).ai_timeout_seconds, 600
        )
        for value in ("0", "-1", "abc", "1.5"):
            with self.subTest(value=value):
                self.environ["GEMINI_TIMEOUT_SECONDS"] = value
                with self.assertRaisesRegex(ConfigurationError, "GEMINI_TIMEOUT_SECONDS"):
                    Settings.from_env(self.environ)

    def test_defaults_model_when_key_is_set(self) -> None:
        self.environ["GEMINI_API_KEY"] = "test-key"
        settings = Settings.from_env(self.environ)
        self.assertEqual(settings.gemini_model, "gemini-1.5-flash")

    def test_rejects_model_without_key(self) -> None:
        self.environ["GEMINI_MODEL"] = "gemini-1.5-flash"

        with self.assertRaisesRegex(ConfigurationError, "GEMINI_MODEL"):
            Settings.from_env(self.environ)

    def test_accepts_installed_browser_channel_for_local_testing(self) -> None:
        self.environ["PLAYWRIGHT_BROWSER_CHANNEL"] = "chrome"

        self.assertEqual(
            Settings.from_env(self.environ).playwright_browser_channel,
            "chrome",
        )

    def test_rejects_unsupported_browser_channel(self) -> None:
        self.environ["PLAYWRIGHT_BROWSER_CHANNEL"] = "chromium"

        with self.assertRaisesRegex(
            ConfigurationError,
            "PLAYWRIGHT_BROWSER_CHANNEL",
        ):
            Settings.from_env(self.environ)

    def test_secret_values_are_not_in_settings_repr(self) -> None:
        self.environ.update(
            {
                "BOT_TOKEN": "secret-bot-token",
                "ANTHROPIC_API_KEY": "secret-api-key",
                "ANTHROPIC_MODEL": "test-model",
                "BILANCIO_URL": "https://example.invalid",
                "BILANCIO_USER": "secret-user",
                "BILANCIO_PASSWORD": "secret-password",
            }
        )

        settings_repr = repr(Settings.from_env(self.environ))

        self.assertNotIn("secret-bot-token", settings_repr)
        self.assertNotIn("secret-api-key", settings_repr)
        self.assertNotIn("secret-user", settings_repr)
        self.assertNotIn("secret-password", settings_repr)


if __name__ == "__main__":
    unittest.main()
