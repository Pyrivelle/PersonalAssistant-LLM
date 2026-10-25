"""Native Maisaka IPC worker, run with PINNED upstream MaiBot Python.

Stdout is reserved for JSONL; upstream logs go to stderr. Native runtime and
ImageManager code are imported from third_party/MaiBot, not reimplemented.
This worker is intentionally not started by the public QQ switch until host
delivery and isolation have passed live integration tests.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from uuid import uuid4

from zhuangfy.engine_fusion.ipc_contract import Envelope, ProtocolError, MAX_WIRE_BYTES
from zhuangfy.engine_fusion.native_runtime_adapter import NativeMaisakaAdapter

UPSTREAM_SHA = "062d45cf8bd4272ac57d7ec03fcf405e5a458211"


class NativeWorker:
    def __init__(self, source=None, sink=None):
        self.input = source or sys.stdin.buffer
        self.output = sink or sys.stdout.buffer
        self.writer_lock = asyncio.Lock()
        self.pending_delivery: dict[str, tuple[str, asyncio.Future[str]]] = {}
        self.adapter = NativeMaisakaAdapter(self.deliver)
        self.ready = False
        self.stopped = False
        self.conversation = ""
        self.tasks: set[asyncio.Task] = set()

    async def emit(self, envelope: Envelope) -> None:
        async with self.writer_lock:
            self.output.write(envelope.to_line())
            self.output.flush()

    async def deliver(self, reply_id: str, text: str) -> str:
        if not self.conversation or not self.ready:
            return ""
        request_id = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending_delivery[request_id] = (reply_id, future)
        try:
            await self.emit(Envelope("outbound", request_id, self.conversation, {
                "reply_id": reply_id, "text": text,
                "cause_event_id": self.adapter._active_event_id or "native-timer",
            }))
            return await asyncio.wait_for(future, timeout=19)
        finally:
            self.pending_delivery.pop(request_id, None)

    async def handle(self, event: Envelope) -> None:
        if event.kind == "hello":
            if event.payload["upstream_sha"] != UPSTREAM_SHA or self.ready:
                await self.emit(Envelope("error", event.request_id, "", {
                    "reason": "upstream_pin_mismatch_or_duplicate_handshake",
                }))
                return
            try:
                await self.adapter.initialize()
                self.ready = True
            except Exception as exc:
                await self.emit(Envelope("error", event.request_id, "", {
                    "reason": "native_initialization_failed",
                    "exception_type": type(exc).__name__,
                }))
                self.stopped = True
                return
            await self.emit(Envelope("hello_ack", event.request_id, "", {
                "upstream_sha": UPSTREAM_SHA,
                "protocol": 1, "transport_ready": True,
                "native_runtime_ready": True,
                "capabilities": ["native_maisaka_planner_reply_wait", "text_ingress", "host_delivery_ack"],
            }))
            return
        if not self.ready:
            await self.emit(Envelope("error", event.request_id, event.conversation_id,
                                     {"reason": "native_not_ready"}))
            return
        if event.kind == "delivery_ack":
            match = self.pending_delivery.get(event.request_id)
            if match is not None:
                reply_id, promise = match
                if (not promise.done() and event.conversation_id == self.conversation
                        and event.payload["reply_id"] == reply_id):
                    promise.set_result(
                        event.payload["platform_message_id"] if event.payload["delivered"] else ""
                    )
            return
        if event.kind == "ingress":
            if self.conversation and self.conversation != event.conversation_id:
                await self.emit(Envelope("error", event.request_id, event.conversation_id,
                                         {"reason": "cross_conversation_denied"}))
                return
            components = event.payload["components"]
            asset_ids = [part["asset_id"] for part in components
                         if part["type"] == "image"]
            if len(asset_ids) > 2:
                await self.emit(Envelope("unavailable", event.request_id, event.conversation_id, {
                    "reason": "too_many_images", "delivered": False,
                }))
                return
            try:
                from zhuangfy.engine_fusion.image_assets import load_staged_image
                instance = Path(os.environ["MAIBOT_INSTANCE_DIR"])
                images = [(asset_id, load_staged_image(instance, asset_id))
                          for asset_id in asset_ids]
            except (OSError, ValueError, KeyError) as error:
                await self.emit(Envelope("unavailable", event.request_id, event.conversation_id, {
                    "reason": "image_asset_unavailable_or_invalid", "delivered": False,
                }))
                return
            self.conversation = event.conversation_id
            p = event.payload
            ctx = p["context"]
            try:
                observations = await self.adapter.receive(
                    event.request_id, p["message_id"], event.conversation_id,
                    "\n".join(part["content"] for part in components
                              if part["type"] == "text"),
                    ctx["persona"], ctx["memory_context"], images=images,
                )
            except Exception as exc:
                await self.emit(Envelope("error", event.request_id, event.conversation_id, {
                    "reason": "native_ingress_failed",
                    "exception_type": type(exc).__name__,
                }))
                return
            await self.emit(Envelope("accepted", event.request_id, event.conversation_id, {
                "message_id": p["message_id"], "native_runtime": True,
                "observations": observations or [],
            }))
            return
        if event.kind == "cancel":
            await self.adapter.cancel()
            for _reply_id, future in self.pending_delivery.values():
                if not future.done():
                    future.set_result("")
            self.pending_delivery.clear()
            await self.emit(Envelope("cancel_ack", event.request_id, event.conversation_id, {
                "target_request_id": event.payload["target_request_id"],
                "cancelled": True,
            }))
            return
        if event.kind == "shutdown":
            self.stopped = True
            await self.emit(Envelope("bye", event.request_id, "", {"closed": True}))
            return
        await self.emit(Envelope("error", event.request_id, event.conversation_id, {
            "reason": "unsupported_native_command",
        }))

    async def run(self) -> int:
        try:
            while not self.stopped:
                # Input uses a thread so upstream native Planner/Wait continues
                # running on the event loop while QQ Host receives a reply.
                line = await asyncio.to_thread(self.input.readline, MAX_WIRE_BYTES + 1)
                if not line:
                    break
                try:
                    event = Envelope.from_line(line)
                except ProtocolError:
                    return 2
                await self.handle(event)
            return 0
        finally:
            for _reply_id, fut in self.pending_delivery.values():
                if not fut.done():
                    fut.set_result("")
            await self.adapter.shutdown()


def main() -> int:
    # Maibot may use print/rich for UI logs. These must never enter the JSONL
    # protocol pipe; keep fd1 for protocol output and redirect Python stdout.
    wire = sys.stdout.buffer
    sys.stdout = sys.stderr
    expected_root = Path(os.environ.get("ZHUANGFY_MAIBOT_SOURCE", "")).resolve()
    if not (expected_root / "src" / "maisaka" / "runtime.py").is_file():
        print("Pinned MaiBot source missing", file=sys.stderr)
        return 2
    import src.maisaka.runtime as native_runtime
    if not str(Path(native_runtime.__file__).resolve()).startswith(str(expected_root) + os.sep):
        print("Refusing non-pinned Maisaka import", file=sys.stderr)
        return 2
    return asyncio.run(NativeWorker(sink=wire).run())


if __name__ == "__main__":
    raise SystemExit(main())
