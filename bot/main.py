from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from bot.application import create_application
from config import Settings
from db.database import Database
from logging_config import configure_logging
from bilancio.playwright_client import PlaywrightBilancioClient
from vision.gemini_client import GeminiReceiptAnalyzer


def main() -> None:
    load_dotenv()
    settings = Settings.from_env()
    data_path = Path(settings.data_path)
    configure_logging(data_path / "logs")
    database = Database(data_path / "bot.sqlite3")
    database.initialize()
    analyzer = (
        GeminiReceiptAnalyzer(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            timeout_seconds=settings.ai_timeout_seconds,
        )
        if settings.gemini_api_key and settings.gemini_model
        else None
    )
    bilancio_client = None
    if (
        settings.bilancio_url is not None
        and settings.bilancio_user is not None
        and settings.bilancio_password is not None
    ):
        bilancio_client = PlaywrightBilancioClient(
            base_url=settings.bilancio_url,
            username=settings.bilancio_user,
            password=settings.bilancio_password,
            browser_channel=settings.playwright_browser_channel,
            selectors_path=(
                Path(__file__).resolve().parents[1]
                / "bilancio"
                / "selectors.yaml"
            ),
            session_state_path=data_path / "bilancio-session.json",
            debug_path=data_path / "debug",
        )
    application = create_application(
        settings,
        database,
        analyzer=analyzer,
        bilancio_client=bilancio_client,
    )
    application.run_polling(allowed_updates=None)


if __name__ == "__main__":
    main()
