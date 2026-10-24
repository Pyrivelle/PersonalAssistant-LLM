"""Phase 3: source-bound task progress events and conservative state reduction.

The authoritative fact is what a verified user reported, not whether the
underlying real-world task is objectively complete. Tool and model output
cannot masquerade as user evidence. SQLite events are append-only; only the
review decision for a pending candidate can be changed by an administrator.
"""
from __future__ import annotations

import re
import time
from uuid import uuid4

from .database import MemoryDatabase
from .identity import ChannelIdentity, IdentityRepository
from .task_links import TaskMemoryLinks
from .task_continuity import _features, _score
from .timeline import TimelineRepository

EVENT_KINDS = frozenset({
    "started", "progress", "blocked", "reported_completed", "cancelled",
    "correction",
})
STATES = frozenset({"proposed", "active", "blocked", "reported_completed", "cancelled"})
UNCERTAIN = re.compile(
    r"可能|也许|大概|应该|似乎|如果|假如|假设|希望|打算|准备|计划|"
    r"还没|尚未|没有完成|没完成|未完成|差点|快要|快完成|"
    r"听说|据说|别人说|他说|她说|预计|将会|明天|以后"
)
CORRECTION_CUES = re.compile(r"说错了|记错了|之前说|其实没|更正|纠正|不是.*(完成|解决)")
RULES = (
    ("cancelled", re.compile(r"不做了|取消了|放弃了|不再(做|继续)|停止(研究|开发|做)")),
    ("reported_completed", re.compile(r"已经完成|终于完成|做完了|搞定了|已经解决|解决了|完成了")),
    ("blocked", re.compile(r"卡住了|遇到瓶颈|遇到困难|遇到问题|还没解决|无法继续")),
    ("started", re.compile(r"已经开始|开始做了|开始写了|正在(写|做|研究|优化|测试|实现)|动手做")),
    ("progress", re.compile(r"推进了|调整了|改进了|测试了|试了一下|又试了|做了一部分|更新了")),
)
MAX_TASKS_TO_MATCH = 120
MAX_TEXT_CHARS = 1000


def extract_report(text: str) -> tuple[str, str, bool] | None:
    """Rule-only proposal; ambiguity requires review, never invented facts."""
    if not isinstance(text, str) or len(text) > MAX_TEXT_CHARS:
        return None
    if CORRECTION_CUES.search(text):
        # Negated/corrected reports require explicit correction review, never
        # silently turn an earlier completion report into a fresh completion.
        return None
    for kind, pattern in RULES:
        if pattern.search(text):
            state = {
                "cancelled": "cancelled", "reported_completed": "reported_completed",
                "blocked": "blocked", "started": "active", "progress": "active",
            }[kind]
            cautious = bool(UNCERTAIN.search(text) or CORRECTION_CUES.search(text))
            return kind, state, cautious
    if CORRECTION_CUES.search(text):
        return "correction", "proposed", True
    return None


class TaskEventStore:
    def __init__(self, db: MemoryDatabase, identities: IdentityRepository,
                 timeline: TimelineRepository, links: TaskMemoryLinks) -> None:
        self.db, self.identities, self.timeline, self.links = db, identities, timeline, links
        with db.connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS task_progress_events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task_memory_tasks(task_id)
                        ON DELETE CASCADE,
                    person_id TEXT NOT NULL REFERENCES persons(person_id)
                        ON DELETE CASCADE,
                    source_entry_id TEXT NOT NULL REFERENCES timeline_entries(entry_id)
                        ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK(kind IN
                        ('started','progress','blocked','reported_completed','cancelled','correction')),
                    reported_state TEXT NOT NULL CHECK(reported_state IN
                        ('proposed','active','blocked','reported_completed','cancelled')),
                    evidence_source TEXT NOT NULL CHECK(evidence_source='user_report'),
                    review_state TEXT NOT NULL CHECK(review_state IN
                        ('pending','accepted','rejected')),
                    supersedes_event_id TEXT REFERENCES task_progress_events(event_id)
                        ON DELETE CASCADE,
                    reported_at INTEGER NOT NULL,
                    occurred_at INTEGER,
                    created_at INTEGER NOT NULL,
                    UNIQUE(task_id,source_entry_id)
                );
                CREATE INDEX IF NOT EXISTS task_progress_scope
                    ON task_progress_events(task_id,review_state,reported_at);
            """)

    def _scope(self, db, *, agent_id: str, identity: ChannelIdentity) -> str:
        scope = self.links.owner_scope(identity)
        owner = db.execute("""
            SELECT person_id FROM identities
            WHERE platform=? AND channel_account_id=? AND external_user_id=?
        """, (identity.platform, identity.channel_account_id,
              identity.external_user_id)).fetchone()
        if owner is None:
            raise PermissionError("Unverified QQ identity")
        self.timeline._require_timeline_consent(db, owner["person_id"])
        return owner["person_id"]

    @staticmethod
    def _source(db, *, task, identity, entry_id: str):
        return db.execute("""
            SELECT e.entry_id,e.content,e.recorded_at,e.sequence
            FROM timeline_entries e JOIN private_conversations c
                ON c.conversation_id=e.conversation_id
            WHERE e.entry_id=? AND e.speaker_role='user' AND e.state='received'
              AND e.speaker_person_id=? AND c.person_id=? AND c.agent_id=?
              AND c.platform='qq' AND c.channel_account_id=?
              AND c.external_conversation_id=?
              AND e.entry_id<>?
        """, (entry_id, task["person_id"], task["person_id"], task["agent_id"],
              identity.channel_account_id, "private:" + identity.external_user_id,
              task["origin_entry_id"])).fetchone()

    @staticmethod
    def _task(db, *, task_id: str, agent_id: str, person_id: str, scope: str):
        task = db.execute("SELECT * FROM task_memory_tasks WHERE task_id=?",
                          (task_id,)).fetchone()
        if (task is None or task["agent_id"] != agent_id
                or task["person_id"] != person_id or task["owner_scope"] != scope):
            raise PermissionError("Unknown or other-person goal")
        return task

    def observe(self, *, agent_id: str, identity: ChannelIdentity,
                source_entry_id: str) -> dict:
        """Suggest updates from persisted private user text, never free-form input.

        Accept automatically only an unambiguous strongly matched assertion.
        Uncertain reports or overlapping goals stay pending. Every call is
        independently checked inside the write transaction.
        """
        if not isinstance(source_entry_id, str) or len(source_entry_id) > 128:
            raise ValueError("Invalid source ID")
        scope = self.links.owner_scope(identity)
        with self.db.write() as db:
            person_id = self._scope(db, agent_id=agent_id, identity=identity)
            entry = db.execute("""
                SELECT e.entry_id,e.content,e.recorded_at,e.sequence
                FROM timeline_entries e JOIN private_conversations c
                  ON c.conversation_id=e.conversation_id
                WHERE e.entry_id=? AND e.speaker_role='user' AND e.state='received'
                  AND e.speaker_person_id=? AND c.person_id=? AND c.agent_id=?
                  AND c.platform='qq' AND c.channel_account_id=?
                  AND c.external_conversation_id=?
            """, (source_entry_id, person_id, person_id, agent_id,
                  identity.channel_account_id,
                  "private:" + identity.external_user_id)).fetchone()
            if entry is None:
                raise PermissionError("Not a verified received message")
            extracted = extract_report(entry["content"])
            if extracted is None:
                return {"events": [], "recognized": False}
            kind, reported_state, cautious = extracted
            entries = db.execute("""
                SELECT t.task_id,e.content,e.sequence
                FROM task_memory_tasks t JOIN timeline_entries e
                  ON e.entry_id=t.origin_entry_id
                WHERE t.person_id=? AND t.agent_id=? AND t.owner_scope=?
                  AND e.sequence<?
                ORDER BY e.sequence DESC LIMIT ?
            """, (person_id, agent_id, scope, entry["sequence"],
                  MAX_TASKS_TO_MATCH)).fetchall()
            signals = _features(entry["content"])
            scored = sorted(
                [(_score(signals, row["content"]), row["task_id"]) for row in entries],
                reverse=True)
            strong = [(score, task_id) for score, task_id in scored if score >= 0.35]
            if not strong:
                return {"events": [], "recognized": True}
            # Multiple similar projects are reviewed instead of blindly
            # picking one. Never assign a vague correction automatically.
            ambiguous = cautious or (len(strong) > 1 and
                                     strong[0][0] - strong[1][0] < 0.18)
            choices = strong[:2] if ambiguous else strong[:1]
            out = []
            for score, task_id in choices:
                review = "pending" if ambiguous else "accepted"
                previous = db.execute("""
                    SELECT event_id,kind,reported_state,review_state
                    FROM task_progress_events
                    WHERE task_id=? AND source_entry_id=?
                """, (task_id, source_entry_id)).fetchone()
                if previous:
                    out.append({"event_id": previous["event_id"],
                                "task_id": task_id, "review_state": previous["review_state"],
                                "created": False})
                    continue
                event_id = str(uuid4())
                db.execute("""
                    INSERT INTO task_progress_events
                        (event_id,task_id,person_id,source_entry_id,kind,
                         reported_state,evidence_source,review_state,
                         supersedes_event_id,reported_at,occurred_at,created_at)
                    VALUES(?,?,?,?,?,?,'user_report',?,NULL,?,NULL,?)
                """, (event_id, task_id, person_id, source_entry_id, kind,
                      reported_state, review, entry["recorded_at"], int(time.time())))
                if review == "accepted":
                    db.execute("""
                        INSERT OR IGNORE INTO task_memory_links
                            (task_id,entry_id,relation,linked_at)
                        VALUES(?,?,'follow_up',?)
                    """, (task_id, source_entry_id, int(time.time())))
                out.append({"event_id": event_id, "task_id": task_id,
                            "review_state": review, "created": True})
            return {"events": out, "recognized": True}

    def review(self, *, agent_id: str, identity: ChannelIdentity,
               event_id: str, action: str) -> dict:
        """Admin-only action: make one pending interpretation accepted/rejected."""
        if action not in ("accept", "reject") or not isinstance(event_id, str):
            raise ValueError("Invalid review decision")
        scope = self.links.owner_scope(identity)
        with self.db.write() as db:
            person_id = self._scope(db, agent_id=agent_id, identity=identity)
            event = db.execute("""
                SELECT ev.* FROM task_progress_events ev
                JOIN task_memory_tasks t ON t.task_id=ev.task_id
                WHERE ev.event_id=? AND ev.person_id=? AND t.agent_id=?
                  AND t.owner_scope=?
            """, (event_id, person_id, agent_id, scope)).fetchone()
            if event is None:
                raise PermissionError("Other-person or absent event")
            target = "accepted" if action == "accept" else "rejected"
            if event["review_state"] == target:
                return {"event_id": event_id, "review_state": target, "changed": False}
            if event["review_state"] != "pending":
                raise ValueError("Final review decision is immutable; record a correction")
            db.execute("UPDATE task_progress_events SET review_state=? WHERE event_id=?",
                       (target, event_id))
            if target == "accepted":
                db.execute("""
                    INSERT OR IGNORE INTO task_memory_links
                        (task_id,entry_id,relation,linked_at)
                    VALUES(?,?,'follow_up',?)
                """, (event["task_id"], event["source_entry_id"], int(time.time())))
            return {"event_id": event_id, "review_state": target, "changed": True}

    def correct(self, *, agent_id: str, identity: ChannelIdentity, task_id: str,
                source_entry_id: str, supersedes_event_id: str, reported_state: str) -> dict:
        """Admin-reviewed append-only correction tied to a *different* user quote."""
        if reported_state not in STATES or not isinstance(supersedes_event_id, str):
            raise ValueError("Invalid correction")
        scope = self.links.owner_scope(identity)
        with self.db.write() as db:
            person_id = self._scope(db, agent_id=agent_id, identity=identity)
            task = self._task(db, task_id=task_id, agent_id=agent_id,
                              person_id=person_id, scope=scope)
            source = self._source(db, task=task, identity=identity,
                                  entry_id=source_entry_id)
            prior = db.execute("""
                SELECT ev.event_id,e.sequence FROM task_progress_events ev
                JOIN timeline_entries e ON e.entry_id=ev.source_entry_id
                WHERE ev.event_id=? AND ev.task_id=? AND ev.person_id=?
                  AND ev.review_state='accepted'
            """, (supersedes_event_id, task_id, person_id)).fetchone()
            if source is None or prior is None or source["sequence"] <= prior["sequence"]:
                raise PermissionError("Correction needs a later, same-person user message")
            previous = db.execute("""
                SELECT event_id,kind,reported_state,supersedes_event_id
                FROM task_progress_events WHERE task_id=? AND source_entry_id=?
            """, (task_id, source_entry_id)).fetchone()
            if previous:
                if (previous["kind"] != "correction"
                        or previous["reported_state"] != reported_state
                        or previous["supersedes_event_id"] != supersedes_event_id):
                    raise ValueError("Conflicting correction for the same source")
                return {"event_id": previous["event_id"], "created": False}
            event_id = str(uuid4())
            db.execute("""
                INSERT INTO task_progress_events
                    (event_id,task_id,person_id,source_entry_id,kind,
                     reported_state,evidence_source,review_state,
                     supersedes_event_id,reported_at,occurred_at,created_at)
                VALUES(?,?,?,?,'correction',?,'user_report','accepted',?,?,NULL,?)
            """, (event_id, task_id, person_id, source_entry_id, reported_state,
                  supersedes_event_id, source["recorded_at"], int(time.time())))
            db.execute("""
                INSERT OR IGNORE INTO task_memory_links
                    (task_id,entry_id,relation,linked_at)
                VALUES(?,?,'follow_up',?)
            """, (task_id, source_entry_id, int(time.time())))
            return {"event_id": event_id, "created": True}

    def snapshot(self, *, agent_id: str, identity: ChannelIdentity,
                 task_id: str, limit: int = 15) -> dict:
        if type(limit) is not int or not 1 <= limit <= 30:
            raise ValueError("Event limit must be 1..30")
        scope = self.links.owner_scope(identity)
        with self.db.connection() as db:
            person_id = self._scope(db, agent_id=agent_id, identity=identity)
            task = self._task(db, task_id=task_id, agent_id=agent_id,
                              person_id=person_id, scope=scope)
            rows = db.execute("""
                SELECT ev.*,e.sequence,e.content FROM task_progress_events ev
                JOIN timeline_entries e ON e.entry_id=ev.source_entry_id
                WHERE ev.task_id=? AND ev.person_id=?
                ORDER BY e.sequence,ev.event_id
            """, (task_id, person_id)).fetchall()
            accepted = [r for r in rows if r["review_state"] == "accepted"]
            superseded = {r["supersedes_event_id"] for r in accepted
                          if r["supersedes_event_id"] is not None}
            effective = [r for r in accepted if r["event_id"] not in superseded]
            latest = effective[-1] if effective else None
            state = latest["reported_state"] if latest else "proposed"
            summaries = [
                {"event_id": r["event_id"], "source_entry_id": r["source_entry_id"],
                 "kind": r["kind"], "reported_state": r["reported_state"],
                 "review_state": r["review_state"],
                 "reported_at": r["reported_at"], "occurred_at": r["occurred_at"],
                 "source": r["evidence_source"], "excerpt": r["content"][:240],
                 "supersedes_event_id": r["supersedes_event_id"]}
                for r in rows[-limit:]
            ]
        self.identities.require_consent(person_id, "timeline")
        return {"task_id": task["task_id"], "state": state,
                "status_basis": "user_report" if latest else "task_origin",
                "last_reported_at": latest["reported_at"] if latest else None,
                "events": summaries}
