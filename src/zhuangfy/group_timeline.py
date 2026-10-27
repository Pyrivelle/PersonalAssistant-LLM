"""Durable, group-scoped public chat history for opt-in QQ groups.

This is not a personal profile or Memory Core Timeline. Group messages retain
their QQ sender provenance and never grant access to a sender's private data.
"""
from __future__ import annotations

import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path


class GroupTimeline:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS group_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
                message_id TEXT NOT NULL, sender_id INTEGER NOT NULL,
                nickname TEXT NOT NULL, content TEXT NOT NULL,
                image_count INTEGER NOT NULL DEFAULT 0,
                received_at REAL NOT NULL,
                memory_state TEXT NOT NULL DEFAULT 'pending',
                UNIQUE(bot_id,group_id,message_id))""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(group_messages)")}
            if "memory_state" not in columns:
                db.execute("ALTER TABLE group_messages ADD COLUMN memory_state "
                           "TEXT NOT NULL DEFAULT 'pending'")
            db.execute("CREATE INDEX IF NOT EXISTS group_messages_recent "
                       "ON group_messages(bot_id,group_id,id DESC)")
            db.execute("""CREATE TABLE IF NOT EXISTS group_members (
                bot_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
                sender_id INTEGER NOT NULL, nickname TEXT NOT NULL,
                first_seen_at REAL NOT NULL, last_seen_at REAL NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(bot_id,group_id,sender_id))""")
            db.execute("""INSERT OR IGNORE INTO group_members
                (bot_id,group_id,sender_id,nickname,first_seen_at,last_seen_at,message_count)
                SELECT m.bot_id,m.group_id,m.sender_id,
                       (SELECT latest.nickname FROM group_messages latest
                        WHERE latest.bot_id=m.bot_id AND latest.group_id=m.group_id
                          AND latest.sender_id=m.sender_id
                        ORDER BY latest.id DESC LIMIT 1),
                       MIN(m.received_at),MAX(m.received_at),COUNT(*)
                FROM group_messages m WHERE m.sender_id<>m.bot_id
                GROUP BY m.bot_id,m.group_id,m.sender_id""")

    def record(self, *, bot_id: int, group_id: int, message_id: str,
               sender_id: int, nickname: str, content: str,
               image_count: int = 0, received_at: float | None = None,
               capture_member: bool = True) -> bool:
        if (bot_id <= 0 or group_id <= 0 or sender_id <= 0 or not message_id
                or not isinstance(content, str) or not content.strip()
                or not 0 <= image_count <= 20):
            raise ValueError("Invalid group message")
        safe_name = "".join(ch for ch in nickname if ch.isprintable())[:80]
        safe_content = content.strip()
        at = time.time() if received_at is None else received_at
        with closing(sqlite3.connect(self.path)) as db, db:
            result = db.execute("""INSERT OR IGNORE INTO group_messages
                (bot_id,group_id,message_id,sender_id,nickname,content,image_count,
                 received_at,memory_state)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (bot_id, group_id, message_id, sender_id, safe_name,
                 safe_content, image_count, at,
                 "pending" if capture_member else "skipped"))
            if result.rowcount and capture_member:
                db.execute("""INSERT INTO group_members
                    (bot_id,group_id,sender_id,nickname,first_seen_at,last_seen_at,message_count)
                    VALUES(?,?,?,?,?,?,1)
                    ON CONFLICT(bot_id,group_id,sender_id) DO UPDATE SET
                    nickname=excluded.nickname,last_seen_at=excluded.last_seen_at,
                    message_count=group_members.message_count+1""",
                    (bot_id, group_id, sender_id, safe_name, at, at))
            return result.rowcount == 1

    def pending_capture(self, *, bot_id: int, group_ids: frozenset[int],
                        limit: int = 100) -> list[dict]:
        if bot_id <= 0 or not group_ids or not 1 <= limit <= 100:
            return []
        groups = sorted(group_ids)
        marks = ",".join("?" for _ in groups)
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(f"""SELECT group_id,message_id,sender_id,nickname,content
                FROM group_messages WHERE bot_id=? AND group_id IN ({marks})
                  AND memory_state='pending' ORDER BY id LIMIT ?""",
                (bot_id, *groups, limit)).fetchall()
        fields = ("group_id", "message_id", "sender_id", "nickname", "content")
        return [dict(zip(fields, row)) for row in rows]

    def mark_capture(self, *, bot_id: int, group_id: int,
                     message_id: str, state: str) -> None:
        if state not in {"captured", "skipped"}:
            raise ValueError("Invalid group capture state")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""UPDATE group_messages SET memory_state=?
                WHERE bot_id=? AND group_id=? AND message_id=?
                  AND memory_state='pending'""",
                (state, bot_id, group_id, message_id))

    def members(self, *, bot_id: int, group_id: int) -> list[dict]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("""SELECT sender_id,nickname,first_seen_at,
                last_seen_at,message_count FROM group_members
                WHERE bot_id=? AND group_id=? ORDER BY first_seen_at,sender_id""",
                (bot_id, group_id)).fetchall()
        fields = ("sender_id", "nickname", "first_seen_at", "last_seen_at", "message_count")
        return [dict(zip(fields, row)) for row in rows]

    def recent(self, *, bot_id: int, group_id: int, limit: int = 60,
               before_message_id: str = "") -> list[dict]:
        if bot_id <= 0 or group_id <= 0 or not 1 <= limit <= 60:
            raise ValueError("Invalid group history request")
        with closing(sqlite3.connect(self.path)) as db:
            before = None
            if before_message_id:
                row = db.execute("SELECT id FROM group_messages WHERE bot_id=? "
                                 "AND group_id=? AND message_id=?",
                                 (bot_id, group_id, before_message_id)).fetchone()
                before = row[0] if row else None
            rows = db.execute("""SELECT message_id,sender_id,nickname,content,image_count
                FROM group_messages WHERE bot_id=? AND group_id=?
                AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?""",
                (bot_id, group_id, before, before, limit)).fetchall()
        return [dict(zip(("message_id", "sender_id", "nickname", "content",
                          "image_count"), row)) for row in reversed(rows)]

    def context(self, *, bot_id: int, group_id: int, limit: int = 60,
                before_message_id: str = "") -> str:
        rows = self.recent(bot_id=bot_id, group_id=group_id, limit=limit,
                           before_message_id=before_message_id)
        lines = []
        for row in rows:
            nickname = row["nickname"].replace("[", "［").replace("]", "］")
            speaker = (f"{nickname}（QQ {row['sender_id']}）" if nickname
                       else f"QQ {row['sender_id']}")
            content = re.sub(r"[\r\n]+", " ↵ ", row["content"][:200])
            if row["image_count"] and "[图片]" not in content:
                content += f" [图片 {row['image_count']} 张]"
            lines.append(f"{speaker}：{content}")
        return "\n".join(lines)[:18000]

    def erase_group(self, *, bot_id: int, group_id: int) -> int:
        with closing(sqlite3.connect(self.path)) as db, db:
            result = db.execute("DELETE FROM group_messages WHERE bot_id=? AND group_id=?",
                                (bot_id, group_id))
            db.execute("DELETE FROM group_members WHERE bot_id=? AND group_id=?",
                       (bot_id, group_id))
            return result.rowcount
