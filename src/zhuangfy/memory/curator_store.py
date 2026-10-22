"""Persistent candidate memories and restartable reflection jobs (M2).

All authoritative records use the M1 MemoryDatabase SQLite file. Curated
memories carry exact Timeline citations and supersession links. Vector
indexing belongs to M3; this store does NOT write to legacy Mem0/Qdrant.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from uuid import uuid4

from .database import MemoryDatabase
from .identity import IdentityRepository
from .timeline import TimelineEntry, TimelineRepository

MAX_ATTEMPTS = 3
DEFAULT_LEASE_SECONDS = 600
MAX_JOB_ENTRIES = 60
MEMORY_KINDS = frozenset({"semantic", "episodic", "procedural"})
EVENT_STATES = frozenset({"unspecified", "ongoing", "completed", "outdated"})


@dataclass(frozen=True)
class ReflectionJob:
    job_id: str
    conversation_id: str
    agent_id: str
    person_id: str
    after_sequence: int
    through_sequence: int
    attempts: int
    lease_token: str


@dataclass(frozen=True)
class Evidence:
    entry_id: str
    quote: str


@dataclass(frozen=True)
class CuratorOperation:
    action: str
    kind: str = ""
    content: str = ""
    source_entry_ids: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    event_state: str = "unspecified"
    target_memory_id: str | None = None


@dataclass(frozen=True)
class CuratedMemory:
    memory_id: str
    agent_id: str
    person_id: str
    conversation_id: str
    kind: str
    content: str
    event_state: str
    status: str
    source_entry_ids: tuple[str, ...]
    supersedes_memory_id: str | None
    created_at: int
    superseded_at: int | None


class CuratorStore:
    def __init__(self, database: MemoryDatabase, identities: IdentityRepository,
                 timeline: TimelineRepository) -> None:
        self.db = database
        self.identities = identities
        self.timeline = timeline
        with database.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS reflection_jobs (
                    job_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES private_conversations(conversation_id)
                        ON DELETE CASCADE,
                    agent_id TEXT NOT NULL,
                    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
                    after_sequence INTEGER NOT NULL,
                    through_sequence INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','running','done','failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_token TEXT,
                    lease_until INTEGER,
                    last_error_type TEXT,
                    created_at INTEGER NOT NULL,
                    finished_at INTEGER,
                    UNIQUE(conversation_id, person_id, after_sequence, through_sequence)
                );
                CREATE INDEX IF NOT EXISTS reflection_jobs_queue
                  ON reflection_jobs(status, attempts, lease_until, created_at);
                CREATE TABLE IF NOT EXISTS curated_memories (
                    memory_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
                    conversation_id TEXT NOT NULL REFERENCES private_conversations(conversation_id)
                        ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK(kind IN ('semantic','episodic','procedural')),
                    content TEXT NOT NULL,
                    event_state TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','superseded')),
                    supersedes_memory_id TEXT,
                    source_job_id TEXT NOT NULL REFERENCES reflection_jobs(job_id) ON DELETE CASCADE,
                    operation_index INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    superseded_at INTEGER,
                    UNIQUE(source_job_id, operation_index)
                );
                CREATE INDEX IF NOT EXISTS curated_memories_active
                    ON curated_memories(agent_id, person_id, status, created_at);
                CREATE TABLE IF NOT EXISTS curated_evidence (
                    memory_id TEXT NOT NULL REFERENCES curated_memories(memory_id) ON DELETE CASCADE,
                    entry_id TEXT NOT NULL REFERENCES timeline_entries(entry_id) ON DELETE CASCADE,
                    quote TEXT NOT NULL,
                    PRIMARY KEY(memory_id, entry_id)
                );
            """)

    def schedule(self, *, conversation_id: str, person_id: str,
                 limit: int = MAX_JOB_ENTRIES) -> str | None:
        """Schedule the next unprocessed window, never overlapping an unfinished job."""
        if not 1 <= limit <= MAX_JOB_ENTRIES:
            raise ValueError("Reflection batch limit is out of bounds")
        self.identities.require_consent(person_id, "timeline")
        self.identities.require_consent(person_id, "curation")
        # Check access even when the conversation currently contains no messages.
        self.timeline.curated_source_entries(
            conversation_id=conversation_id, person_id=person_id, limit=1,
        )
        with self.db.write() as db:
            existing = db.execute("""SELECT job_id FROM reflection_jobs WHERE conversation_id=?
                AND person_id=? AND status IN ('pending','running','failed')
                ORDER BY created_at DESC LIMIT 1""", (conversation_id, person_id)).fetchone()
            if existing:
                return str(existing["job_id"])
            completed = db.execute("""SELECT MAX(through_sequence) AS seq
                FROM reflection_jobs WHERE conversation_id=? AND person_id=? AND status='done'""",
                (conversation_id, person_id)).fetchone()
            after = int(completed["seq"] or 0)
            rows = db.execute("""SELECT sequence FROM timeline_entries WHERE conversation_id=?
                AND sequence>? AND ((speaker_role='user' AND state='received')
                    OR (speaker_role='assistant' AND state='delivered'))
                ORDER BY sequence LIMIT ?""", (conversation_id, after, limit)).fetchall()
            if not rows:
                return None
            through = int(rows[-1]["sequence"])
            job_id = str(uuid4())
            db.execute("""INSERT INTO reflection_jobs
                (job_id,conversation_id,agent_id,person_id,after_sequence,
                 through_sequence,status,created_at)
                SELECT ?,conversation_id,agent_id,person_id,?,?,'pending',?
                FROM private_conversations WHERE conversation_id=? AND person_id=?""",
                (job_id, after, through, int(time.time()), conversation_id, person_id))
            return job_id

    def claim_next(self, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> ReflectionJob | None:
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("Lease must be between 30 and 3600 seconds")
        now = int(time.time())
        with self.db.write() as db:
            row = db.execute("""SELECT * FROM reflection_jobs
                WHERE attempts < ?
                  AND (status IN ('pending','failed')
                    OR (status='running' AND lease_until < ?))
                ORDER BY created_at, rowid LIMIT 1""", (MAX_ATTEMPTS, now)).fetchone()
            if not row:
                return None
            # Consent is checked again before model invocation; revocation must
            # prevent processing already-queued records.
            for purpose in ("timeline", "curation"):
                consent = db.execute("""SELECT 1 FROM memory_consents WHERE person_id=?
                    AND purpose=? AND granted_at IS NOT NULL AND revoked_at IS NULL""",
                    (row["person_id"], purpose)).fetchone()
                if consent is None:
                    db.execute("""UPDATE reflection_jobs
                        SET status='failed', attempts=?, lease_token=NULL,
                            last_error_type='ConsentRevoked'
                        WHERE job_id=?""", (MAX_ATTEMPTS, row["job_id"]))
                    return None
            token = str(uuid4())
            db.execute("""UPDATE reflection_jobs
                SET status='running', attempts=attempts+1, lease_token=?,
                    lease_until=?, last_error_type=NULL WHERE job_id=?""",
                (token, now + lease_seconds, row["job_id"]))
            return ReflectionJob(
                job_id=row["job_id"], conversation_id=row["conversation_id"],
                agent_id=row["agent_id"], person_id=row["person_id"],
                after_sequence=row["after_sequence"], through_sequence=row["through_sequence"],
                attempts=row["attempts"] + 1, lease_token=token,
            )

    def _owned_running_job(self, db: sqlite3.Connection, job: ReflectionJob) -> sqlite3.Row:
        row = db.execute("SELECT * FROM reflection_jobs WHERE job_id=?", (job.job_id,)).fetchone()
        if (row is None or row["status"] != "running" or row["lease_token"] != job.lease_token
                or row["agent_id"] != job.agent_id or row["person_id"] != job.person_id
                or row["conversation_id"] != job.conversation_id):
            raise RuntimeError("Reflection job lease has been lost or replaced")
        # Check consent under the same transaction as the authoritative write.
        for purpose in ("timeline", "curation"):
            consent = db.execute("""SELECT 1 FROM memory_consents WHERE person_id=?
                AND purpose=? AND granted_at IS NOT NULL AND revoked_at IS NULL""",
                (job.person_id, purpose)).fetchone()
            if consent is None:
                raise PermissionError("Memory consent was revoked during reflection")
        return row

    def read_sources(self, job: ReflectionJob) -> list[TimelineEntry]:
        return self.timeline.curated_source_entries(
            conversation_id=job.conversation_id, person_id=job.person_id,
            after_sequence=job.after_sequence, through_sequence=job.through_sequence,
            limit=MAX_JOB_ENTRIES,
        )

    @staticmethod
    def _memory(row: sqlite3.Row, evidence: list[str]) -> CuratedMemory:
        return CuratedMemory(
            memory_id=row["memory_id"], agent_id=row["agent_id"],
            person_id=row["person_id"], conversation_id=row["conversation_id"],
            kind=row["kind"], content=row["content"], event_state=row["event_state"],
            status=row["status"], source_entry_ids=tuple(evidence),
            supersedes_memory_id=row["supersedes_memory_id"], created_at=row["created_at"],
            superseded_at=row["superseded_at"],
        )

    def current_memories(self, *, agent_id: str, person_id: str, limit: int = 100) -> list[CuratedMemory]:
        """Internal trusted caller only; do not expose unprotected over HTTP."""
        if not 1 <= limit <= 1000:
            raise ValueError("Memory query limit out of bounds")
        with self.db.connection() as db:
            rows = db.execute("""SELECT * FROM curated_memories
                WHERE agent_id=? AND person_id=? AND status='active'
                ORDER BY created_at DESC, memory_id DESC LIMIT ?""",
                (agent_id, person_id, limit)).fetchall()
            return [self._memory(row, [
                r["entry_id"] for r in db.execute(
                    "SELECT entry_id FROM curated_evidence WHERE memory_id=? ORDER BY entry_id",
                    (row["memory_id"],)).fetchall()
            ]) for row in rows]

    def get_active(self, memory_id: str, *, agent_id: str, person_id: str) -> CuratedMemory | None:
        with self.db.connection() as db:
            row = db.execute("""SELECT * FROM curated_memories
                WHERE memory_id=? AND agent_id=? AND person_id=? AND status='active'""",
                (memory_id, agent_id, person_id)).fetchone()
            if not row:
                return None
            evidence = [x[0] for x in db.execute(
                "SELECT entry_id FROM curated_evidence WHERE memory_id=?", (memory_id,)).fetchall()]
            return self._memory(row, evidence)

    def complete(self, job: ReflectionJob, operations: list[CuratorOperation]) -> list[str]:
        """Atomic apply and acknowledgement; lease token makes retries idempotent.

        Validation is performed by curator.py before this call. Enforce critical
        state and scope constraints again inside the SQLite transaction.
        """
        created = []
        with self.db.write() as db:
            self._owned_running_job(db, job)
            for index, operation in enumerate(operations):
                if operation.action == "ignore":
                    continue
                if operation.action not in {"add", "supersede"} or operation.kind not in MEMORY_KINDS:
                    raise ValueError("Invalid operation")
                if operation.event_state not in EVENT_STATES:
                    raise ValueError("Invalid event state")
                if not operation.content or not operation.source_entry_ids or not operation.evidence:
                    raise ValueError("Memory requires verified content and evidence")
                previous = None
                if operation.action == "supersede":
                    if not operation.target_memory_id:
                        raise ValueError("Supersede requires target_memory_id")
                    previous = db.execute("""SELECT * FROM curated_memories WHERE memory_id=?
                        AND agent_id=? AND person_id=? AND status='active'""",
                        (operation.target_memory_id, job.agent_id, job.person_id)).fetchone()
                    if previous is None or previous["kind"] != operation.kind:
                        raise ValueError("Cannot supersede another person's, inactive, or incompatible memory")
                elif operation.target_memory_id is not None:
                    raise ValueError("Add must not have target_memory_id")
                for entry_id in operation.source_entry_ids:
                    row = db.execute("""SELECT 1 FROM timeline_entries
                        WHERE entry_id=? AND conversation_id=? AND sequence>?
                          AND sequence<=? AND
                            ((speaker_role='user' AND state='received')
                            OR (speaker_role='assistant' AND state='delivered'))""",
                        (entry_id, job.conversation_id, job.after_sequence, job.through_sequence)).fetchone()
                    if row is None:
                        raise ValueError("Unverifiable source entry")
                now = int(time.time())
                memory_id = str(uuid4())
                db.execute("""INSERT INTO curated_memories
                    (memory_id,agent_id,person_id,conversation_id,kind,content,
                     event_state,status,supersedes_memory_id,source_job_id,
                     operation_index,created_at,superseded_at)
                    VALUES(?,?,?,?,?,?,?,'active',?,?,?,?,NULL)""",
                    (memory_id, job.agent_id, job.person_id, job.conversation_id,
                     operation.kind, operation.content, operation.event_state,
                     operation.target_memory_id, job.job_id, index, now))
                for evidence in operation.evidence:
                    if evidence.entry_id not in operation.source_entry_ids:
                        raise ValueError("Evidence references an unrelated source")
                    db.execute("""INSERT INTO curated_evidence(memory_id,entry_id,quote)
                        VALUES(?,?,?)""", (memory_id, evidence.entry_id, evidence.quote))
                if previous is not None:
                    db.execute("""UPDATE curated_memories SET status='superseded',superseded_at=?
                        WHERE memory_id=? AND status='active'""", (now, previous["memory_id"]))
                created.append(memory_id)
            db.execute("""UPDATE reflection_jobs SET status='done', lease_token=NULL,
                lease_until=NULL, finished_at=? WHERE job_id=?""",
                (int(time.time()), job.job_id))
        return created

    def fail(self, job: ReflectionJob, exception_type: str) -> None:
        """Save only the exception *type*, never model output or user messages."""
        with self.db.write() as db:
            row = db.execute("SELECT status,lease_token FROM reflection_jobs WHERE job_id=?",
                             (job.job_id,)).fetchone()
            if row is None or row["status"] != "running" or row["lease_token"] != job.lease_token:
                return
            db.execute("""UPDATE reflection_jobs SET status='failed', lease_token=NULL,
                lease_until=NULL, last_error_type=? WHERE job_id=?""",
                (exception_type[:80], job.job_id))

    def retry_failed_job(self, job_id: str) -> bool:
        """Explicit trusted-operator retry after MAX_ATTEMPTS.

        This is not exposed to chat users: a human or trusted service should
        fix the model/validation error before resetting the backoff budget.
        """
        with self.db.write() as db:
            row = db.execute("""SELECT person_id,status,lease_until FROM reflection_jobs
                WHERE job_id=?""", (job_id,)).fetchone()
            if row is None or row["status"] != "failed":
                return False
            for purpose in ("timeline", "curation"):
                consent = db.execute("""SELECT 1 FROM memory_consents WHERE person_id=?
                    AND purpose=? AND granted_at IS NOT NULL AND revoked_at IS NULL""",
                    (row["person_id"], purpose)).fetchone()
                if consent is None:
                    raise PermissionError("Cannot retry a job without renewed consent")
            db.execute("""UPDATE reflection_jobs SET status='pending', attempts=0,
                lease_token=NULL, lease_until=NULL, last_error_type=NULL
                WHERE job_id=?""", (job_id,))
            return True

    def job_status(self, job_id: str) -> tuple[str, int, str | None] | None:
        with self.db.connection() as db:
            row = db.execute("SELECT status, attempts, last_error_type FROM reflection_jobs WHERE job_id=?",
                             (job_id,)).fetchone()
            return (row["status"], row["attempts"], row["last_error_type"]) if row else None

    def erase_person(self, person_id: str) -> int:
        """Delete all generated memories for this person; leaves others untouched."""
        with self.db.write() as db:
            count = db.execute("SELECT COUNT(*) FROM curated_memories WHERE person_id=?",
                               (person_id,)).fetchone()[0]
            db.execute("DELETE FROM curated_memories WHERE person_id=?", (person_id,))
            db.execute("DELETE FROM reflection_jobs WHERE person_id=?", (person_id,))
            return count
