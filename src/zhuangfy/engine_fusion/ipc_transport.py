"""Launch and supervise the Hybrid worker without sharing QQ credentials.

This only validates the stdio transport. Native Maisaka runtime capability is
explicitly checked and must be TRUE before the host forwards user messages.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from uuid import uuid4

from .ipc_contract import Envelope, ProtocolError, inbound_from_host
from .worker import UPSTREAM_SHA


class HybridTransportUnavailable(RuntimeError):
    pass


class HybridWorkerClient:
    def __init__(self, process: asyncio.subprocess.Process, *, timeout: float):
        self.process = process
        self.timeout = timeout
        self.native_ready = False
        self.transport_ready = False
        self._lock = asyncio.Lock()

    @classmethod
    async def launch(cls, *, timeout: float = 5.0,
                     python: str | None = None) -> "HybridWorkerClient":
        if not 0.5 <= timeout <= 30:
            raise ValueError("invalid worker timeout")
        root = Path(__file__).resolve().parents[3]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(root / "src") + os.pathsep + environment.get("PYTHONPATH", "")
        proc = await asyncio.create_subprocess_exec(
            python or sys.executable, "-u", "-m", "zhuangfy.engine_fusion.worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, cwd=root, env=environment,
            limit=20_001,
        )
        client = cls(proc, timeout=timeout)
        try:
            hello = await client._exchange(
                Envelope("hello", uuid4().hex, "", {"upstream_sha": UPSTREAM_SHA}),
                response_kind="hello_ack",
            )
            if (not hello.payload.get("transport_ready")
                    or hello.payload.get("upstream_sha") != UPSTREAM_SHA):
                raise HybridTransportUnavailable("worker handshake or upstream pin failed")
            client.transport_ready = True
            client.native_ready = hello.payload.get("native_runtime_ready") is True
        except BaseException:
            await client.close()
            raise
        return client

    async def _exchange(self, envelope: Envelope, *, response_kind: str) -> Envelope:
        async with self._lock:
            if self.process.returncode is not None:
                raise HybridTransportUnavailable("worker already terminated")
            if self.process.stdin is None or self.process.stdout is None:
                raise HybridTransportUnavailable("missing worker pipes")
            try:
                self.process.stdin.write(envelope.to_line())
                await asyncio.wait_for(self.process.stdin.drain(), timeout=self.timeout)
                data = await asyncio.wait_for(
                    self.process.stdout.readline(), timeout=self.timeout)
                result = Envelope.from_line(data)
            except (OSError, EOFError, asyncio.TimeoutError, ProtocolError) as exc:
                raise HybridTransportUnavailable("invalid or unresponsive worker") from exc
            if (result.request_id != envelope.request_id
                    or result.kind != response_kind
                    or result.conversation_id != envelope.conversation_id):
                raise HybridTransportUnavailable("unmatched worker response")
            return result

    async def submit(self, context, *, event_id: str, message_id: str,
                     components: list[dict]):
        """Never swallow QQ messages before native planner is actually ready."""
        if not self.native_ready:
            raise HybridTransportUnavailable(
                "native Maisaka Planner/Reply/Wait is not connected; no QQ event forwarded"
            )
        envelope = inbound_from_host(
            context, event_id=event_id, message_id=message_id, components=components)
        # Native worker will advertise ready only once this method exists.
        return await self._exchange(envelope, response_kind="accepted")

    async def close(self) -> None:
        if self.process.returncode is not None:
            return
        try:
            await self._exchange(Envelope("shutdown", uuid4().hex, "", {}),
                                 response_kind="bye")
            await asyncio.wait_for(self.process.wait(), timeout=self.timeout)
        except (HybridTransportUnavailable, asyncio.TimeoutError):
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
