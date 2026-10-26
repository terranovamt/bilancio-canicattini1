from __future__ import annotations

import logging

from bot.application import create_application
from config import Settings
from db.database import Database
from vision.ollama_client import OllamaReceiptAnalyzer


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
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
