"""Bounded local event relay for independently launched conversation engines."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from zhuangfy.paths import Paths


class ExternalEvents:
    def __init__(self, path: Path | None = None):
        self.path = path or Paths.current().project_data_root / "external_events.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")

    def append(self, event: dict) -> None:
        with closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.execute("INSERT INTO events(payload) VALUES (?)",
                       (json.dumps(event, ensure_ascii=False),))
            db.execute("DELETE FROM events WHERE id <= "
                       "COALESCE((SELECT MAX(id) - 5000 FROM events), 0)")

    def since(self, cursor: int, limit: int = 300) -> list[tuple[int, dict]]:
        with closing(sqlite3.connect(self.path, timeout=2)) as db:
            rows = db.execute("SELECT id,payload FROM events WHERE id>? ORDER BY id LIMIT ?",
                              (cursor, limit)).fetchall()
        result = []
        for event_id, payload in rows:
            try:
                event = json.loads(payload)
                if isinstance(event, dict):
                    result.append((event_id, event))
            except (TypeError, ValueError):
                continue
        return result
