"""M3 derived index: authoritative curated_memories -> Qdrant and SQLite FTS5.

The FTS table uses SQLite triggers, including when a conversation is erased
by foreign-key cascade. The Qdrant index is eventually consistent; query-time
checks always re-validate the authoritative row, content hash and owner.

Only ONE memory-server process should own a persistent Qdrant Local directory.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from .curator_store import CuratorStore
from .database import MemoryDatabase
from .identity import IdentityRepository
from .vector import EmbeddingProvider, VectorIndex, checked_vector

LOG = logging.getLogger(__name__)


def content_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SyncResult:
    indexed: int
    removed: int
    failed: int


class CuratedIndexProjector:
    """Idempotent projection of current verified memories (NOT M0 manual memories)."""

    def __init__(self, database: MemoryDatabase, identity: IdentityRepository,
                 store: CuratorStore, embedding: EmbeddingProvider, index: VectorIndex):
        self.database = database
        self.identity = identity
        self.store = store
        self.embedding = embedding
        self.index = index
        self.version = f"{embedding.model_id}@{embedding.dimension}"
        with database.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS curated_vector_projection (
                    memory_id TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    person_id TEXT NOT NULL,
                    PRIMARY KEY(memory_id, model_version)
                );
            """)
            # FTS5 trigram indexes Chinese phrases and mixed-language content.
            # The virtual table indexes content by curated_memories rowid.
            db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS curated_memory_fts
                USING fts5(content, content='curated_memories',
                              content_rowid='rowid', tokenize='trigram')""")
            db.executescript("""
                CREATE TRIGGER IF NOT EXISTS curated_fts_ai
                AFTER INSERT ON curated_memories BEGIN
                    INSERT INTO curated_memory_fts(rowid,content) VALUES(new.rowid,new.content);
                END;
                CREATE TRIGGER IF NOT EXISTS curated_fts_ad
                AFTER DELETE ON curated_memories BEGIN
                    INSERT INTO curated_memory_fts(curated_memory_fts,rowid,content)
                        VALUES('delete',old.rowid,old.content);
                END;
                CREATE TRIGGER IF NOT EXISTS curated_fts_au
                AFTER UPDATE OF content ON curated_memories BEGIN
                    INSERT INTO curated_memory_fts(curated_memory_fts,rowid,content)
                        VALUES('delete',old.rowid,old.content);
                    INSERT INTO curated_memory_fts(rowid,content) VALUES(new.rowid,new.content);
                END;
            """)
            # Safe to rebuild: FTS is derived from curated_memories and never the
            # authoritative source. Handles existing rows created before M3.
            db.execute("INSERT INTO curated_memory_fts(curated_memory_fts) VALUES('rebuild')")

    def sync(self, *, limit: int = 100) -> SyncResult:
        """Reconcile tombstones BEFORE upserts; no LLM/network in SQLite writes.

        Other embedding versions use separate collection names. This instance
        only reconciles its own model version; old versions require separate
        cleanup or destruction of their collections.
        """
        if not 1 <= limit <= 2000:
            raise ValueError("limit must be 1..2000")
        removed = indexed = failed = 0
        with self.database.connection() as db:
            # Projection entries remain as tombstones after curated row erasure:
            # deliberately no FK to curated_memories here.
            rows = db.execute("""SELECT p.memory_id,p.content_digest,p.person_id,p.agent_id AS expected_agent,
                    m.content,m.status,m.agent_id,m.person_id AS active_person
                FROM curated_vector_projection p
                LEFT JOIN curated_memories m ON m.memory_id=p.memory_id
                WHERE p.model_version=? ORDER BY p.memory_id""", (self.version,)).fetchall()
        obsolete = []
        for row in rows:
            if (row["status"] != "active"
                    or row["active_person"] != row["person_id"]
                    or row["agent_id"] != row["expected_agent"]
                    or row["content"] is None
                    or content_digest(row["content"]) != row["content_digest"]):
                obsolete.append(row["memory_id"])
        # No authority data is removed on Qdrant failures.
        for memory_id in obsolete[:limit]:
            try:
                self.index.delete(memory_id)
                with self.database.write() as db:
                    db.execute("""DELETE FROM curated_vector_projection
                        WHERE memory_id=? AND model_version=?""", (memory_id, self.version))
                removed += 1
            except Exception:
                LOG.warning("Cannot remove stale vector: memory_id=%s", memory_id, exc_info=True)
                failed += 1
        # If any obsolete records remain, don't re-upsert their IDs until the
        # old vector can be deleted (prevents an old index version being reused).
        with self.database.connection() as db:
            active = db.execute("""SELECT m.memory_id,m.agent_id,m.person_id,m.content,
                       p.content_digest AS digest
                FROM curated_memories m
                LEFT JOIN curated_vector_projection p ON p.memory_id=m.memory_id
                     AND p.model_version=?
                WHERE m.status='active'
                ORDER BY m.created_at,m.memory_id""", (self.version,)).fetchall()
        for row in active:
            if indexed + failed >= limit:
                break
            if row["digest"] == content_digest(row["content"]):
                continue
            try:
                if not (self.identity.has_consent(row["person_id"], "timeline")
                        and self.identity.has_consent(row["person_id"], "curation")):
                    continue
                vector = checked_vector(
                    self.embedding.embed_document(row["content"]), self.embedding.dimension)
                self.index.upsert(row["memory_id"], row["agent_id"], row["person_id"], vector)
                with self.database.write() as db:
                    latest = db.execute("""SELECT agent_id,person_id,content,status
                        FROM curated_memories WHERE memory_id=?""", (row["memory_id"],)).fetchone()
                    # The curator may supersede or erase while embedding runs.
                    if (latest is None or latest["status"] != "active"
                            or latest["agent_id"] != row["agent_id"]
                            or latest["person_id"] != row["person_id"]
                            or latest["content"] != row["content"]):
                        # No projection pointer is written: the next sync can
                        # delete the orphan; query-time auth rejects it.
                        raise RuntimeError("Curated row changed while indexing")
                    db.execute("""INSERT INTO curated_vector_projection
                        (memory_id,model_version,content_digest,agent_id,person_id)
                        VALUES(?,?,?,?,?)
                        ON CONFLICT(memory_id,model_version) DO UPDATE SET
                          content_digest=excluded.content_digest,
                          agent_id=excluded.agent_id,person_id=excluded.person_id""",
                        (row["memory_id"], self.version, content_digest(row["content"]),
                         row["agent_id"], row["person_id"]))
                indexed += 1
            except Exception:
                LOG.warning("Curated vector not indexed: memory_id=%s", row["memory_id"], exc_info=True)
                failed += 1
        return SyncResult(indexed, removed, failed)

    def close(self) -> None:
        self.index.close()
