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
            with self._opener.open(req, timeout=self.timeout) as response:
                data = response.read(2_000_000)
            result = json.loads(data)
            if not isinstance(result, dict) or "result" not in result:
                raise MemoryClientError(502, "Unexpected memory service response")
            return result["result"]
        except HTTPError as e:
            raise MemoryClientError(e.code, "Memory service rejected request") from None
        except (URLError, TimeoutError, ConnectionError):
            raise MemoryClientError(503, "Memory service unavailable") from None

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
