"""Bounded, local short-term conversation shared by verified owner channels.

The QQ account binding is the canonical scope for the current desktop owner.
Other QQ users and unbound desktop sessions never join this store.
"""
from __future__ import annotations

import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from zhuangfy.paths import Paths

MAX_MESSAGES = 12
RETENTION_SECONDS = 30 * 86400
_SCOPE = re.compile(r"^qq:[1-9][0-9]*:private:[1-9][0-9]*$")
_SECRET = re.compile(r"(?i)(api[_ -]?key|password|密钥|密码|验证码|access[_ -]?token|sk-[\w-]{8,})")


def owner_scope() -> str | None:
    """Resolve only the explicitly configured, validated local owner link."""
    try:
        from owner_memory import load_link, current_bot_id
        from qq_bridge import QqSettings
        link = load_link(validate=False)
        if link is None or link.qq_id not in QqSettings.load().allowed_private_ids:
            return None
        active_bot = current_bot_id()
        if active_bot is not None and active_bot != link.bot_id:
            return None
        return f"qq:{link.bot_id}:private:{link.qq_id}"
    except (OSError, ValueError, ImportError):
        return None


class RecentContextStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or Paths.current().project_data_root / "recent_context.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL, source TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL,
                event_id TEXT NOT NULL, created_at INTEGER NOT NULL,
                UNIQUE(scope, event_id)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS recent_scope ON messages(scope, id)")

    @staticmethod
    def _validate(scope: str, source: str) -> None:
        if not _SCOPE.fullmatch(scope) or source not in {"qq", "text", "voice"}:
            raise ValueError("invalid verified recent-context scope or source")

    def append(self, scope: str, source: str, role: str, content: str, event_id: str) -> None:
        self._validate(scope, source)
        if role not in {"user", "assistant"} or not event_id or len(event_id) > 128:
            raise ValueError("invalid recent-context event")
        content = content.strip()
        if not content or _SECRET.search(content):
            return
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("INSERT OR IGNORE INTO messages(scope,source,role,content,event_id,created_at) "
                       "VALUES(?,?,?,?,?,?)",
                       (scope, source, role, content[:1500], event_id, int(time.time())))
            db.execute("DELETE FROM messages WHERE scope=? AND id NOT IN "
                       "(SELECT id FROM messages WHERE scope=? ORDER BY id DESC LIMIT ?)",
                       (scope, scope, MAX_MESSAGES))
            db.execute("DELETE FROM messages WHERE created_at<?",
                       (int(time.time()) - RETENTION_SECONDS,))

    def history(self, scope: str, *, exclude_source: str | None = None) -> list[dict[str, str]]:
        if not _SCOPE.fullmatch(scope):
            raise ValueError("invalid verified recent-context scope")
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            rows = db.execute(
                "SELECT source,role,content FROM messages WHERE scope=? AND created_at>=? "
                "ORDER BY id DESC LIMIT ?", (scope, int(time.time()) - RETENTION_SECONDS,
                                               MAX_MESSAGES)).fetchall()
        return [{"source": source, "role": role, "content": content}
                for source, role, content in reversed(rows) if source != exclude_source]

    def external_excerpt(self, scope: str, *, exclude_source: str = "qq", budget: int = 1600) -> str:
        rows = self.history(scope, exclude_source=exclude_source)
        lines = [f"{'用户' if row['role'] == 'user' else '庄方宜'}（{'语音' if row['source'] == 'voice' else '文字'}）：{row['content']}"
                 for row in rows]
        result = "\n".join(lines)
        return result[-budget:]

    def delete(self, scope: str) -> None:
        if not _SCOPE.fullmatch(scope):
            raise ValueError("invalid verified recent-context scope")
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("DELETE FROM messages WHERE scope=?", (scope,))


def desktop_history(local: list[dict[str, str]]) -> list[dict[str, str]]:
    """Use the common six-turn window when the desktop owner is verified."""
    scope = owner_scope()
    if scope is None:
        return list(local)
    try:
        common = RecentContextStore().history(scope)
        return [{"role": row["role"], "content": row["content"]} for row in common] or list(local)
    except (OSError, sqlite3.Error, ValueError):
        return list(local)


def record_desktop_turn(source: str, user: str, assistant: str, event_id: str) -> None:
    scope = owner_scope()
    if scope is None:
        return
    store = RecentContextStore()
    store.append(scope, source, "user", user, event_id + ":user")
    store.append(scope, source, "assistant", assistant, event_id + ":assistant")
