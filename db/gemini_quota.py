from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class QuotaReservation:
    allowed: bool
    warning: bool = False
    retry_at: float | None = None
    remote: bool = False
    known_retry: bool = True


def day_window(now: float) -> tuple[float, float]:
    zone = ZoneInfo("America/Los_Angeles")
    day = datetime.fromtimestamp(now, zone).date()
    return (
        datetime.combine(day, time.min, zone).timestamp(),
        datetime.combine(day + timedelta(days=1), time.min, zone).timestamp(),
    )


class GeminiQuotaStore:
    """Atomic bot-only reservations and remote cooldowns on a persistent volume."""

    def __init__(self, path: Path) -> None:
        self._path = path
        with closing(sqlite3.connect(self._path)) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS gemini_requests (
                    model TEXT NOT NULL, requested_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS gemini_requests_model_time
                    ON gemini_requests(model, requested_at);
                CREATE TABLE IF NOT EXISTS gemini_cooldowns (
                    model TEXT PRIMARY KEY, until_at REAL NOT NULL, known_retry INTEGER NOT NULL
                );
            """)

    def reserve(self, model: str, rpm: int, rpd: int, warn_percent: int, now: float) -> QuotaReservation:
        start, end = day_window(now)
        with closing(sqlite3.connect(self._path, timeout=10)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM gemini_requests WHERE requested_at < ?", (start - 60,))
            cooldown = connection.execute(
                "SELECT until_at, known_retry FROM gemini_cooldowns WHERE model = ?", (model,)
            ).fetchone()
            if cooldown and cooldown[0] > now:
                return QuotaReservation(False, retry_at=cooldown[0], remote=True, known_retry=bool(cooldown[1]))
            daily, minute = connection.execute(
                """SELECT COUNT(CASE WHEN requested_at >= ? THEN 1 END),
                          COUNT(CASE WHEN requested_at > ? THEN 1 END)
                   FROM gemini_requests WHERE model = ?""",
                (start, now - 60, model),
            ).fetchone()
            waits = []
            if daily >= rpd:
                waits.append(end)
            if minute >= rpm:
                oldest = connection.execute(
                    "SELECT MIN(requested_at) FROM gemini_requests WHERE model = ? AND requested_at > ?",
                    (model, now - 60),
                ).fetchone()[0]
                waits.append(oldest + 60)
            if waits:
                return QuotaReservation(False, retry_at=max(waits))
            connection.execute(
                "INSERT INTO gemini_requests VALUES (?, ?)", (model, now)
            )
            warning = (
                minute * 100 < rpm * warn_percent <= (minute + 1) * 100
                or daily * 100 < rpd * warn_percent <= (daily + 1) * 100
            )
            return QuotaReservation(True, warning=warning)

    def record_cooldown(self, model: str, now: float, retry_at: float | None) -> None:
        # Unknown resets are not promises: stop attempts until the next bookkeeping day.
        until = retry_at if retry_at is not None else day_window(now)[1]
        with closing(sqlite3.connect(self._path)) as connection, connection:
            connection.execute(
                """INSERT INTO gemini_cooldowns VALUES (?, ?, ?)
                   ON CONFLICT(model) DO UPDATE SET
                   until_at = MAX(until_at, excluded.until_at),
                   known_retry = CASE WHEN excluded.until_at >= until_at
                                      THEN excluded.known_retry ELSE known_retry END""",
                (model, until, int(retry_at is not None)),
            )
