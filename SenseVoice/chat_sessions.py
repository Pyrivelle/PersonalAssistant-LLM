"""Local conversation list for the dashboard's text chat.

The chat transcript is deliberately separate from the bounded context sent to
the model. Both are local-only and can be deleted one conversation at a time.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from dialogue_context import DialogueContext
from zhuangfy.paths import Paths


class ChatSessions:
    def __init__(self, path: Path | None = None):
        self.path = path or Paths.current().conversation_root / "dashboard_chats.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    updated_at INTEGER NOT NULL, context TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL, created_at INTEGER NOT NULL,
                    image BLOB
                );
                CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, id);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
            if "image" not in columns:
                db.execute("ALTER TABLE messages ADD COLUMN image BLOB")

    def _connect(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def list_chats(self) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT id,title,updated_at FROM chats ORDER BY updated_at DESC,id DESC").fetchall()
        return [{"id": row[0], "title": row[1], "updated_at": row[2]} for row in rows]

    def create(self) -> str:
        chat_id = uuid4().hex
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO chats VALUES(?,?,?,?)",
                       (chat_id, "新对话", int(time.time()), json.dumps(DialogueContext().to_dict())))
        return chat_id

    def delete(self, chat_id: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM chats WHERE id=?", (chat_id,))

    def load(self, chat_id: str) -> tuple[str, DialogueContext, list[dict]]:
        with closing(self._connect()) as db:
            row = db.execute("SELECT title,context FROM chats WHERE id=?", (chat_id,)).fetchone()
            if row is None:
                raise KeyError(chat_id)
            messages = db.execute("SELECT role,content,image FROM messages WHERE chat_id=? ORDER BY id",
                                  (chat_id,)).fetchall()
        return row[0], DialogueContext.from_dict(json.loads(row[1])), [
            {"role": role, "content": content, "image": image}
            for role, content, image in messages]

    def append(self, chat_id: str, role: str, content: str, *, image: bytes | None = None) -> None:
        if role not in {"user", "assistant"} or not content.strip():
            raise ValueError("Invalid chat message")
        if image is not None and (role != "user" or not 0 < len(image) <= 2_500_000):
            raise ValueError("Invalid chat image")
        now = int(time.time())
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO messages(chat_id,role,content,created_at,image) VALUES(?,?,?,?,?)",
                       (chat_id, role, content, now, image))
            if role == "user":
                db.execute("UPDATE chats SET title=?,updated_at=? WHERE id=? AND title='新对话'",
                           (content.replace("\n", " ").strip()[:22] or "新对话", now, chat_id))
            db.execute("UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id))

    def save_context(self, chat_id: str, context: DialogueContext) -> None:
        with closing(self._connect()) as db, db:
            db.execute("UPDATE chats SET context=? WHERE id=?",
                       (json.dumps(context.to_dict(), ensure_ascii=False), chat_id))
