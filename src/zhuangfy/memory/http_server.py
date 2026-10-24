"""Loopback-only authenticated memory HTTP service (M4).

Start: python -m zhuangfy.memory.http_server
A sole server process owns Qdrant Local. No network listener starts on import.
API tokens and optional cloud Curator settings are supplied only via env vars.

Security: the client token authenticates a trusted *channel adapter*, not an
arbitrary QQ user. The server resolves external identity against administrator-
approved mappings. Do not expose this HTTP listener outside localhost.
"""

from __future__ import annotations

import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .coordinator import MemoryCore
from .llm_provider import configured_curator
from .vector import FastEmbedProvider

MAX_REQUEST_BYTES = 18_000
PUBLIC_ENDPOINTS = frozenset({
    "/v1/onboard-qq", "/v1/receive", "/v1/recall", "/v1/complete-reply", "/v1/erase-self",
    "/v1/relationship/snapshot", "/v1/relationship/apply",
})
ADMIN_ENDPOINTS = frozenset({
    "/v1/admin/create-person", "/v1/admin/link", "/v1/admin/consent",
    "/v1/admin/list-people", "/v1/admin/list-memories",
    "/v1/admin/sync-index", "/v1/admin/reflect-one",
    "/v1/admin/evidence", "/v1/admin/correct", "/v1/admin/forget-memory",
    "/v1/admin/forget-person", "/v1/admin/link-episodes",
    "/v1/admin/list-jobs", "/v1/admin/retry-job",
    "/v1/admin/relationship/enroll", "/v1/admin/relationship/correct",
    "/v1/admin/relationship/get", "/v1/admin/relationship/events",
    "/v1/admin/relationship/erase",
})


def _token(name: str) -> str:
    value = os.environ.get(name, "")
    if len(value) < 32 or value != value.strip():
        raise RuntimeError(f"Set {name} to a unique random token of at least 32 characters")
    return value


def build_handler(core: MemoryCore, *, client_token: str, admin_token: str):
    if (len(client_token) < 32 or len(admin_token) < 32
            or hmac.compare_digest(client_token, admin_token)):
        raise ValueError("Client and admin tokens must be distinct strong secrets")

    class Handler(BaseHTTPRequestHandler):
        server_version = "ZhuangFYMemory/0.1"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args) -> None:
            # Don't log full paths, headers, body or private memories.
            return

        def _send(self, code: int, data: dict) -> None:
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            self._send(404, {"error": "Not found"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            role = "admin" if path in ADMIN_ENDPOINTS else (
                "client" if path in PUBLIC_ENDPOINTS else None)
            if not role:
                self._send(404, {"error": "Not found"})
                return
            authorization = self.headers.get("Authorization", "")
            required = admin_token if role == "admin" else client_token
            if not hmac.compare_digest(authorization, "Bearer " + required):
                self._send(401, {"error": "Unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 2 or size > MAX_REQUEST_BYTES:
                    raise ValueError("Invalid request length")
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError("JSON object required")
                if path == "/v1/onboard-qq":
                    result = core.onboard_qq(**payload)
                elif path == "/v1/receive":
                    result = core.receive(**payload)
                elif path == "/v1/recall":
                    result = core.recall(**payload)
                elif path == "/v1/complete-reply":
                    result = core.complete_reply(**payload)
                elif path == "/v1/erase-self":
                    result = core.erase_self(**payload)
                elif path == "/v1/relationship/snapshot":
                    result = core.relationship_snapshot(**payload)
                elif path == "/v1/relationship/apply":
                    result = core.relationship_apply(**payload)
                elif path == "/v1/admin/relationship/enroll":
                    result = core.admin_relationship_enroll(**payload)
                elif path == "/v1/admin/relationship/correct":
                    result = core.admin_relationship_correct(**payload)
                elif path == "/v1/admin/relationship/get":
                    result = core.admin_relationship_get(**payload)
                elif path == "/v1/admin/relationship/events":
                    result = core.admin_relationship_events(**payload)
                elif path == "/v1/admin/relationship/erase":
                    result = core.admin_relationship_erase(**payload)
                elif path == "/v1/admin/create-person":
                    result = core.admin_create_person(**payload)
                elif path == "/v1/admin/link":
                    result = core.admin_link(**payload)
                elif path == "/v1/admin/consent":
                    result = core.admin_consent(**payload)
                elif path == "/v1/admin/list-people":
                    result = {"people": core.list_people()}
                elif path == "/v1/admin/list-memories":
                    result = {"memories": core.list_memories(**payload)}
                elif path == "/v1/admin/sync-index":
                    result = core.project()
                elif path == "/v1/admin/evidence":
                    result = core.admin_evidence(**payload)
                elif path == "/v1/admin/correct":
                    result = core.admin_correct(**payload)
                elif path == "/v1/admin/forget-memory":
                    result = core.admin_forget_memory(**payload)
                elif path == "/v1/admin/forget-person":
                    result = core.admin_forget_person(**payload)
                elif path == "/v1/admin/link-episodes":
                    result = core.admin_link_episodes(**payload)
                elif path == "/v1/admin/list-jobs":
                    result = core.admin_list_jobs(**payload)
                elif path == "/v1/admin/retry-job":
                    result = core.admin_retry_job(**payload)
                else:
                    result = core.reflect_one()
                self._send(200, {"result": result})
            except PermissionError:
                self._send(403, {"error": "Access denied or consent required"})
            except (ValueError, TypeError, KeyError):
                self._send(400, {"error": "Invalid request"})
            except Exception:
                # Never reflect exception messages containing model data,
                # account identifiers, secrets or file paths to clients.
                self._send(503, {"error": "Memory service temporarily unavailable"})

    return Handler


def serve(core: MemoryCore, *, host: str = "127.0.0.1", port: int = 8769,
          client_token: str, admin_token: str) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("Memory service must bind only to IPv4 loopback")
    if not 1024 <= port <= 65535:
        raise ValueError("Port must be in 1024..65535")
    return ThreadingHTTPServer(
        (host, port),
        build_handler(core, client_token=client_token, admin_token=admin_token),
    )


def main() -> None:
    client_token = _token("ZHUANGFY_MEMORY_TOKEN")
    admin_token = _token("ZHUANGFY_MEMORY_ADMIN_TOKEN")
    root = Path(os.environ.get("ZHUANGFY_MEMORY_DIR", "")).expanduser() if os.environ.get(
        "ZHUANGFY_MEMORY_DIR") else Path(__file__).resolve().parents[3] / "data" / "memory_core"
    port = int(os.environ.get("ZHUANGFY_MEMORY_PORT", "8769"))
    embedding = FastEmbedProvider(cache_dir=root / "embedding_cache")
    auto_capture = os.environ.get("ZHUANGFY_MEMORY_QQ_AUTO_TIMELINE", "0")
    if auto_capture not in {"0", "1"}:
        raise ValueError("ZHUANGFY_MEMORY_QQ_AUTO_TIMELINE must be 0 or 1")
    core = MemoryCore(root, embedding=embedding, curator_model=configured_curator(),
                      auto_qq_timeline=(auto_capture == "1"))
    stop_worker = threading.Event()

    def background() -> None:
        while not stop_worker.wait(8):
            try:
                if core.curator is not None:
                    core.schedule_due_reflections()
                    core.reflect_one()
                else:
                    core.project()
            except Exception:
                # Never print model payloads, tokens or private dialogue.
                print("Memory background job failed (see local administrator checks)")

    worker = threading.Thread(target=background, name="zhuangfy-memory-worker", daemon=True)
    try:
        # Explicitly sync only after starting on a single owner process.
        core.project()
        server = serve(core, port=port, client_token=client_token, admin_token=admin_token)
        worker.start()
        print(f"ZhuangFY Memory Service listening only on 127.0.0.1:{port}")
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        stop_worker.set()
        if worker.is_alive():
            worker.join(timeout=5)
        core.close()


if __name__ == "__main__":
    main()
