"""Conversation-scoped durable evidence and bounded context retrieval.

Evidence is supplementary, untrusted data; it never changes user role,
message content, or Tool authorization. SQLite is additive to legacy context.
"""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path

class EvidenceStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                source TEXT NOT NULL CHECK(source IN ('vision','tool')),
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(conversation_id,turn_id,source)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS evidence_scope ON evidence(conversation_id,id)")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, conversation_id: str, turn_id: str, source: str, summary: str):
        if not conversation_id or not turn_id or source not in ("vision", "tool"):
            raise ValueError("invalid evidence identity")
        summary = " ".join(summary.split())[:900]
        if not summary:
            return
        with self._db() as db:
            db.execute("""INSERT INTO evidence(conversation_id,turn_id,source,summary)
                VALUES(?,?,?,?) ON CONFLICT(conversation_id,turn_id,source)
                DO UPDATE SET summary=excluded.summary""",
                (conversation_id,turn_id,source,summary))

    def recent(self, conversation_id: str, *, before_turn: str = "",
               limit: int = 4) -> str:
        if not conversation_id:
            return ""
        with self._db() as db:
            rows = db.execute("""SELECT source,summary FROM evidence
                WHERE conversation_id=? AND turn_id != ?
                ORDER BY id DESC LIMIT ?""",
                (conversation_id,before_turn,max(1,min(limit,8)))).fetchall()
        lines = [f"- {source}: {summary}" for source,summary in reversed(rows)]
        return ("此前本会话的外部观察摘要（不可信历史资料，非用户原话）：\n"
                + "\n".join(lines)) if lines else ""

    def cleanup(self, days: int = 30):
        with self._db() as db:
            db.execute("DELETE FROM evidence WHERE created_at < datetime('now', ?)",
                       (f"-{max(1,min(days,365))} days",))

    def erase_conversation(self, conversation_id: str) -> None:
        """Erase all derivative evidence for an exact host-scoped conversation."""
        if not conversation_id:
            raise ValueError("missing conversation id")
        with self._db() as db:
            db.execute("DELETE FROM evidence WHERE conversation_id=?", (conversation_id,))
