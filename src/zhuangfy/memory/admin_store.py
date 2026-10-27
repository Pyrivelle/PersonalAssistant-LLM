"""M5 audited memory management, evidence inspection, and episode links.

Admin-only interface. Manual edits are marked as *overrides*, never disguised
as claims automatically verified by the original user quotes. All database
changes use SQLite transactions; index re-projection is called after commit.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .curator import SECRET
from .curator_store import CuratorStore
from .database import MemoryDatabase
from .identity import IdentityRepository

LINK_TYPES = frozenset({"related_to", "follow_up", "resolved_by"})


class MemoryAdminStore:
    def __init__(self, db: MemoryDatabase, identity: IdentityRepository, curated: CuratorStore):
        self.db = db
        self.identity = identity
        self.curated = curated
        with db.connection() as con:
            columns = {row["name"] for row in con.execute("PRAGMA table_info(curated_memories)")}
            if "manual_override" not in columns:
                con.execute("""ALTER TABLE curated_memories
                    ADD COLUMN manual_override INTEGER NOT NULL DEFAULT 0""")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS curated_memory_edits (
                    edit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL REFERENCES curated_memories(memory_id)
                        ON DELETE CASCADE,
                    old_content TEXT NOT NULL,
                    new_content TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    edited_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS episode_links (
                    source_memory_id TEXT NOT NULL
                        REFERENCES curated_memories(memory_id) ON DELETE CASCADE,
                    target_memory_id TEXT NOT NULL
                        REFERENCES curated_memories(memory_id) ON DELETE CASCADE,
                    person_id TEXT NOT NULL REFERENCES persons(person_id)
                        ON DELETE CASCADE,
                    relation TEXT NOT NULL
                        CHECK(relation IN ('related_to','follow_up','resolved_by')),
                    linked_at INTEGER NOT NULL,
                    PRIMARY KEY(source_memory_id,target_memory_id,relation)
                );
            """)

    def correct(self, *, agent_id: str, person_id: str, memory_id: str,
                new_content: str, reason: str) -> dict:
        if (not isinstance(new_content, str) or not 3 <= len(new_content.strip()) <= 400
                or SECRET.search(new_content)):
            raise ValueError("Correction must be 3..400 chars and contain no credentials")
        if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 200:
            raise ValueError("A short change reason is required")
        with self.db.write() as db:
            old = db.execute("""SELECT content FROM curated_memories WHERE
                memory_id=? AND agent_id=? AND person_id=? AND status='active'""",
                (memory_id, agent_id, person_id)).fetchone()
            if not old:
                raise ValueError("Unknown, inactive or other-person memory")
            if old["content"] == new_content.strip():
                return {"changed": False}
            db.execute("""INSERT INTO curated_memory_edits
                (memory_id,old_content,new_content,reason,edited_at)
                VALUES(?,?,?,?,?)""",
                (memory_id, old["content"], new_content.strip(), reason.strip(), int(time.time())))
            # SQLite FTS trigger indexes the new content. A Qdrant projection
            # digest mismatch makes previous vectors unusable immediately.
            db.execute("""UPDATE curated_memories SET content=?,manual_override=1
                WHERE memory_id=?""", (new_content.strip(), memory_id))
        return {"changed": True, "manual_override": True}

    def forget_memory(self, *, agent_id: str, person_id: str, memory_id: str) -> dict:
        """Delete selected memory and all its prior superseded versions.

        Future/lateral episodes are NOT erased, so the admin should inspect
        related links and privacy requirements when erasing a whole project.
        """
        with self.db.write() as db:
            root = db.execute("""SELECT memory_id FROM curated_memories WHERE
                memory_id=? AND agent_id=? AND person_id=?""",
                (memory_id, agent_id, person_id)).fetchone()
            if not root:
                raise ValueError("Unknown or other-person memory")
            ids = {memory_id}
            while True:
                older = {row["supersedes_memory_id"] for row in db.execute(
                    """SELECT supersedes_memory_id FROM curated_memories
                        WHERE agent_id=? AND person_id=? AND memory_id IN (%s)"""
                    % ",".join("?" for _ in ids), (agent_id, person_id, *ids)).fetchall()
                    if row["supersedes_memory_id"]}
                extended = ids | older
                if extended == ids:
                    break
                ids = extended
            db.executemany("""DELETE FROM curated_memories WHERE memory_id=?
                AND agent_id=? AND person_id=?""",
                [(memory, agent_id, person_id) for memory in ids])
        return {"erased_versions": len(ids)}

    def forget_person(self, *, person_id: str) -> dict:
        """Erase canonical person and all private conversations by FK cascade.

        Production backups, legacy Mem0, QQ stores and other external replicas
        are not covered by this local SQLite erasure.
        """
        with self.db.write() as db:
            found = db.execute("SELECT 1 FROM persons WHERE person_id=?", (person_id,)).fetchone()
            if not found:
                raise ValueError("Unknown person")
            db.execute("DELETE FROM persons WHERE person_id=?", (person_id,))
        return {"erased": True}

    def evidence(self, *, agent_id: str, person_id: str, memory_id: str) -> dict:
        with self.db.connection() as db:
            record = db.execute("""SELECT m.memory_id,m.content,m.manual_override,
                m.kind,m.event_state,m.status,m.supersedes_memory_id
                FROM curated_memories m
                WHERE m.memory_id=? AND m.agent_id=? AND m.person_id=?""",
                (memory_id, agent_id, person_id)).fetchone()
            if record is None:
                raise ValueError("Unknown or other-person memory")
            rows = db.execute("""SELECT e.entry_id,e.quote,t.speaker_role,
                       t.content AS original,t.state,t.recorded_at
                FROM curated_evidence e
                JOIN timeline_entries t ON t.entry_id=e.entry_id
                WHERE e.memory_id=? ORDER BY t.sequence""", (memory_id,)).fetchall()
            edits = db.execute("""SELECT reason,edited_at FROM curated_memory_edits
                WHERE memory_id=? ORDER BY edit_id""", (memory_id,)).fetchall()
            links = db.execute("""SELECT source_memory_id,target_memory_id,relation
                FROM episode_links
                WHERE person_id=? AND (source_memory_id=? OR target_memory_id=?)
                ORDER BY linked_at""", (person_id, memory_id, memory_id)).fetchall()
            return {"memory": dict(record), "sources": [dict(r) for r in rows],
                    "corrections": [dict(r) for r in edits], "event_links": [dict(r) for r in links]}

    def link_episodes(self, *, agent_id: str, person_id: str,
                      source_memory_id: str, target_memory_id: str, relation: str) -> dict:
        if relation not in LINK_TYPES or source_memory_id == target_memory_id:
            raise ValueError("Invalid episode relationship")
        with self.db.write() as db:
            rows = db.execute("""SELECT memory_id FROM curated_memories WHERE
                agent_id=? AND person_id=? AND kind='episodic' AND status='active'
                AND memory_id IN (?,?)""",
                (agent_id, person_id, source_memory_id, target_memory_id)).fetchall()
            if len(rows) != 2:
                raise PermissionError("Both current episodes must belong to the same person")
            db.execute("""INSERT OR IGNORE INTO episode_links
                (source_memory_id,target_memory_id,person_id,relation,linked_at)
                VALUES(?,?,?,?,?)""",
                (source_memory_id, target_memory_id, person_id, relation, int(time.time())))
        return {"linked": True}

    def list_jobs(self, *, person_id: str) -> list[dict]:
        if self.identity.get_person(person_id) is None:
            raise ValueError("Unknown person")
        with self.db.connection() as db:
            return [dict(r) for r in db.execute("""
                SELECT j.job_id,j.status,j.attempts,j.last_error_type,
                       j.created_at,j.finished_at,j.conversation_id,
                       c.platform,c.channel_account_id,c.external_conversation_id,
                       j.after_sequence,j.through_sequence,
                       (SELECT COUNT(*) FROM timeline_entries e
                        WHERE e.conversation_id=j.conversation_id
                          AND e.sequence>j.after_sequence
                          AND e.sequence<=j.through_sequence
                          AND ((e.speaker_role='user' AND e.state='received')
                            OR (e.speaker_role='assistant' AND e.state='delivered'))
                       ) AS source_count,
                       (SELECT MIN(e.recorded_at) FROM timeline_entries e
                        WHERE e.conversation_id=j.conversation_id
                          AND e.sequence>j.after_sequence
                          AND e.sequence<=j.through_sequence
                          AND ((e.speaker_role='user' AND e.state='received')
                            OR (e.speaker_role='assistant' AND e.state='delivered'))
                       ) AS source_first_at,
                       (SELECT MAX(e.recorded_at) FROM timeline_entries e
                        WHERE e.conversation_id=j.conversation_id
                          AND e.sequence>j.after_sequence
                          AND e.sequence<=j.through_sequence
                          AND ((e.speaker_role='user' AND e.state='received')
                            OR (e.speaker_role='assistant' AND e.state='delivered'))
                       ) AS source_last_at,
                       (SELECT COUNT(*) FROM curated_memories m
                        WHERE m.source_job_id=j.job_id) AS memory_count
                FROM reflection_jobs j
                JOIN private_conversations c ON c.conversation_id=j.conversation_id
                WHERE j.person_id=? ORDER BY j.created_at DESC LIMIT 200
            """, (person_id,)).fetchall()]

    def retry_job(self, *, person_id: str, job_id: str) -> dict:
        with self.db.connection() as db:
            job = db.execute(
                "SELECT person_id FROM reflection_jobs WHERE job_id=?", (job_id,)).fetchone()
            if not job or job["person_id"] != person_id:
                raise PermissionError("Job belongs to another person")
        return {"requeued": self.curated.retry_failed_job(job_id)}
