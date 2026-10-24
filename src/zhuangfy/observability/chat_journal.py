"""Local, bounded chat-stream history separate from this launch's console log."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path


from zhuangfy.paths import Paths

DEFAULT_PATH = Paths.current().project_data_root / "chat_stream.sqlite3"
MAX_EVENTS = 5000
_VISIBLE = {"消息", "思考V2", "思考", "搜索结果", "等待V2", "静默V2",
            "回复", "语音回复", "失败", "模型全部失败", "识图"}


def is_chat_event(event: dict) -> bool:
    if not event.get("turn_id") or event.get("channel") not in {"QQ", "文字", "语音"}:
        return False
    source, phase = event.get("source"), event.get("phase")
    return ((source == event["channel"] and phase in _VISIBLE)
            or (source == "模型" and phase in {"推理内容", "规划结果"}))


class ChatJournal:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")

    def append(self, event: dict) -> None:
        if not is_chat_event(event):
            return
        with closing(sqlite3.connect(self.path)) as db:
            with db:
                db.execute("INSERT INTO events(payload) VALUES (?)",
                           (json.dumps(event, ensure_ascii=False),))
                db.execute("DELETE FROM events WHERE id <= "
                           "COALESCE((SELECT MAX(id) - ? FROM events), 0)", (MAX_EVENTS,))

    def recent(self) -> list[dict]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT payload FROM events ORDER BY id DESC LIMIT ?",
                              (MAX_EVENTS,)).fetchall()
        result = []
        for (payload,) in reversed(rows):
            try:
                event = json.loads(payload)
                if isinstance(event, dict) and is_chat_event(event):
                    result.append(event)
            except (ValueError, TypeError):
                continue
        return result
