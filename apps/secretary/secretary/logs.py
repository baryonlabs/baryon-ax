"""Log lines that can be read back after the fact: every line carries its time (ISO 8601 with the
time zone), and what people said stays out of the log unless the operator turns it on.

    SECRETARY_LOG_TEXT=1   also log turn text, confirmation prompts and tool input (off by default:
                           they hold mail bodies, names and addresses)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime

FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class IsoFormatter(logging.Formatter):
    """asctime as 2026-11-28T21:04:05.123+09:00."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802 - logging API
        return datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds")


def setup(level: str | int = "INFO") -> None:
    """Root and uvicorn handlers all write ISO-timestamped lines (uvicorn sets up its own before the app loads)."""
    formatter = IsoFormatter(FORMAT)
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    root.setLevel(level)
    for name in ("",) + UVICORN_LOGGERS:
        for handler in logging.getLogger(name).handlers:
            handler.setFormatter(formatter)


def log_text() -> bool:
    return os.getenv("SECRETARY_LOG_TEXT", "").strip().lower() in {"1", "true", "yes", "on"}


def shown(text: str, limit: int = 300) -> str:
    """What was said, for a log line: hidden unless SECRETARY_LOG_TEXT is on."""
    return json.dumps(text[:limit], ensure_ascii=False) if log_text() else "(hidden)"
