"""First-stage manual memory CRUD and scoped semantic recall.

No QQ user IDs are resolved here. The caller must supply a verified person_id.
Do not ingest chat transcripts until a consent and timeline pipeline exists.
Qdrant is only an eventually consistent, rebuildable index.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID, uuid4

from .repository import MemoryEntry, SQLiteMemoryRepository
from .vector import EmbeddingProvider, VectorIndex, checked_vector

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoreResult:
    entry: MemoryEntry
    indexed: bool


@dataclass(frozen=True)
class RecallResult:
    entry: MemoryEntry
    score: float


@dataclass(frozen=True)
class RepairResult:
    indexed: int
    purged: int
    failures: int


class MemoryVectorService:
    """One-owner-process coordinator for SQLite and an external vector index."""

    def __init__(self, repository: SQLiteMemoryRepository, index: VectorIndex,
                 embedding: EmbeddingProvider, *, agent_id: str) -> None:
        if not agent_id.strip() or not embedding.model_id.strip() or embedding.dimension <= 0:
            raise ValueError("Agent ID and embedding metadata are required")
        self.repository = repository
        self.index = index
        self.embedding = embedding
        self.agent_id = agent_id
        self.index_version = f"{embedding.model_id}@{embedding.dimension}"

    def _document_vector(self, text: str) -> list[float]:
        return checked_vector(self.embedding.embed_document(text), self.embedding.dimension)

    def remember(self, *, person_id: str, content: str, kind: str = "semantic",
                 source_turn_ids: tuple[str, ...] = (), memory_id: str | None = None) -> StoreResult:
        """Create a manually approved private memory. Never infers identity."""
        if not person_id.strip() or not content.strip() or kind not in {"semantic", "episodic", "procedural"}:
            raise ValueError("Verified person_id, nonempty content, and supported kind are required")
        if not isinstance(source_turn_ids, tuple) or any(not isinstance(t, str) or not t for t in source_turn_ids):
            raise ValueError("source_turn_ids must be a tuple of nonempty strings")
        memory_id = memory_id or str(uuid4())
        if str(UUID(memory_id)) != memory_id:
            raise ValueError("memory_id must be a canonical UUID")
        entry = self.repository.add(memory_id, self.agent_id, person_id, content, kind, source_turn_ids)
        try:
            vector = self._document_vector(entry.content)
            self.index.upsert(entry.memory_id, entry.agent_id, entry.person_id, vector)
            self.repository.mark_indexed(entry.memory_id, self.index_version)
        except Exception:
            logger.warning("Vector indexing pending for memory_id=%s", entry.memory_id, exc_info=True)
            return StoreResult(entry, indexed=False)
        return StoreResult(entry, indexed=True)

    def recall(self, *, person_id: str, query: str, limit: int = 5) -> list[RecallResult]:
        if not person_id.strip() or not query.strip():
            raise ValueError("Verified person_id and nonempty query are required")
        query_vector = checked_vector(self.embedding.embed_query(query), self.embedding.dimension)
        matches = self.index.search(self.agent_id, person_id, query_vector, limit)
        results = []
        for hit in matches:
            # Defense in depth: SQLite scope check is required even if the vector
            # backend accidentally returns a foreign person's memory IDs.
            entry = self.repository.get(hit.memory_id, self.agent_id, person_id)
            if entry and entry.indexed_model == self.index_version:
                results.append(RecallResult(entry, hit.score))
        return results

    def forget(self, *, person_id: str, memory_id: str) -> bool:
        if not person_id.strip():
            raise ValueError("Verified person_id is required")
        if not self.repository.mark_deleted(memory_id, self.agent_id, person_id):
            return False
        try:
            self.index.delete(memory_id)
        except Exception:
            # Text is scrubbed; Qdrant still has a UUID + scope until repaired.
            logger.warning("Vector deletion pending for memory_id=%s", memory_id, exc_info=True)
        else:
            self.repository.purge_tombstone(memory_id)
        return True

    def repair_index(self, *, limit: int = 100) -> RepairResult:
        """Idempotently retry pending upserts and vector tombstone removals."""
        indexed = purged = failures = 0
        for memory_id in self.repository.deleted_ids(limit):
            try:
                self.index.delete(memory_id)
                self.repository.purge_tombstone(memory_id)
                purged += 1
            except Exception:
                logger.warning("Failed removing vector tombstone=%s", memory_id, exc_info=True)
                failures += 1
        for entry in self.repository.list_unindexed(self.index_version, limit):
            try:
                vector = self._document_vector(entry.content)
                self.index.upsert(entry.memory_id, entry.agent_id, entry.person_id, vector)
                self.repository.mark_indexed(entry.memory_id, self.index_version)
                indexed += 1
            except Exception:
                logger.warning("Failed reindexing memory_id=%s", entry.memory_id, exc_info=True)
                failures += 1
        return RepairResult(indexed=indexed, purged=purged, failures=failures)

    def close(self) -> None:
        self.index.close()
