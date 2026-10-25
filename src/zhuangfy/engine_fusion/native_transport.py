"""Duplex host for the *real* upstream Maisaka worker.

A native LLM reply becomes a QQ message only when an authenticated ZhuangFY
send callback returns an actual OneBot platform message ID. The host never
synthesizes ACKs, and only a single verified QQ private conversation can bind
to each worker instance.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from typing import Awaitable, Callable
from uuid import uuid4

from .ipc_contract import Envelope, ProtocolError, inbound_from_host
from .ipc_contract import VERSION
from .ipc_transport import HybridTransportUnavailable
from .worker import UPSTREAM_SHA

ROOT = Path(__file__).resolve().parents[3]
DeliverToQQ = Callable[[str, str, str, str], Awaitable[str]]
TelemetryCallback = Callable[[str, dict], Awaitable[None]]


class NativeMaisakaClient:
    def __init__(self, process: asyncio.subprocess.Process, delivery: DeliverToQQ,
                 telemetry: TelemetryCallback | None = None):
        self.process = process
        self.delivery = delivery
        self.telemetry = telemetry
        self.ready = False
        self.conversation = ""
        self.pending: dict[str, asyncio.Future[Envelope]] = {}
        self._writer_lock = asyncio.Lock()
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._delivery_tasks: set[asyncio.Task] = set()

    @classmethod
    async def launch(cls, delivery: DeliverToQQ, *, source: Path, instance: Path,
                     python: Path, handshake_timeout: float = 120,
                     telemetry: TelemetryCallback | None = None) -> "NativeMaisakaClient":
        if (not source.is_dir() or not instance.is_dir()
                or not (source / "src/maisaka/runtime.py").is_file()
                or not (instance / "config/bot_config.toml").is_file()
                or not (instance / "config/model_config.toml").is_file()
                or not python.is_file()):
            raise HybridTransportUnavailable("MaiBot native runtime or isolated config missing")
        if not 5 <= handshake_timeout <= 180:
            raise ValueError("invalid initialization timeout")
        # Verify the exact submodule revision. No implicit upstream updates.
        import subprocess
        git = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True)
        if git.stdout.strip() != UPSTREAM_SHA:
            raise HybridTransportUnavailable("pinned Maisaka submodule SHA drift")
        env = os.environ.copy()
        env["MAIBOT_INSTANCE_DIR"] = str(instance.resolve())
        env["ZHUANGFY_MAIBOT_SOURCE"] = str(source.resolve())
        env["PYTHONPATH"] = str(source.resolve()) + os.pathsep + str(ROOT / "src")
        proc = await asyncio.create_subprocess_exec(
            str(python.resolve()), "-u", "-m", "zhuangfy.engine_fusion.native_worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, cwd=instance,
            env=env, limit=20_001,
        )
        client = cls(proc, delivery, telemetry)
        try:
            hello = await client.request(
                Envelope("hello", uuid4().hex, "", {"upstream_sha": UPSTREAM_SHA}),
                timeout=handshake_timeout)
            if (hello.kind != "hello_ack"
                    or hello.payload.get("protocol") != VERSION
                    or hello.payload.get("native_runtime_ready") is not True
                    or hello.payload.get("upstream_sha") != UPSTREAM_SHA):
                raise HybridTransportUnavailable("native Maisaka startup not ready")
            client.ready = True
            client.image_instance = instance
            return client
        except BaseException:
            await client.close()
            raise

    async def _write(self, event: Envelope) -> None:
        async with self._writer_lock:
            if self.process.returncode is not None or self.process.stdin is None:
                raise HybridTransportUnavailable("native worker exited")
            self.process.stdin.write(event.to_line())
            await self.process.stdin.drain()

    async def request(self, event: Envelope, *, timeout: float = 30) -> Envelope:
        if not 0.1 <= timeout <= 180:
            raise ValueError("invalid IPC deadline")
        if event.request_id in self.pending:
            raise ProtocolError("duplicate request ID")
        future = asyncio.get_running_loop().create_future()
        self.pending[event.request_id] = future
        try:
            await self._write(event)
            result = await asyncio.wait_for(future, timeout=timeout)
            if result.kind == "error":
                raise HybridTransportUnavailable(str(result.payload.get("reason", "native_error")))
            return result
        finally:
            self.pending.pop(event.request_id, None)

    async def _reader_loop(self) -> None:
        try:
            while self.process.stdout is not None:
                data = await self.process.stdout.readline()
                if not data:
                    break
                packet = Envelope.from_line(data)
                if packet.kind == "telemetry":
                    if (self.ready and packet.conversation_id == self.conversation
                            and self.telemetry is not None):
                        try:
                            await self.telemetry(packet.conversation_id, packet.payload)
                        except (OSError, RuntimeError, ValueError):
                            pass
                    continue
                if packet.kind == "outbound":
                    if not self.ready or packet.conversation_id != self.conversation:
                        # Fail closed. No messages to wrong/unknown recipients.
                        await self._ack(packet, platform_id="")
                        continue
                    task = asyncio.create_task(self._deliver(packet))
                    self._delivery_tasks.add(task)
                    task.add_done_callback(self._delivery_tasks.discard)
                    continue
                future = self.pending.get(packet.request_id)
                if future is not None and not future.done():
                    future.set_result(packet)
        except (OSError, ProtocolError) as exc:
            pass
        finally:
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(HybridTransportUnavailable("native IPC disconnected"))

    async def _deliver(self, packet: Envelope) -> None:
        platform_id = ""
        try:
            platform_id = await asyncio.wait_for(
                self.delivery(packet.conversation_id, packet.payload["reply_id"],
                              packet.payload["text"],
                              packet.payload["cause_event_id"]), timeout=18)
            if not isinstance(platform_id, str) or len(platform_id) > 128:
                platform_id = ""
        except (OSError, RuntimeError, asyncio.TimeoutError):
            platform_id = ""
        await self._ack(packet, platform_id=platform_id)

    async def _ack(self, packet: Envelope, *, platform_id: str) -> None:
        await self._write(Envelope(
            "delivery_ack", packet.request_id, packet.conversation_id,
            {"reply_id": packet.payload["reply_id"],
             "delivered": bool(platform_id), "platform_message_id": platform_id},
        ))

    async def submit(self, context, *, event_id: str, message_id: str,
                     text: str = "", images: list[bytes] | None = None) -> Envelope:
        """Transfer original image bytes by authenticated SHA asset IDs.

        No paths, URLs, base64 blobs or secret-bearing config cross JSONL.
        The worker rechecks the digest before handing bytes to ImageManager.
        """
        if not self.ready:
            raise HybridTransportUnavailable("native worker not ready")
        if self.conversation and context.conversation_id != self.conversation:
            raise PermissionError("worker already bound to another QQ person")
        if not isinstance(text, str) or len(text) > 3500:
            raise ValueError("invalid native message text")
        if images is None:
            images = []
        if not isinstance(images, list) or len(images) > 2 or not text.strip() and not images:
            raise ValueError("native message needs text or up to two images")
        from .image_assets import stage_image, discard_staged_image
        components = []
        staged = []
        try:
            if text.strip():
                components.append({"id":"text-1", "type":"text", "content":text})
            for index, raw in enumerate(images):
                asset_id = stage_image(self.image_instance, raw)
                staged.append(asset_id)
                components.append({"id":f"image-{index}", "type":"image",
                                   "asset_id":asset_id})
            envelope = inbound_from_host(
                context, event_id=event_id, message_id=message_id,
                components=components)
            self.conversation = context.conversation_id
            result = await self.request(
                envelope, timeout=150 if images else 30)
            if result.kind != "accepted" or result.payload.get("native_runtime") is not True:
                raise HybridTransportUnavailable(
                    "native Maisaka did not accept and process the QQ turn")
            if len(result.payload.get("observations", [])) != len(images):
                raise HybridTransportUnavailable(
                    "native ImageManager observation count mismatch")
            return result
        finally:
            for asset_id in staged:
                discard_staged_image(self.image_instance, asset_id)

    async def cancel(self, *, target_request_id: str) -> None:
        if not self.conversation:
            return
        result = await self.request(Envelope(
            "cancel", uuid4().hex, self.conversation,
            {"target_request_id": target_request_id},
        ), timeout=15)
        if result.kind != "cancel_ack":
            raise HybridTransportUnavailable("native cancellation was not acknowledged")

    async def close(self) -> None:
        self.ready = False
        for task in list(self._delivery_tasks):
            task.cancel()
        if self.process.returncode is None:
            try:
                reply = await self.request(
                    Envelope("shutdown", uuid4().hex, "", {}), timeout=5)
                if reply.kind != "bye":
                    raise HybridTransportUnavailable("shutdown was not acknowledged")
                await asyncio.wait_for(self.process.wait(), timeout=8)
            except (HybridTransportUnavailable, asyncio.TimeoutError, OSError):
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
        self._reader_task.cancel()
        await asyncio.gather(self._reader_task, *self._delivery_tasks, return_exceptions=True)
