"""SQLite: authoritative storage for manually confirmed private memories.

Not a chat transcript store, identity resolver or an LLM extractor. The vector
index is derived from active rows and can be rebuilt independently.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class MemoryEntry:
    memory_id: str
    agent_id: str
    person_id: str
    content: str
    kind: str
    source_turn_ids: tuple[str, ...]
    created_at: int
    indexed_model: str | None = None


class SQLiteMemoryRepository:
    def __init__(self, database: Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS memory_items (
                    memory_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    person_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source_turn_ids TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active', 'deleted')),
                    indexed_model TEXT
                )
            """)
            db.execute("""CREATE INDEX IF NOT EXISTS idx_memory_items_scope
                         ON memory_items(agent_id, person_id, status, created_at)""")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout = 10000")
            db.execute("PRAGMA journal_mode = WAL")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _entry(row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            memory_id=row["memory_id"], agent_id=row["agent_id"],
            person_id=row["person_id"], content=row["content"], kind=row["kind"],
            source_turn_ids=tuple(json.loads(row["source_turn_ids"])),
            created_at=row["created_at"], indexed_model=row["indexed_model"],
        )

    def add(self, memory_id: str, agent_id: str, person_id: str, content: str,
            kind: str, source_turn_ids: tuple[str, ...] = ()) -> MemoryEntry:
        if not all((memory_id, agent_id.strip(), person_id.strip(), content.strip(), kind.strip())):
            raise ValueError("memory_id, agent_id, person_id, content and kind are required")
        timestamp = int(time.time())
        with self._connect() as db:
            db.execute("""INSERT INTO memory_items
                (memory_id,agent_id,person_id,content,kind,source_turn_ids,created_at)
                VALUES(?,?,?,?,?,?,?)""", (memory_id, agent_id, person_id, content.strip(), kind,
                                         json.dumps(source_turn_ids, ensure_ascii=False), timestamp))
        return MemoryEntry(memory_id, agent_id, person_id, content.strip(), kind,
                           source_turn_ids, timestamp)

    def get(self, memory_id: str, agent_id: str, person_id: str) -> MemoryEntry | None:
        with self._connect() as db:
            row = db.execute("""SELECT * FROM memory_items WHERE memory_id=? AND agent_id=?
                AND person_id=? AND status='active'""", (memory_id, agent_id, person_id)).fetchone()
            return self._entry(row) if row else None

    def list_unindexed(self, model_id: str, limit: int = 100) -> list[MemoryEntry]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as db:
            rows = db.execute("""SELECT * FROM memory_items WHERE status='active'
                AND (indexed_model IS NULL OR indexed_model != ?)
                ORDER BY created_at, memory_id LIMIT ?""", (model_id, limit)).fetchall()
            return [self._entry(row) for row in rows]

    def mark_indexed(self, memory_id: str, model_id: str) -> None:
        with self._connect() as db:
            db.execute("""UPDATE memory_items SET indexed_model=?
                WHERE memory_id=? AND status='active'""", (model_id, memory_id))

    def mark_deleted(self, memory_id: str, agent_id: str, person_id: str) -> bool:
        """Scrub private text first; leave a UUID tombstone for index repair."""
        with self._connect() as db:
            cursor = db.execute("""UPDATE memory_items SET status='deleted',
                content='', source_turn_ids='[]', indexed_model=NULL
                WHERE memory_id=? AND agent_id=? AND person_id=? AND status='active'""",
                (memory_id, agent_id, person_id))
            return cursor.rowcount == 1

    def deleted_ids(self, limit: int = 1000) -> list[str]:
        with self._connect() as db:
            rows = db.execute("SELECT memory_id FROM memory_items WHERE status='deleted' LIMIT ?", (limit,)).fetchall()
            return [row[0] for row in rows]

    def purge_tombstone(self, memory_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM memory_items WHERE memory_id=? AND status='deleted'", (memory_id,))
