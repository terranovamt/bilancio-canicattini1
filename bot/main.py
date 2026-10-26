from __future__ import annotations

from bot.application import create_application
from config import Settings
from db.database import Database
from logging_config import configure_logging
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
    application = create_application(
        settings,
        database,
        analyzer=analyzer,
    )
    application.run_polling(allowed_updates=None)


if __name__ == "__main__":
    main()
