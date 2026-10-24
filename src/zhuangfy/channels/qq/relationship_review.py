"""Durable, per-QQ-account daily budget for optional relationship review."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path


class DailyRelationshipReview:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS review_days (
                    bot_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    local_day TEXT NOT NULL,
                    PRIMARY KEY (bot_id, user_id, local_day)
                )
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def claim(self, bot_id: int, user_id: int, *, day: date | None = None) -> bool:
        """Reserve at most one model review per local calendar day, including failures."""
        if bot_id <= 0 or user_id <= 0:
            raise ValueError("QQ account IDs must be positive")
        local_day = (day or datetime.now().astimezone().date()).isoformat()
        with self._connect() as db:
            result = db.execute(
                "INSERT OR IGNORE INTO review_days VALUES (?, ?, ?)",
                (bot_id, user_id, local_day),
            )
            return result.rowcount == 1

    def erase(self, bot_id: int, user_id: int) -> None:
        """Remove scheduling metadata when the user erases their records."""
        with self._connect() as db:
            db.execute("DELETE FROM review_days WHERE bot_id=? AND user_id=?",
                       (bot_id, user_id))
