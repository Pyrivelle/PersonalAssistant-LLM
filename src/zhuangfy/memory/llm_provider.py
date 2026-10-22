"""Explicit opt-in Chat Completions adapter for M2 MemoryCurator.

Do not automatically reuse the user's reply-stage API key, especially when
failover may route a private transcript to another provider. Configure a
separate memory-curation model and grant explicit remote-data approval.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener


@dataclass
class ChatCompletionsCurator:
    base_url: str
    model: str
    key: str
    timeout: float = 60.0

    def __post_init__(self):
        p = urlparse(self.base_url)
        if (p.scheme not in {"http", "https"} or not p.netloc or p.query or p.fragment
                or p.username or p.password):
            raise ValueError("Invalid curator model endpoint")
        if p.scheme == "http" and p.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("Unencrypted remote curator endpoints are forbidden")
        if not self.model.strip() or len(self.key) < 8:
            raise ValueError("Model and curator API key required")
        if p.hostname not in {"localhost", "127.0.0.1"} and os.environ.get(
                "ZHUANGFY_MEMORY_ALLOW_REMOTE") != "1":
            raise PermissionError("Memory remote API sharing requires explicit opt-in")
        self.base_url = self.base_url.rstrip("/")

    def extract(self, *, system_prompt: str, payload: dict) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ], "stream": False,
            "temperature": 0.1,
        }, ensure_ascii=False).encode("utf-8")
        if len(body) > 50000:
            raise ValueError("Curator model payload too large")
        req = Request(self.base_url + "/chat/completions", data=body, method="POST",
                      headers={"Authorization": "Bearer " + self.key,
                               "Content-Type": "application/json"})
        opener = build_opener(ProxyHandler({}))
        with opener.open(req, timeout=self.timeout) as res:
            raw = res.read(500_001)
        if len(raw) > 500000:
            raise ValueError("Curator model response too large")
        data = json.loads(raw)
        value = data["choices"][0]["message"]["content"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Curator model response was empty")
        return value.strip()


def configured_curator():
    if os.environ.get("ZHUANGFY_MEMORY_REFLECT") != "1":
        return None
    return ChatCompletionsCurator(
        base_url=os.environ["ZHUANGFY_MEMORY_LLM_BASE_URL"],
        model=os.environ["ZHUANGFY_MEMORY_LLM_MODEL"],
        key=os.environ["ZHUANGFY_MEMORY_LLM_API_KEY"],
    )
