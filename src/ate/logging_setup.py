"""Per-run logging: a detailed run.log file, and short messages on the terminal."""

from __future__ import annotations

import logging
from pathlib import Path

LOGGER_NAME = "ate"


def start_run_logging(log_path: Path, console: bool) -> list[logging.Handler]:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    handlers: list[logging.Handler] = []

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handlers.append(file_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter("%(message)s"))
        handlers.append(console_handler)

    for handler in handlers:
        logger.addHandler(handler)
    return handlers


def stop_run_logging(handlers: list[logging.Handler]) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    for handler in handlers:
        logger.removeHandler(handler)
        handler.close()
