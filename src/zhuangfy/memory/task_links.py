"""Phase 1: authoritative links between trusted goals and real Timeline evidence.

Vector similarity is NOT proof of task identity. A goal must be explicitly
connected to an already-persisted inbound QQ message by the trusted host.
This repository neither starts goals nor creates/refines any long-term memory.
"""
from __future__ import annotations

import re
import time

from .database import MemoryDatabase
from .identity import ChannelIdentity, IdentityRepository
from .timeline import TimelineRepository


class TaskMemoryLinks:
    def __init__(self, database: MemoryDatabase, identities: IdentityRepository,
                 timeline: TimelineRepository) -> None:
        self.database = database
        self.identities = identities
        self.timeline = timeline
        with database.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS task_memory_tasks (
                    task_id TEXT PRIMARY KEY,
                    person_id TEXT NOT NULL REFERENCES persons(person_id)
                        ON DELETE CASCADE,
                    agent_id TEXT NOT NULL,
                    owner_scope TEXT NOT NULL,
                    origin_entry_id TEXT NOT NULL REFERENCES timeline_entries(entry_id)
                        ON DELETE CASCADE,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS task_memory_person
                    ON task_memory_tasks(agent_id,person_id);
                CREATE TABLE IF NOT EXISTS task_memory_links (
                    task_id TEXT NOT NULL REFERENCES task_memory_tasks(task_id)
                        ON DELETE CASCADE,
                    entry_id TEXT NOT NULL REFERENCES timeline_entries(entry_id)
                        ON DELETE CASCADE,
                    relation TEXT NOT NULL CHECK(relation IN ('origin','follow_up')),
                    linked_at INTEGER NOT NULL,
                    PRIMARY KEY(task_id,entry_id)
                );
            """)

    @staticmethod
    def owner_scope(identity: ChannelIdentity) -> str:
        """Require the unambiguous Bot-scoped QQ private owner convention."""
        if (identity.platform != "qq"
                or not all(isinstance(s, str) and s.isascii() and s.isdecimal()
                           and 0 < int(s) and str(int(s)) == s
                           for s in (identity.channel_account_id,
                                     identity.external_user_id))):
            raise ValueError("Phase 1 requires a canonical QQ private identity")
        return ("qq:" + identity.channel_account_id + ":private:"
                + identity.external_user_id)

    @staticmethod
    def _id(value: str, name: str) -> str:
        if (not isinstance(value, str) or not 1 <= len(value) <= 128
                or not re.fullmatch(r"[A-Za-z0-9_-]+", value)):
            raise ValueError(f"Invalid {name}")
        return value

    def bind_origin(self, *, agent_id: str, identity: ChannelIdentity,
                    task_id: str, owner_scope: str, source_entry_id: str) -> dict:
        """Bind an operator-verified GoalStore goal to an actual user entry.

        The service cannot trust goal IDs supplied by a chat model. Only the
        separate ADMIN HTTP route may invoke this function. An operator bridge
        must first inspect the existing GoalStore goal and confirm that its
        source_event_id is this same Timeline entry_id.
        """
        task_id = self._id(task_id, "task_id")
        source_entry_id = self._id(source_entry_id, "source_entry_id")
        expected = self.owner_scope(identity)
        if not isinstance(owner_scope, str) or owner_scope != expected:
            raise PermissionError("Goal owner does not match the verified QQ channel")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("Agent ID is required")
        with self.database.write() as db:
            owner = db.execute("""
                SELECT person_id FROM identities
                WHERE platform=? AND channel_account_id=? AND external_user_id=?
            """, (identity.platform, identity.channel_account_id,
                  identity.external_user_id)).fetchone()
            if owner is None:
                raise PermissionError("Unknown QQ identity")
            person_id = owner["person_id"]
            self.timeline._require_timeline_consent(db, person_id)
            evidence = db.execute("""
                SELECT e.entry_id FROM timeline_entries AS e
                JOIN private_conversations AS c
                    ON c.conversation_id=e.conversation_id
                WHERE e.entry_id=? AND e.speaker_role='user'
                    AND e.state='received' AND e.speaker_person_id=?
                    AND c.person_id=? AND c.agent_id=?
                    AND c.platform='qq' AND c.channel_account_id=?
                    AND c.external_conversation_id=?
            """, (source_entry_id, person_id, person_id, agent_id,
                  identity.channel_account_id,
                  "private:" + identity.external_user_id)).fetchone()
            if evidence is None:
                raise PermissionError("Source is not a verified QQ private user entry")
            prior = db.execute("""
                SELECT person_id,agent_id,owner_scope,origin_entry_id
                FROM task_memory_tasks WHERE task_id=?
            """, (task_id,)).fetchone()
            if prior is not None:
                if (prior["person_id"] != person_id or prior["agent_id"] != agent_id
                        or prior["owner_scope"] != owner_scope
                        or prior["origin_entry_id"] != source_entry_id):
                    raise PermissionError("Task ID already bound to another origin")
                return {"task_id": task_id, "source_entry_id": source_entry_id,
                        "created": False}
            now = int(time.time())
            db.execute("""
                INSERT INTO task_memory_tasks
                    (task_id,person_id,agent_id,owner_scope,origin_entry_id,created_at)
                VALUES(?,?,?,?,?,?)
            """, (task_id, person_id, agent_id, owner_scope, source_entry_id, now))
            db.execute("""
                INSERT INTO task_memory_links(task_id,entry_id,relation,linked_at)
                VALUES(?,?,'origin',?)
            """, (task_id, source_entry_id, now))
            return {"task_id": task_id, "source_entry_id": source_entry_id,
                    "created": True}

    def get_task(self, *, agent_id: str, identity: ChannelIdentity,
                 task_id: str) -> dict:
        """A verified QQ participant can inspect only their own linked source."""
        task_id = self._id(task_id, "task_id")
        expected_scope = self.owner_scope(identity)
        with self.database.connection() as db:
            owner = db.execute("""
                SELECT person_id FROM identities
                WHERE platform=? AND channel_account_id=? AND external_user_id=?
            """, (identity.platform, identity.channel_account_id,
                  identity.external_user_id)).fetchone()
            if owner is None:
                raise PermissionError("Unknown identity")
            self.timeline._require_timeline_consent(db, owner["person_id"])
            task = db.execute("""
                SELECT task_id,person_id,agent_id,owner_scope,origin_entry_id,
                       created_at FROM task_memory_tasks WHERE task_id=?
            """, (task_id,)).fetchone()
            if (task is None or task["person_id"] != owner["person_id"]
                    or task["agent_id"] != agent_id
                    or task["owner_scope"] != expected_scope):
                raise PermissionError("Unknown or other-person task")
            sources = db.execute("""
                SELECT l.entry_id,l.relation,e.source_message_id,e.recorded_at
                FROM task_memory_links AS l
                JOIN timeline_entries AS e ON e.entry_id=l.entry_id
                WHERE l.task_id=? ORDER BY l.linked_at,l.entry_id
            """, (task_id,)).fetchall()
            # Do not fabricate GoalStore state or read other people's messages.
            return {
                "task_id": task_id,
                "owner_scope": task["owner_scope"],
                "origin_entry_id": task["origin_entry_id"],
                "created_at": task["created_at"],
                "evidence": [dict(row) for row in sources],
            }
