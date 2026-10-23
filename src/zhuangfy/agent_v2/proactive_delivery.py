"""Opt-in, rate-limited delivery of B2 suggestions and daily QQ check-ins."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

from .initiative_policy import PresencePolicy, PresenceSnapshot, timing_gate


@dataclass(frozen=True)
class ProactiveConfig:
    enabled: bool = False
    channel: str = "qq"
    recipient_id: int = 0
    daily_checkin: bool = False
    quiet_start: int = 22
    quiet_end: int = 8
    min_idle_minutes: int = 30
    min_gap_hours: int = 8
    daily_limit: int = 1

    @classmethod
    def load(cls, path: Path) -> "ProactiveConfig":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("主动互动配置必须是 JSON 对象")
        config = cls(**{key: data.get(key, getattr(cls(), key)) for key in cls.__dataclass_fields__})
        config.validate()
        return config

    def validate(self) -> None:
        if type(self.enabled) is not bool or type(self.daily_checkin) is not bool:
            raise ValueError("主动互动开关必须是布尔值")
        if self.channel != "qq":
            raise ValueError("当前仅支持 QQ 主动消息通道")
        if type(self.recipient_id) is not int or self.recipient_id < 0:
            raise ValueError("主动互动收件人必须是 QQ 号码")
        PresencePolicy(
            f"qq:private:{self.recipient_id}", enabled=self.enabled,
            quiet_start=self.quiet_start, quiet_end=self.quiet_end,
            min_human_idle_seconds=self.min_idle_minutes * 60,
            min_outbound_gap_seconds=self.min_gap_hours * 3600,
            daily_limit=self.daily_limit,
        )

    def policy(self) -> PresencePolicy:
        self.validate()
        return PresencePolicy(
            f"qq:private:{self.recipient_id}", enabled=self.enabled,
            quiet_start=self.quiet_start, quiet_end=self.quiet_end,
            min_human_idle_seconds=self.min_idle_minutes * 60,
            min_outbound_gap_seconds=self.min_gap_hours * 3600,
            daily_limit=self.daily_limit,
        )


class ProactiveStore:
    """Durable timing and at-most-once send reservations; uncertain sends stay reserved."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS presence (
                recipient_id INTEGER PRIMARY KEY, last_human REAL NOT NULL,
                last_outbound REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS proactive_sends (
                source_id TEXT PRIMARY KEY, recipient_id INTEGER NOT NULL,
                reserved_at REAL NOT NULL, status TEXT NOT NULL,
                message TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS proactive_considerations (
                source_id TEXT PRIMARY KEY, considered_at REAL NOT NULL)""")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=8)
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self, recipient_id: int, now: float) -> None:
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO presence VALUES (?,?,?)", (recipient_id, now, now))

    def human_message(self, recipient_id: int, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.initialize(recipient_id, now)
        with self._db() as db:
            db.execute("UPDATE presence SET last_human=? WHERE recipient_id=?",
                       (now, recipient_id))

    def outbound_message(self, recipient_id: int, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.initialize(recipient_id, now)
        with self._db() as db:
            db.execute("UPDATE presence SET last_outbound=? WHERE recipient_id=?",
                       (now, recipient_id))

    def snapshot(self, recipient_id: int, now: float, *, voice_active: bool = False,
                 active_chat_turn: bool = False) -> PresenceSnapshot:
        self.initialize(recipient_id, now)
        with self._db() as db:
            human, outbound = db.execute(
                "SELECT last_human,last_outbound FROM presence WHERE recipient_id=?",
                (recipient_id,)).fetchone()
            sent = db.execute(
                "SELECT COUNT(*) FROM proactive_sends WHERE recipient_id=? AND reserved_at>?",
                (recipient_id, now - 86400)).fetchone()[0]
        return PresenceSnapshot(
            local_hour=datetime.fromtimestamp(now).hour,
            seconds_since_human_message=max(0, now - human),
            seconds_since_last_outbound=max(0, now - outbound),
            sent_last_24h=sent, voice_active=voice_active,
            active_chat_turn=active_chat_turn,
        )

    def reserve(self, source_id: str, recipient_id: int, message: str, now: float,
                policy: PresencePolicy, *, voice_active: bool = False,
                active_chat_turn: bool = False) -> bool:
        if not source_id or not message or len(message) > 220:
            return False
        self.initialize(recipient_id, now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            human, outbound = db.execute(
                "SELECT last_human,last_outbound FROM presence WHERE recipient_id=?",
                (recipient_id,)).fetchone()
            sent = db.execute(
                "SELECT COUNT(*) FROM proactive_sends WHERE recipient_id=? AND reserved_at>?",
                (recipient_id, now - 86400)).fetchone()[0]
            snapshot = PresenceSnapshot(datetime.fromtimestamp(now).hour,
                max(0, now-human), max(0, now-outbound), sent,
                voice_active=voice_active, active_chat_turn=active_chat_turn)
            if not timing_gate(policy, snapshot).eligible:
                return False
            result = db.execute(
                "INSERT OR IGNORE INTO proactive_sends VALUES (?,?,?,?,?)",
                (source_id, recipient_id, now, "reserved", message))
            if result.rowcount:
                db.execute("UPDATE presence SET last_outbound=? WHERE recipient_id=?",
                           (now, recipient_id))
            return result.rowcount == 1

    def mark_sent(self, source_id: str) -> None:
        with self._db() as db:
            db.execute("UPDATE proactive_sends SET status='sent' WHERE source_id=? AND status='reserved'",
                       (source_id,))

    def already_reserved(self, source_id: str) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM proactive_sends WHERE source_id=?",
                              (source_id,)).fetchone() is not None

    def already_considered(self, source_id: str) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM proactive_considerations WHERE source_id=?",
                              (source_id,)).fetchone() is not None

    def mark_considered(self, source_id: str, now: float) -> None:
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO proactive_considerations VALUES (?,?)",
                       (source_id, now))
