from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            },
            ensure_ascii=False,
        )


def configure_logging(directory: str | Path) -> None:
    log_directory = Path(directory)
    log_directory.mkdir(parents=True, exist_ok=True)
    formatter = JsonLogFormatter()
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        log_directory / "bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(console)
    root_logger.addHandler(file_handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
