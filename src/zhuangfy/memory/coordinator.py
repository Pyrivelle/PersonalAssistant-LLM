"""M4 service orchestration: one owner for Qdrant Local and curated SQLite.

Use this only behind a trusted loopback transport. Person IDs are always
derived by resolving a verified account identity, not accepted from chat text.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .curator import MemoryCurator
from .admin_store import MemoryAdminStore
from .curator_store import CuratorStore
from .database import MemoryDatabase
from .identity import ChannelIdentity, IdentityProof, IdentityRepository
from .projector import CuratedIndexProjector
from .retrieval import HybridMemoryRetriever
from .timeline import TimelineRepository
from .vector import EmbeddingProvider, QdrantLocalIndex, VectorIndex


class MemoryCore:
    def __init__(self, root: Path, *, embedding: EmbeddingProvider,
                 index: VectorIndex | None = None, curator_model=None,
                 agent_id: str = "zhuangfy-main") -> None:
        self.agent_id = agent_id
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = MemoryDatabase(self.root / "memory.sqlite3")
        self.identities = IdentityRepository(self.database)
        self.timeline = TimelineRepository(self.database, self.identities)
        self.curated = CuratorStore(self.database, self.identities, self.timeline)
        self.projector = CuratedIndexProjector(
            self.database, self.identities, self.curated, embedding,
            index if index is not None else QdrantLocalIndex(
                self.root / "qdrant", model_id=embedding.model_id, dimension=embedding.dimension),
        )
        self.retriever = HybridMemoryRetriever(self.projector, self.curated, self.identities)
        self.admin = MemoryAdminStore(self.database, self.identities, self.curated)
        self.curator = MemoryCurator(self.curated, curator_model) if curator_model else None
        self._index_lock = threading.RLock()

    @staticmethod
    def _identity(data: dict) -> ChannelIdentity:
        if not isinstance(data, dict) or set(data) != {
                "platform", "channel_account_id", "external_user_id"}:
            raise ValueError("Channel identity requires exactly three fields")
        if not all(isinstance(v, str) and v.strip() for v in data.values()):
            raise ValueError("Channel identity fields must be nonempty strings")
        return ChannelIdentity(**data)

    def _person(self, claim: dict) -> str:
        person_id = self.identities.resolve(self._identity(claim))
        if person_id is None:
            raise PermissionError("Unknown or unverified identity")
        return person_id

    def receive(self, *, identity: dict, chat_id: str, message_id: str, content: str) -> dict:
        ident = self._identity(identity)
        conv = self.timeline.open_private(
            agent_id=self.agent_id, identity=ident, external_conversation_id=chat_id)
        entry = self.timeline.receive(
            conversation_id=conv.conversation_id, identity=ident,
            source_message_id=message_id, content=content)
        return {"conversation_id": conv.conversation_id, "entry_id": entry.entry_id}

    def recall(self, *, identity: dict, query: str, limit: int = 5) -> dict:
        person_id = self._person(identity)
        with self._index_lock:
            hits = self.retriever.search(
                agent_id=self.agent_id, person_id=person_id, query=query, limit=limit)
        return {
            "memories": [{
                "memory_id": h.memory.memory_id,
                "content": h.memory.content,
                "kind": h.memory.kind,
                "event_state": h.memory.event_state,
                "source_entry_ids": list(h.memory.source_entry_ids),
                "score": h.rank_score,
            } for h in hits],
        }

    def complete_reply(self, *, identity: dict, conversation_id: str,
                       entry_id: str, reply_id: str, content: str) -> dict:
        """Trusted transport MUST have confirmed delivery before invoking.

        If the caller crashes after generating a draft, a later retry with the
        same reply_id can commit delivery without creating duplicate entries.
        """
        person_id = self._person(identity)
        self.identities.require_consent(person_id, "timeline")
        with self.database.connection() as db:
            conv = db.execute(
                "SELECT person_id FROM private_conversations WHERE conversation_id=? AND agent_id=?",
                (conversation_id, self.agent_id)).fetchone()
            if not conv or conv["person_id"] != person_id:
                raise PermissionError("Conversation belongs to another identity")
        draft = self.timeline.generate_reply(
            conversation_id=conversation_id, in_reply_to=entry_id,
            source_message_id=reply_id, content=content)
        confirmed = self.timeline.set_reply_state(
            conversation_id=conversation_id, entry_id=draft.entry_id,
            new_state="delivered")
        # A lack of curation consent must never erase or prevent a valid
        # delivered reply already recorded to Timeline.
        try:
            job_id = self.curated.schedule(conversation_id=conversation_id,
                                           person_id=person_id)
        except PermissionError:
            job_id = None
        return {"entry_id": confirmed.entry_id, "state": confirmed.state,
                "reflection_job_id": job_id}

    def erase_self(self, *, identity: dict) -> dict:
        person_id = self._person(identity)
        self.timeline.erase_private_history(person_id=person_id)
        self.curated.erase_person(person_id)
        with self._index_lock:
            status = self.projector.sync()
        return {"erased": True, "index_repaired": status.failed == 0}

    def project(self) -> dict:
        with self._index_lock:
            p = self.projector.sync()
        return {"indexed": p.indexed, "removed": p.removed, "failed": p.failed}

    def reflect_one(self) -> dict:
        if self.curator is None:
            raise RuntimeError("Curator model is not configured; remote LLM is opt-in")
        result = self.curator.process_next()
        if not result:
            return {"status": "idle"}
        sync = self.project()
        return {"status": result.status, "job_id": result.job_id,
                "created": len(result.created_memory_ids), "index_failures": sync["failed"]}

    def list_people(self) -> list[dict]:
        with self.database.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT person_id,display_name,created_at FROM persons ORDER BY created_at,person_id").fetchall()]

    def admin_create_person(self, display_name: str) -> dict:
        p = self.identities.create_person(display_name)
        return {"person_id": p.person_id, "display_name": p.display_name}

    def admin_link(self, *, person_id: str, identity: dict,
                   verification_reference: str) -> dict:
        if not verification_reference.strip():
            raise ValueError("A verifiable approval reference is required")
        self.identities.link_verified(
            person_id, self._identity(identity),
            IdentityProof("trusted_admin", verification_reference))
        return {"linked": True}

    def admin_consent(self, *, person_id: str, purpose: str, granted: bool) -> dict:
        if type(granted) is not bool:
            raise ValueError("Consent must be explicit boolean")
        self.identities.set_consent(person_id, purpose, granted)
        return {"consent": self.identities.has_consent(person_id, purpose)}

    def list_memories(self, *, person_id: str) -> list[dict]:
        if self.identities.get_person(person_id) is None:
            raise ValueError("Unknown person")
        return [{
            "memory_id": m.memory_id, "content": m.content, "kind": m.kind,
            "event_state": m.event_state, "status": m.status,
            "source_entry_ids": list(m.source_entry_ids),
            "created_at": m.created_at, "supersedes_memory_id": m.supersedes_memory_id,
        } for m in self.curated.current_memories(agent_id=self.agent_id, person_id=person_id)]

    def admin_evidence(self, *, person_id: str, memory_id: str) -> dict:
        return self.admin.evidence(agent_id=self.agent_id, person_id=person_id, memory_id=memory_id)

    def admin_correct(self, *, person_id: str, memory_id: str, new_content: str,
                      reason: str) -> dict:
        with self._index_lock:
            result = self.admin.correct(
                agent_id=self.agent_id, person_id=person_id, memory_id=memory_id,
                new_content=new_content, reason=reason)
            if result.get("changed"):
                result["index"] = self.project()
        return result

    def admin_forget_memory(self, *, person_id: str, memory_id: str) -> dict:
        with self._index_lock:
            result = self.admin.forget_memory(agent_id=self.agent_id,
                                             person_id=person_id, memory_id=memory_id)
            result["index"] = self.project()
        return result

    def admin_forget_person(self, *, person_id: str) -> dict:
        with self._index_lock:
            result = self.admin.forget_person(person_id=person_id)
            result["index"] = self.project()
        return result

    def admin_link_episodes(self, *, person_id: str, source_memory_id: str,
                            target_memory_id: str, relation: str) -> dict:
        return self.admin.link_episodes(
            agent_id=self.agent_id, person_id=person_id,
            source_memory_id=source_memory_id, target_memory_id=target_memory_id,
            relation=relation)

    def admin_list_jobs(self, *, person_id: str) -> dict:
        return {"jobs": self.admin.list_jobs(person_id=person_id)}

    def admin_retry_job(self, *, person_id: str, job_id: str) -> dict:
        return self.admin.retry_job(person_id=person_id, job_id=job_id)

    def close(self) -> None:
        with self._index_lock:
            self.projector.close()
