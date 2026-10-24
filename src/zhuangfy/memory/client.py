"""Small synchronous loopback Memory Service client for trusted Python adapters.

Instantiate only when explicit environment opt-in is set in the caller.
Never place bearer tokens or private content in logs or exception strings.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener


class MemoryClientError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class MemoryChannel:
    platform: str
    channel_account_id: str
    external_user_id: str

    def as_payload(self) -> dict:
        return asdict(self)


class MemoryClient:
    def __init__(self, *, url: str = "http://127.0.0.1:8769",
                 token: str, timeout: float = 2.0):
        parsed = urlparse(url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError("MemoryClient only accepts a local loopback HTTP URL")
        if not 32 <= len(token) <= 4096 or token != token.strip():
            raise ValueError("Memory client token must be at least 32 chars")
        if not 0.1 <= timeout <= 30:
            raise ValueError("Timeout must be 0.1..30s")
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._opener = build_opener(ProxyHandler({}))

    @classmethod
    def from_environment(cls, *, enabled_var: str, admin: bool = False) -> "MemoryClient | None":
        if os.environ.get(enabled_var, "").strip() != "1":
            return None
        token_name = "ZHUANGFY_MEMORY_ADMIN_TOKEN" if admin else "ZHUANGFY_MEMORY_TOKEN"
        token = os.environ.get(token_name, "")
        if not token:
            raise ValueError(f"{token_name} is required for enabled memory integration")
        return cls(url=os.environ.get("ZHUANGFY_MEMORY_URL", "http://127.0.0.1:8769"),
                   token=token)

    def request(self, route: str, payload: dict) -> dict:
        if not route.startswith("/v1/") or ".." in route or "?" in route:
            raise ValueError("Invalid memory route")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(data) > 18000:
            raise ValueError("Request too large")
        req = Request(self.url + route, data=data, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.token,
        })
        try:
            # Curation may spend up to 60 seconds on the model request. Keep
            # ordinary recall latency short, but let an explicit admin run
            # receive the actual result instead of timing out after 2 seconds.
            request_timeout = max(self.timeout, 150.0) if route == "/v1/admin/reflect-one" else self.timeout
            with self._opener.open(req, timeout=request_timeout) as response:
                data = response.read(2_000_000)
            result = json.loads(data)
            if not isinstance(result, dict) or "result" not in result:
                raise MemoryClientError(502, "Unexpected memory service response")
            return result["result"]
        except HTTPError as e:
            raise MemoryClientError(e.code, "Memory service rejected request") from None
        except (URLError, TimeoutError, ConnectionError):
            raise MemoryClientError(503, "Memory service unavailable") from None

    def onboard_qq(self, identity: MemoryChannel, display_name: str) -> dict:
        """Register a QQ identity after a trusted OneBot private-message event.

        This endpoint never grants consent to save conversation content.
        """
        return self.request("/v1/onboard-qq", {
            "identity": identity.as_payload(), "display_name": display_name,
        })

    def task_recall(self, identity: MemoryChannel, query: str,
                    limit: int = 2, exclude_entry_id: str = "") -> dict:
        return self.request("/v1/task-recall", {
            "identity": identity.as_payload(), "query": query, "limit": limit,
            "exclude_entry_id": exclude_entry_id,
        })

    def task_state(self, identity: MemoryChannel, task_id: str,
                   limit: int = 15) -> dict:
        return self.request("/v1/task-state", {
            "identity": identity.as_payload(), "task_id": task_id, "limit": limit,
        })

    def admin_task_event_review(self, identity: MemoryChannel, *,
                                event_id: str, action: str) -> dict:
        return self.request("/v1/admin/task-event-review", {
            "identity": identity.as_payload(), "event_id": event_id, "action": action,
        })

    def admin_task_event_correct(self, identity: MemoryChannel, *,
                                 task_id: str, source_entry_id: str,
                                 supersedes_event_id: str, reported_state: str) -> dict:
        return self.request("/v1/admin/task-event-correct", {
            "identity": identity.as_payload(), "task_id": task_id,
            "source_entry_id": source_entry_id,
            "supersedes_event_id": supersedes_event_id,
            "reported_state": reported_state,
        })

    def task_origin(self, identity: MemoryChannel, task_id: str) -> dict:
        return self.request("/v1/task-origin", {
            "identity": identity.as_payload(), "task_id": task_id,
        })

    def admin_bind_goal_origin(self, identity: MemoryChannel, *, task_id: str,
                               owner_scope: str, source_entry_id: str) -> dict:
        return self.request("/v1/admin/bind-goal-origin", {
            "identity": identity.as_payload(), "task_id": task_id,
            "owner_scope": owner_scope, "source_entry_id": source_entry_id,
        })

    def receive(self, identity: MemoryChannel, *, chat_id: str,
                message_id: str, content: str) -> dict:
        return self.request("/v1/receive", {
            "identity": identity.as_payload(), "chat_id": chat_id,
            "message_id": message_id, "content": content,
        })

    def recall(self, identity: MemoryChannel, query: str, limit: int = 5) -> dict:
        return self.request("/v1/recall", {
            "identity": identity.as_payload(), "query": query, "limit": limit,
        })

    def complete_reply(self, identity: MemoryChannel, *, conversation_id: str,
                       entry_id: str, reply_id: str, content: str) -> dict:
        return self.request("/v1/complete-reply", {
            "identity": identity.as_payload(),
            "conversation_id": conversation_id, "entry_id": entry_id,
            "reply_id": reply_id, "content": content,
        })

    def relationship_snapshot(self, identity: MemoryChannel) -> dict:
        return self.request("/v1/relationship/snapshot", {"identity": identity.as_payload()})

    def relationship_apply(self, identity: MemoryChannel, *,
                           source_entry_id: str, event_kind: str, confidence: float,
                           evidence_quote: str, expected_version: int) -> dict:
        return self.request("/v1/relationship/apply", {
            "identity": identity.as_payload(), "source_entry_id": source_entry_id,
            "event_kind": event_kind, "confidence": confidence,
            "evidence_quote": evidence_quote, "expected_version": expected_version,
        })

    def relationship_get(self, identity: MemoryChannel) -> dict:
        return self.request("/v1/admin/relationship/get", {
            "identity": identity.as_payload()})

    def relationship_events(self, identity: MemoryChannel, limit: int = 30) -> dict:
        return self.request("/v1/admin/relationship/events", {
            "identity": identity.as_payload(), "limit": limit})

    def relationship_erase(self, identity: MemoryChannel) -> dict:
        return self.request("/v1/admin/relationship/erase", {
            "identity": identity.as_payload()})

    def relationship_enroll(self, identity: MemoryChannel, *,
                            initial_tier: str = "L0",
                            allow_affection: bool = False) -> dict:
        return self.request("/v1/admin/relationship/enroll", {
            "identity": identity.as_payload(), "initial_tier": initial_tier,
            "allow_affection": allow_affection,
        })

    def relationship_correct(self, identity: MemoryChannel, *,
                             expected_version: int, changes: dict) -> dict:
        return self.request("/v1/admin/relationship/correct", {
            "identity": identity.as_payload(), "expected_version": expected_version,
            "changes": changes,
        })

    def erase_self(self, identity: MemoryChannel) -> dict:
        return self.request("/v1/erase-self", {"identity": identity.as_payload()})

    # Administrative endpoints require an independently configured admin token.
    # Never instantiate an admin client in SnowLuma, Qt chat worker or browser.
    def admin_create_person(self, display_name: str) -> dict:
        return self.request("/v1/admin/create-person", {"display_name": display_name})

    def admin_link(self, person_id: str, identity: MemoryChannel,
                   verification_reference: str) -> dict:
        return self.request("/v1/admin/link", {
            "person_id": person_id, "identity": identity.as_payload(),
            "verification_reference": verification_reference,
        })

    def admin_consent(self, person_id: str, purpose: str, granted: bool) -> dict:
        return self.request("/v1/admin/consent", {
            "person_id": person_id, "purpose": purpose, "granted": granted,
        })

    def list_people(self) -> list[dict]:
        return self.request("/v1/admin/list-people", {})["people"]

    def list_memories(self, person_id: str) -> list[dict]:
        return self.request("/v1/admin/list-memories", {"person_id": person_id})["memories"]

    def sync_index(self) -> dict:
        return self.request("/v1/admin/sync-index", {})

    def reflect_one(self) -> dict:
        return self.request("/v1/admin/reflect-one", {})

    def get_evidence(self, person_id: str, memory_id: str) -> dict:
        return self.request("/v1/admin/evidence", {
            "person_id": person_id, "memory_id": memory_id})

    def correct_memory(self, person_id: str, memory_id: str,
                       new_content: str, reason: str) -> dict:
        return self.request("/v1/admin/correct", {
            "person_id": person_id, "memory_id": memory_id,
            "new_content": new_content, "reason": reason,
        })

    def forget_memory(self, person_id: str, memory_id: str) -> dict:
        return self.request("/v1/admin/forget-memory", {
            "person_id": person_id, "memory_id": memory_id})

    def forget_person(self, person_id: str) -> dict:
        return self.request("/v1/admin/forget-person", {"person_id": person_id})

    def link_episodes(self, person_id: str, source_memory_id: str,
                      target_memory_id: str, relation: str) -> dict:
        return self.request("/v1/admin/link-episodes", {
            "person_id": person_id, "source_memory_id": source_memory_id,
            "target_memory_id": target_memory_id, "relation": relation,
        })

    def list_jobs(self, person_id: str) -> list[dict]:
        return self.request("/v1/admin/list-jobs", {"person_id": person_id})["jobs"]

    def retry_job(self, person_id: str, job_id: str) -> dict:
        return self.request("/v1/admin/retry-job", {
            "person_id": person_id, "job_id": job_id})
