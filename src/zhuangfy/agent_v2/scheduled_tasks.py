"""Host-owned, durable one-shot QQ timers. A timer is never a Planner wait."""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

MAX_DELAY_SECONDS = 7 * 24 * 3600
_AMOUNTS = {"半": 0.5, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_DURATION = re.compile(
    r"(?P<amount>\d{1,5}|半|[一二两三四五六七八九十])\s*"
    r"(?P<unit>天|小时|钟头|分钟|分|秒钟|秒)\s*后\s*"
    r"(?:请|麻烦你)?\s*(?:提醒我|叫我|喊我)\s*(?P<topic>[^\n]{1,220})"
)


@dataclass(frozen=True)
class ScheduledIntent:
    kind: str  # reminder | deferred_reply
    delay_seconds: int
    content: str


@dataclass(frozen=True)
class ScheduledTask:
    id: str
    bot_id: int
    user_id: int
    source_message_id: str
    source_turn_id: str
    kind: str
    content: str
    due_at: float
    status: str
    context_json: str = "[]"
    generation_failures: int = 0

    def creation_context(self) -> list[dict[str, str]]:
        try:
            return bounded_context(json.loads(self.context_json))
        except (TypeError, ValueError):
            return []


def bounded_context(value: object) -> list[dict[str, str]]:
    """Keep only a small, explicitly scoped chat excerpt in the timer DB."""
    if not isinstance(value, list):
        return []
    result = []
    for entry in value[-8:]:
        if (isinstance(entry, dict) and entry.get("role") in {"user", "assistant"}
                and isinstance(entry.get("content"), str)):
            content = entry["content"].strip()[:500]
            if content:
                result.append({"role": entry["role"], "content": content})
    while len(json.dumps(result, ensure_ascii=False)) > 3000:
        result.pop(0)
    return result


def parse_scheduled_intent(text: str) -> ScheduledIntent | None:
    """Only clear, relative, private-chat requests are eligible for a timer."""
    if not isinstance(text, str) or not 1 <= len(text) <= 500:
        return None
    match = _DURATION.search(text)
    if match:
        prefix = text[:match.start()]
        if re.search(r"(?:^|[，。！？\s])我(?:会|准备|打算|要)?\s*$", prefix):
            return None
        raw = match.group("amount")
        amount = float(raw) if raw.isascii() and raw.isdecimal() else _AMOUNTS[raw]
        factor = {"天": 86400, "小时": 3600, "钟头": 3600,
                  "分钟": 60, "分": 60, "秒钟": 1, "秒": 1}[match.group("unit")]
        delay = int(amount * factor)
        topic = match.group("topic").strip(" ，。！？!?.；;：:")
        if 1 <= delay <= MAX_DELAY_SECONDS and 1 <= len(topic) <= 200:
            return ScheduledIntent("reminder", delay, topic)
        return None
    from .reply_delay import requested_reply_delay
    delay = requested_reply_delay(text)
    if delay is not None:
        return ScheduledIntent("deferred_reply", delay, text.strip())
    return None


class ScheduledTaskStore:
    """Claim-before-send avoids duplicate QQ messages after ambiguous ACKs."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise ValueError("Unsupported scheduled-task database version")
            db.execute("""CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id TEXT PRIMARY KEY, bot_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                source_message_id TEXT NOT NULL, source_turn_id TEXT NOT NULL,
                kind TEXT NOT NULL, content TEXT NOT NULL, due_at REAL NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL,
                completed_at REAL, error_kind TEXT NOT NULL DEFAULT '',
                UNIQUE(bot_id, user_id, source_message_id, kind))""")
            db.execute("CREATE INDEX IF NOT EXISTS scheduled_due ON scheduled_tasks(status, due_at)")
            if version < 2:
                db.execute("ALTER TABLE scheduled_tasks ADD COLUMN context_json TEXT NOT NULL DEFAULT '[]'")
                db.execute("ALTER TABLE scheduled_tasks ADD COLUMN generation_failures INTEGER NOT NULL DEFAULT 0")
                db.execute("ALTER TABLE scheduled_tasks ADD COLUMN retry_at REAL NOT NULL DEFAULT 0")
            db.execute("PRAGMA user_version=2")
            # A previous process may have sent before its ACK was recorded.
            db.execute("UPDATE scheduled_tasks SET status='uncertain', error_kind='interrupted' "
                       "WHERE status='sending'")

    @staticmethod
    def _task(row: tuple) -> ScheduledTask:
        return ScheduledTask(*row)

    def schedule(self, *, bot_id: int, user_id: int, source_message_id: str,
                 source_turn_id: str, intent: ScheduledIntent,
                 received_at: float | None = None,
                 context: list[dict[str, str]] | None = None) -> ScheduledTask:
        if (bot_id <= 0 or user_id <= 0 or not source_message_id or not source_turn_id
                or intent.kind not in {"reminder", "deferred_reply"}
                or not 1 <= intent.delay_seconds <= MAX_DELAY_SECONDS
                or not 1 <= len(intent.content) <= 500):
            raise ValueError("Invalid scheduled task")
        created = time.time() if received_at is None else received_at
        due = created + intent.delay_seconds
        context_json = json.dumps(bounded_context(context or []), ensure_ascii=False)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT OR IGNORE INTO scheduled_tasks "
                       "(id,bot_id,user_id,source_message_id,source_turn_id,kind,content,due_at,status,created_at,context_json) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (uuid.uuid4().hex[:12], bot_id, user_id, source_message_id,
                        source_turn_id, intent.kind, intent.content, due, "pending", created, context_json))
            row = db.execute("SELECT id,bot_id,user_id,source_message_id,source_turn_id,"
                             "kind,content,due_at,status,context_json,generation_failures "
                             "FROM scheduled_tasks WHERE "
                             "bot_id=? AND user_id=? AND source_message_id=? AND kind=?",
                             (bot_id, user_id, source_message_id, intent.kind)).fetchone()
        return self._task(row)

    def due(self, bot_id: int, now: float, limit: int = 8) -> list[ScheduledTask]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT id,bot_id,user_id,source_message_id,source_turn_id,"
                              "kind,content,due_at,status,context_json,generation_failures "
                              "FROM scheduled_tasks WHERE bot_id=? AND status='pending' "
                              "AND due_at<=? AND retry_at<=? "
                              "ORDER BY due_at LIMIT ?", (bot_id, now, now, limit)).fetchall()
        return [self._task(row) for row in rows]

    def claim(self, task_id: str) -> bool:
        with closing(sqlite3.connect(self.path)) as db, db:
            result = db.execute("UPDATE scheduled_tasks SET status='sending' "
                                "WHERE id=? AND status='pending'", (task_id,))
            return result.rowcount == 1

    def finish(self, task_id: str, *, delivered: bool, error_kind: str = "") -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE scheduled_tasks SET status=?, completed_at=?, error_kind=? "
                       "WHERE id=? AND status='sending'",
                       ("delivered" if delivered else "uncertain", time.time(),
                        error_kind[:80], task_id))

    def generation_failed(self, task_id: str, *, error_kind: str,
                          now: float | None = None) -> str:
        """A model failure occurs before any QQ send, so bounded retry is safe."""
        current = time.time() if now is None else now
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE scheduled_tasks SET generation_failures=generation_failures+1, "
                       "status=CASE WHEN generation_failures>=2 THEN 'failed' ELSE 'pending' END, "
                       "retry_at=?, error_kind=? WHERE id=? AND status='pending'",
                       (current + 30, error_kind[:80], task_id))
            row = db.execute("SELECT status FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
        return row[0] if row else "missing"

    def get_by_source(self, bot_id: int, user_id: int,
                      source_message_id: str, kind: str) -> ScheduledTask | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT id,bot_id,user_id,source_message_id,source_turn_id,"
                             "kind,content,due_at,status,context_json,generation_failures "
                             "FROM scheduled_tasks WHERE "
                             "bot_id=? AND user_id=? AND source_message_id=? AND kind=?",
                             (bot_id, user_id, source_message_id, kind)).fetchone()
        return self._task(row) if row else None

    def cancel_user(self, bot_id: int, user_id: int) -> int:
        """Erase unexecuted personal tasks on an explicit privacy request."""
        with closing(sqlite3.connect(self.path)) as db, db:
            result = db.execute("DELETE FROM scheduled_tasks WHERE bot_id=? AND user_id=? "
                                "AND status='pending'", (bot_id, user_id))
            return result.rowcount
