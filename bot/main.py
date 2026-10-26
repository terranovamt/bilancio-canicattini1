from __future__ import annotations

from pathlib import Path

from bot.application import create_application
from config import Settings
from db.database import Database
from logging_config import configure_logging
from bilancio.playwright_client import PlaywrightBilancioClient
from vision.ollama_client import OllamaReceiptAnalyzer


def main() -> None:
    settings = Settings.from_env()
    configure_logging("/data/logs")
    database = Database("/data/bot.sqlite3")
    database.initialize()
    analyzer = (
        OllamaReceiptAnalyzer(
            base_url=settings.local_vision_url,
            model=settings.local_vision_model,
        )
        if settings.local_vision_model
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
            selectors_path=(
                Path(__file__).resolve().parents[1]
                / "bilancio"
                / "selectors.yaml"
            ),
            session_state_path="/data/bilancio-session.json",
            debug_path="/data/debug",
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
