"""Experimental QQ-private host for REAL upstream Maisaka Planner/Reply/Wait.

This is an explicit preview command and NOT the default ZhuangFY QQ path.
Native Maisaka has zero OneBot transport ownership; ZhuangFY alone authenticates
sender, checks rate limit and returns an actual OneBot message id for each reply.
Do not use with production data until the local end-to-end checklist passes.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import time
import uuid

from activity_log import log_event
from llm import load_settings
from qq_bridge import (QqBridge, QqSettings, Incoming, Conversation,
                       parse_message, single_instance, ROOT)
from zhuangfy.engine_fusion.shared_context import (
    SharedContextProvider, VerifiedPrivateQQ, VerifiedQQMemoryIdentity,
)
from zhuangfy.engine_fusion.native_transport import NativeMaisakaClient

SOURCE = Path(__file__).resolve().parents[1] / "third_party" / "MaiBot"
INSTANCE = Path(__file__).resolve().parents[1] / ".maisaka-hybrid-preview"
MAI_PYTHON = SOURCE / ".venv" / (
    "Scripts/python.exe" if os.name == "nt" else "bin/python"
)


class HybridQQBridge(QqBridge):
    def __init__(self, settings: QqSettings, *, test_user_id: int):
        if (type(test_user_id) is not int or test_user_id <= 0
                or settings.allow_all_private
                or test_user_id not in settings.allowed_private_ids):
            raise ValueError("Hybrid preview requires one explicitly allowed private QQ user")
        super().__init__(settings)
        self.test_user_id = test_user_id
        self._hybrid_erasing = False
        self._native: dict[str, NativeMaisakaClient] = {}
        self._reply_receipts: dict[tuple[str, str], str] = {}

    def accept(self, event: dict) -> bool:
        verified = parse_message(event, self.bot_id, self.settings)
        # Stage 1 only supports authenticated private text.
        # No old ZhuangFY group Planner should run silently in Hybrid.
        if (verified is None or verified.kind != "private"
                or verified.user_id != self.test_user_id):
            return False
        return super().accept(event)

    async def proactive_loop(self) -> None:
        # Maisaka may continue to plan after wait, but ZhuangFY's separate
        # proactive campaign must not run concurrently with that same identity.
        return

    async def _qq_delivery(self, conversation_id: str,
                           reply_id: str, text: str) -> str:
        expected = f"qq:{self.bot_id}:private:"
        if not conversation_id.startswith(expected):
            return ""
        try:
            user_id = int(conversation_id[len(expected):])
        except ValueError:
            return ""
        if (user_id != self.test_user_id
                or user_id not in self.settings.allowed_private_ids
                or not text or not self.ws or not self.bot_id):
            return ""
        key = (conversation_id, reply_id)
        # Receipt cache is kept for the lifetime of the QQ connection to avoid
        # duplicate sends if native Runtime repeats the same reply id.
        if key in self._reply_receipts:
            return self._reply_receipts[key]
        async with self.send_lock:
            if key in self._reply_receipts:
                return self._reply_receipts[key]
            self._reply_receipts[key] = ""
            incoming = Incoming(kind="private", user_id=user_id, group_id=None,
                                message_id="", text="")
            wait, capped = self.limiter.check(incoming, time.monotonic())
            if capped:
                log_event("Hybrid", "发送拒绝", "QQ 频率上限")
                return ""
            await asyncio.sleep(max(0.0, wait,
                                    self.next_send_at - time.monotonic()))
            self.next_send_at = time.monotonic() + 1.2
            try:
                record = await self.action("send_private_msg",
                                           {"user_id": user_id, "message": text,
                                            "auto_escape": True}, timeout=18)
            except (asyncio.TimeoutError, RuntimeError, OSError) as exc:
                log_event("Hybrid", "发送失败", type(exc).__name__)
                return ""
            platform_id = record.get("message_id")
            # Only an actual OneBot acknowledgement qualifies as delivered.
            if (type(platform_id) not in (str, int)
                    or not str(platform_id).strip()):
                return ""
            receipt = str(platform_id)
            self.limiter.mark_sent(incoming, time.monotonic())
            self._reply_receipts[key] = receipt
            log_event("Hybrid", "真实回执", f"qq:{self.bot_id}:private:{user_id} · 回复已确认")
            return receipt

    async def _client(self, scope: str) -> NativeMaisakaClient:
        old = self._native.get(scope)
        if old is not None and old.ready and old.process.returncode is None:
            return old
        if old is not None:
            await old.close()
        client = await NativeMaisakaClient.launch(
            self._qq_delivery, source=SOURCE, instance=INSTANCE,
            python=MAI_PYTHON, handshake_timeout=120,
        )
        self._native[scope] = client
        return client

    async def process(self, state: Conversation) -> None:
        while state.pending:
            # Keep ZhuangFY's existing admitted and de-duplicated QQ events.
            await asyncio.sleep(max(0, self.settings.quiet_seconds))
            incoming = state.incoming
            events = list(state.pending_event_ids)
            text = "\n".join(state.pending)
            images = list(state.pending_images)
            state.pending.clear()
            state.pending_event_ids.clear()
            state.pending_images.clear()
            state.pending_directed = False
            if not events:
                continue
            if text.strip() in {"/forget_me", "/删除我的记忆"}:
                # The destructive memory command is deliberately NOT forwarded
                # to upstream. The regular ZhuangFY erase path must run.
                state.pending = [text]
                state.pending_event_ids = events
                self._hybrid_erasing = True
                try:
                    await super().process(state)
                finally:
                    self._hybrid_erasing = False
                client = self._native.pop(
                    f"qq:{self.bot_id}:private:{incoming.user_id}", None)
                if client:
                    await client.close()
                continue
            if images:
                # Never pretend the text-only native path recognized the image.
                log_event("Hybrid", "图片暂缓",
                          "原版 ImageManager 尚未完成 AssetID→图像字节接入，拒绝吞图")
                continue
            if not text.strip() or not self.bot_id:
                continue
            scope = f"qq:{self.bot_id}:private:{incoming.user_id}"
            try:
                person_source = (
                    VerifiedQQMemoryIdentity(self.memory_client, incoming.nickname)
                    if self.memory_client is not None else None
                )
                if person_source is None:
                    # Memory integration is optional; no guessed PersonID.
                    class Unlinked:
                        def resolve(self, _): return None
                        def has_consent(self, *_): return False
                    person_source = Unlinked()
                provider = SharedContextProvider(
                    qq_settings=self.settings,
                    identity_repository=person_source,
                    memory_client=self.memory_client,
                )
                context = await asyncio.to_thread(
                    provider.prepare, VerifiedPrivateQQ(self.bot_id, incoming.user_id),
                    persona=load_settings().system_prompt,
                    query=text[:300], engine="hybrid",
                )
                client = await self._client(scope)
                # The native worker now owns reply->wait->timer->Planner cycles.
                # Ingress acceptance is NOT counted as delivery.
                await client.submit(
                    context, event_id=uuid.uuid4().hex,
                    message_id=events[-1], text=text[:3500],
                )
            except (RuntimeError, ValueError, PermissionError, OSError) as exc:
                log_event("Hybrid", "原生会话失败", f"{scope} · {type(exc).__name__}")

    async def send_reply(self, incoming: Incoming, reply: str, *,
                         voice: bool = False, continuation: bool = False) -> bool:
        if self._hybrid_erasing:
            reply += (
                "\\n注意：原生 Maisaka 的实验数据库不会随此命令删除。"
                "请退出 Hybrid、清理 .maisaka-hybrid-preview 隔离目录，"
                "并核实无其他备份，才能确认实验数据也被清理。"
            )
        return await super().send_reply(
            incoming, reply, voice=voice, continuation=continuation)

    async def receive(self) -> None:
        try:
            await super().receive()
        finally:
            await self._close_native()

    async def _close_native(self) -> None:
        clients = list(self._native.values())
        self._native.clear()
        self._reply_receipts.clear()
        if clients:
            await asyncio.gather(*(c.close() for c in clients), return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Hybrid QQ private preview: REAL Maisaka + ZhuangFY host")
    parser.add_argument("--user-id", type=int, required=True,
                        help="仅接入这个已在 ZhuangFY QQ 白名单内的测试账号")
    parser.add_argument("--confirm-experimental-hybrid", action="store_true",
                        help="允许使用尚未真实 QQ 验收的实验通道")
    args = parser.parse_args()
    if not args.confirm_experimental_hybrid:
        parser.error("必须显式确认实验启动；默认 ZhuangFY 不受影响")
    settings = QqSettings.load()
    if (settings.allow_all_private or args.user_id <= 0
            or args.user_id not in settings.allowed_private_ids):
        parser.error("Hybrid 只允许指定 QQ 白名单内单一测试账号，且必须关闭 allow_all_private")
    with single_instance():
        try:
            asyncio.run(HybridQQBridge(settings, test_user_id=args.user_id).run_forever())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
