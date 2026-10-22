"""M3 hybrid RAG: Chinese-friendly trigram FTS5 + scoped semantic recall.

The authoritative SQLite row and per-model projection are checked again for
EVERY Qdrant hit. Text from memory is untrusted evidence, never an instruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .curator_store import CuratedMemory, CuratorStore
from .identity import IdentityRepository
from .projector import CuratedIndexProjector, content_digest
from .vector import checked_vector

RRF_K = 60


@dataclass(frozen=True)
class MemoryHit:
    memory: CuratedMemory
    rank_score: float
    retrieval_paths: tuple[str, ...]


class HybridMemoryRetriever:
    def __init__(self, projector: CuratedIndexProjector, store: CuratorStore,
                 identity: IdentityRepository) -> None:
        self.projector = projector
        self.store = store
        self.identity = identity

    def search(self, *, agent_id: str, person_id: str, query: str,
               limit: int = 5, include_outdated: bool = False) -> list[MemoryHit]:
        if not agent_id.strip() or not person_id.strip() or not query.strip():
            raise ValueError("Agent, verified person and query are required")
        if not 1 <= limit <= 20:
            raise ValueError("limit must be 1..20")
        if len(query) > 2048:
            raise ValueError("Query is too long")
        # An authenticated service endpoint must resolve principal -> person_id
        # before calling here. Do not expose this raw internal function publicly.
        self.identity.require_consent(person_id, "timeline")
        self.identity.require_consent(person_id, "curation")
        lexical = []
        with self.projector.database.connection() as db:
            # FTS5 trigram matches >=3 character phrases. Short technical names
            # (Qt, C++) use bounded SQL substring fallback.
            if len(query.strip()) >= 3:
                fts_query = '"' + query.strip().replace('"', '""') + '"'
                try:
                    rows = db.execute("""SELECT m.memory_id FROM curated_memory_fts f
                        JOIN curated_memories m ON m.rowid=f.rowid
                        WHERE curated_memory_fts MATCH ?
                          AND m.agent_id=? AND m.person_id=? AND m.status='active'
                          AND (? OR m.event_state != 'outdated')
                        ORDER BY bm25(curated_memory_fts) LIMIT ?""",
                        (fts_query, agent_id, person_id, int(include_outdated), limit * 4)).fetchall()
                    lexical = [r["memory_id"] for r in rows]
                except Exception as exc:
                    # Raise rather than silently dropping all lexical evidence.
                    raise RuntimeError("SQLite FTS5 search failed") from exc
            if len(query.strip()) < 3 or not lexical:
                rows = db.execute("""SELECT memory_id FROM curated_memories
                    WHERE agent_id=? AND person_id=? AND status='active'
                      AND (? OR event_state != 'outdated')
                      AND instr(lower(content),lower(?))>0
                    ORDER BY created_at DESC LIMIT ?""",
                    (agent_id, person_id, int(include_outdated), query.strip(), limit * 4)).fetchall()
                lexical = list(dict.fromkeys(lexical + [r["memory_id"] for r in rows]))
        semantic = []
        try:
            query_vector = checked_vector(
                self.projector.embedding.embed_query(query),
                self.projector.embedding.dimension)
            hits = self.projector.index.search(agent_id, person_id, query_vector, min(50, limit * 4))
            # Don't trust vector payload even after index-side scope filtering.
            with self.projector.database.connection() as db:
                for hit in hits:
                    row = db.execute("""SELECT m.content,m.status,m.event_state,m.agent_id,
                            m.person_id,p.content_digest,p.agent_id AS p_agent,
                            p.person_id AS p_person
                        FROM curated_memories m
                        INNER JOIN curated_vector_projection p ON p.memory_id=m.memory_id
                            AND p.model_version=?
                        WHERE m.memory_id=?""",
                        (self.projector.version, hit.memory_id)).fetchone()
                    if (row is None or row["status"] != "active"
                            or row["agent_id"] != agent_id or row["person_id"] != person_id
                            or row["p_agent"] != agent_id or row["p_person"] != person_id
                            or row["content_digest"] != content_digest(row["content"])
                            or (not include_outdated and row["event_state"] == "outdated")):
                        continue
                    semantic.append(hit.memory_id)
        except (OSError, RuntimeError):
            # Semantic index is derived; lexical recall still works when it is
            # temporarily unavailable. Programming/config errors propagate.
            semantic = []
        merged: dict[str, tuple[float, set[str]]] = {}
        for source, ids in (("lexical", lexical), ("semantic", semantic)):
            for rank, memory_id in enumerate(dict.fromkeys(ids), start=1):
                score, kinds = merged.get(memory_id, (0.0, set()))
                kinds.add(source)
                merged[memory_id] = (score + 1.0 / (RRF_K + rank), kinds)
        # Fetch authority records only for the authenticated scope, and verify
        # consent again in case it was revoked during query embedding.
        self.identity.require_consent(person_id, "timeline")
        self.identity.require_consent(person_id, "curation")
        active = {m.memory_id: m for m in self.store.current_memories(
            agent_id=agent_id, person_id=person_id, limit=1000)}
        ranked = []
        for memory_id, (score, kinds) in merged.items():
            memory = active.get(memory_id)
            if memory and (include_outdated or memory.event_state != "outdated"):
                ranked.append(MemoryHit(memory, score, tuple(sorted(kinds))))
        return sorted(ranked, key=lambda hit: (-hit.rank_score, -hit.memory.created_at,
                                               hit.memory.memory_id))[:limit]
