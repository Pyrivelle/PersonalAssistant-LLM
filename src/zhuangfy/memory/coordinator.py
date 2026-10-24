"""M4 service orchestration: one owner for Qdrant Local and curated SQLite.

Use this only behind a trusted loopback transport. Person IDs are always
derived by resolving a verified account identity, not accepted from chat text.
"""

from __future__ import annotations

import threading
import json
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
from zhuangfy.relationship import RelationshipCore, RelationshipEvolution, RelationshipCandidate
from .vector import EmbeddingProvider, QdrantLocalIndex, VectorIndex


class MemoryCore:
    def __init__(self, root: Path, *, embedding: EmbeddingProvider,
                 index: VectorIndex | None = None, curator_model=None,
                 agent_id: str = "zhuangfy-main",
                 auto_qq_timeline: bool = False) -> None:
        if type(auto_qq_timeline) is not bool:
            raise ValueError("auto_qq_timeline must be boolean")
        self.auto_qq_timeline = auto_qq_timeline
        self.agent_id = agent_id
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = MemoryDatabase(self.root / "memory.sqlite3")
        self.identities = IdentityRepository(self.database)
        self.timeline = TimelineRepository(self.database, self.identities)
        self.relationship = RelationshipCore(self.database, self.identities)
        self.relationship_evolution = RelationshipEvolution(self.relationship)
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

    def onboard_qq(self, *, identity: dict, display_name: str) -> dict:
        """Trust-boundary call: only a QQ adapter with the client token may invoke.

        It creates an identity shell, never Timeline or curation consent.
        """
        person, created = self.identities.ensure_qq_person(
            self._identity(identity), display_name)
        return {
            "created": created,
            "display_name": person.display_name,
            "timeline_consent": self.identities.has_consent(person.person_id, "timeline"),
            "curation_consent": self.identities.has_consent(person.person_id, "curation"),
        }

    def receive(self, *, identity: dict, chat_id: str, message_id: str, content: str) -> dict:
        ident = self._identity(identity)
        conv = self.timeline.open_private(
            agent_id=self.agent_id, identity=ident, external_conversation_id=chat_id)
        entry = self.timeline.receive(
            conversation_id=conv.conversation_id, identity=ident,
            source_message_id=message_id, content=content)
        return {"conversation_id": conv.conversation_id, "entry_id": entry.entry_id}

    def relationship_snapshot(self, *, identity: dict) -> dict:
        """Private, verified client lookup. Unenrolled accounts get no relationship."""
        person = self._person(identity)
        self.identities.require_consent(person, "timeline")
        snap = self.relationship.snapshot(
            agent_id=self.agent_id, identity=self._identity(identity))
        if snap is None:
            return {"enabled": False, "tier": "unassigned", "hint": ""}
        # Never expose event text or untrusted memory content through the prompt.
        return {"enabled": True, "tier": snap.tier, "version": snap.version,
                "auto_growth": snap.auto_growth, "frozen": snap.frozen,
                "hint": snap.prompt_hint()}

    def relationship_apply(self, *, identity: dict, source_entry_id: str,
                           event_kind: str, confidence: float, evidence_quote: str,
                           expected_version: int) -> dict:
        """Candidate-only client submission; deterministic server adjudication."""
        candidate = RelationshipCandidate.parse(
            json.dumps({"event_kind": event_kind, "confidence": confidence,
                                     "evidence_quote": evidence_quote}),
            source_entry_id)
        snap = self.relationship_evolution.apply(
            agent_id=self.agent_id, identity=self._identity(identity),
            candidate=candidate, expected_version=expected_version)
        return {"version": snap.version, "tier": snap.tier}

    def admin_relationship_get(self, *, identity: dict) -> dict:
        """Trusted administrator can inspect disabled profiles as well."""
        import dataclasses
        ident = self._identity(identity)
        with self.database.connection() as db:
            person_id = self.relationship._identity(db, ident)
            row = db.execute("SELECT * FROM relationship_profiles WHERE agent_id=? AND person_id=?",
                             (self.agent_id, person_id)).fetchone()
            if row is None:
                return {"enrolled": False}
            snap = self.relationship._snapshot(row)
            return {"enrolled": True, "enabled": bool(row["enabled"]),
                    "profile": dataclasses.asdict(snap)}

    def admin_relationship_events(self, *, identity: dict, limit: int = 30) -> dict:
        import dataclasses
        ident = self._identity(identity)
        # All audit entries are identity and agent scoped; no private message body.
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid limit")
        with self.database.connection() as db:
            person_id = self.relationship._identity(db, ident)
            items = db.execute("""
                SELECT event_id,kind,source_entry_id,dimension,old_value,new_value,
                       occurred_at,detail FROM relationship_events
                WHERE agent_id=? AND person_id=?
                ORDER BY occurred_at DESC,event_id DESC LIMIT ?
            """, (self.agent_id, person_id, limit)).fetchall()
            return {"events": [dict(x) for x in items]}

    def admin_relationship_erase(self, *, identity: dict) -> dict:
        return {"erased": self.relationship.erase(
            agent_id=self.agent_id, identity=self._identity(identity))}

    def admin_relationship_enroll(self, *, identity: dict, initial_tier: str = "L0",
                                  allow_affection: bool = False) -> dict:
        import dataclasses
        snap = self.relationship.enroll(agent_id=self.agent_id,
                                        identity=self._identity(identity),
                                        initial_tier=initial_tier,
                                        allow_affection=allow_affection)
        return dataclasses.asdict(snap)

    def admin_relationship_correct(self, *, identity: dict, expected_version: int,
                                   changes: dict) -> dict:
        import dataclasses
        if not isinstance(changes, dict):
            raise ValueError("Invalid relationship changes")
        allowed = {"familiarity","trust","closeness","tier_ceiling","allow_affection",
                   "avoid_nicknames","tone","auto_growth","frozen","enabled",
                   "lock_familiarity","lock_trust","lock_closeness"}
        if not changes or set(changes) - allowed:
            raise ValueError("Unknown relationship correction")
        snap = self.relationship.correct(agent_id=self.agent_id,
                  identity=self._identity(identity), expected_version=expected_version,
                  **changes)
        return dataclasses.asdict(snap)

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
        ident = self._identity(identity)
        person_id = self._person(identity)
        self.identities.require_consent(person_id, "timeline")
        with self.database.connection() as db:
            conv = db.execute(
                """SELECT person_id, platform, channel_account_id
                   FROM private_conversations WHERE conversation_id=? AND agent_id=?""",
                (conversation_id, self.agent_id)).fetchone()
            if (not conv or conv["person_id"] != person_id
                    or conv["platform"] != ident.platform
                    or conv["channel_account_id"] != ident.channel_account_id):
                raise PermissionError("Conversation belongs to another channel identity")
        draft = self.timeline.generate_reply(
            conversation_id=conversation_id, in_reply_to=entry_id,
            source_message_id=reply_id, content=content)
        confirmed = self.timeline.set_reply_state(
            conversation_id=conversation_id, entry_id=draft.entry_id,
            new_state="delivered")
        # A lack of curation consent must never erase or prevent a valid
        # delivered reply already recorded to Timeline.
        try:
            job_id = self.curated.schedule_if_ready(conversation_id=conversation_id,
                                                    person_id=person_id)
        except PermissionError:
            job_id = None
        return {"entry_id": confirmed.entry_id, "state": confirmed.state,
                "reflection_job_id": job_id}

    def erase_self(self, *, identity: dict) -> dict:
        """Erase a person's new-core history and disable future retention.

        A unified Person can have several verified accounts: self-erasure
        necessarily affects all of that person's linked channels. Re-enable
        storage only after fresh, explicit administrator-reviewed consent.
        """
        person_id = self._person(identity)
        with self._index_lock:
            with self.database.write() as db:
                # Revoke both opt-ins in the SAME transaction as erasure. A
                # following QQ message must not silently recreate the memory.
                db.execute("""UPDATE memory_consents SET granted_at=NULL, revoked_at=strftime('%s','now')
                    WHERE person_id=? AND purpose IN ('timeline','curation')""", (person_id,))
                # FK cascade removes Timeline entries, reflection jobs,
                # evidence, curated memories, edited versions and links.
                db.execute("DELETE FROM private_conversations WHERE person_id=?", (person_id,))
                # Erase R1 relationship profile and audit events in the same transaction.
                db.execute("DELETE FROM relationship_profiles WHERE person_id=?", (person_id,))
                # Also cover any old/orphan curated rows without conversations.
                db.execute("DELETE FROM curated_memories WHERE person_id=?", (person_id,))
                db.execute("DELETE FROM reflection_jobs WHERE person_id=?", (person_id,))
            status = self.projector.sync()
        return {"erased": True, "retention_disabled": True,
                "index_repaired": status.failed == 0}

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
                "created": len(result.created_memory_ids), "index_failures": sync["failed"],
                "model_attempted": result.model_attempted,
                "error_type": result.error_type}

    def schedule_due_reflections(self) -> int:
        return self.curated.schedule_due()

    def list_people(self) -> list[dict]:
        with self.database.connection() as db:
            people = [dict(row) for row in db.execute(
                "SELECT person_id,display_name,created_at FROM persons ORDER BY created_at,person_id").fetchall()]
            identities = db.execute(
                "SELECT person_id,platform,channel_account_id,external_user_id "
                "FROM identities ORDER BY platform,channel_account_id,external_user_id").fetchall()
        by_person = {person["person_id"]: person for person in people}
        for person in people:
            person["identities"] = []
        for identity in identities:
            person = by_person.get(identity["person_id"])
            if person is not None:
                person["identities"].append({
                    "platform": identity["platform"],
                    "channel_account_id": identity["channel_account_id"],
                    "external_user_id": identity["external_user_id"],
                })
        return people

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
