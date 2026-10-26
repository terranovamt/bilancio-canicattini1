import json
import logging
import tempfile
import unittest
from pathlib import Path

from logging_config import JsonLogFormatter, configure_logging


class LoggingConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        self.clear_handlers()

    def clear_handlers(self) -> None:
        root_logger = logging.getLogger()
        for handler in tuple(root_logger.handlers):
            handler.close()
        root_logger.handlers.clear()

    def test_formatter_produces_structured_json(self) -> None:
        record = logging.LogRecord(
            name="bot.workflow",
            level=logging.WARNING,
            pathname="workflow.py",
            lineno=12,
            msg="Receipt analysis failed",
            args=(),
            exc_info=None,
        )

        formatted = json.loads(JsonLogFormatter().format(record))

        self.assertEqual(formatted["level"], "WARNING")
        self.assertEqual(formatted["logger"], "bot.workflow")
        self.assertEqual(formatted["message"], "Receipt analysis failed")

    def test_configures_rotating_file_and_console_handlers(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        try:
            configure_logging(temporary_directory.name)

            handlers = logging.getLogger().handlers
            file_handlers = [
                handler
                for handler in handlers
                if hasattr(handler, "maxBytes")
            ]

            self.assertEqual(len(file_handlers), 1)
            self.assertEqual(file_handlers[0].maxBytes, 5 * 1024 * 1024)
            self.assertTrue(
                (Path(temporary_directory.name) / "bot.log").exists()
            )
        finally:
            self.clear_handlers()
            temporary_directory.cleanup()


if __name__ == "__main__":
    unittest.main()
