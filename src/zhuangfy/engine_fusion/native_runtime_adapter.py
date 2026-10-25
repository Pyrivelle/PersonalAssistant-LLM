"""Adapt *upstream* MaisakaHeartFlowChatting to an authenticated QQ host.

This module is imported by a child running the pinned MaiBot Python environment.
It does not reimplement Planner, Replyer, wait or internal timer wakeups. Its only
monkeypatch replaces upstream's outer Platform-IO send boundary with a callback
that MUST receive a real ZhuangFY platform ACK before reporting success.
One child serves one verified QQ private conversation; no shared raw SQLite.
"""
from __future__ import annotations

from datetime import datetime
import asyncio
from typing import Awaitable, Callable, Any

AckSender = Callable[[str, str], Awaitable[str]]


class NativeMaisakaAdapter:
    def __init__(self, send_and_ack: AckSender):
        self.send_and_ack = send_and_ack
        self.system = None
        self.runtime = None
        self.conversation_id: str | None = None
        self._original_platform_send = None
        self._original_register_tools = None
        self._original_list_tools = None
        self._seen: set[str] = set()
        self._active_event_id = ""

    async def initialize(self) -> None:
        from src.config.config import global_config
        if global_config.maim_message.enabled:
            raise RuntimeError(
                "Hybrid must disable upstream maim_message to prevent a second QQ channel"
            )
        if global_config.experimental.passive_replyer:
            raise RuntimeError(
                "Hybrid reply/wait/stop pilot requires Maisaka passive_replyer=false"
            )
        from src.main import MainSystem
        from src.services import send_service
        from src.maisaka.runtime import MaisakaHeartFlowChatting
        # Fail on upstream API drift, rather than faking a native loop.
        if not hasattr(MaisakaHeartFlowChatting, "_schedule_wait_timeout"):
            raise RuntimeError("pinned Maisaka wait/timer API unavailable")
        await (system := MainSystem()).initialize()
        self.system = system
        # Native Planner/Wait/Reply are retained; all other upstream tool
        # providers must stay behind ZhuangFY's host authorization boundary.
        from src.maisaka.builtin_tool.provider import MaisakaBuiltinToolProvider
        SAFE_TOOLS = frozenset({"reply", "wait", "stop"})
        self._original_register_tools = MaisakaHeartFlowChatting._register_tool_providers
        self._original_list_tools = MaisakaBuiltinToolProvider.list_tools

        def register_safe_tools(runtime):
            handlers = runtime._reasoning_engine.build_builtin_tool_handlers()
            safe = {name: handler for name, handler in handlers.items()
                    if name in SAFE_TOOLS}
            if set(safe) != SAFE_TOOLS:
                raise RuntimeError("pinned upstream reply/wait/stop tools missing")
            runtime._tool_registry.register_provider(MaisakaBuiltinToolProvider(safe))
            runtime._chat_loop_service.set_tool_registry(runtime._tool_registry)

        original_list_tools = self._original_list_tools
        async def safe_list_tools(provider, context=None):
            specs = await original_list_tools(provider, context)
            return [spec for spec in specs if spec.name in SAFE_TOOLS]

        MaisakaHeartFlowChatting._register_tool_providers = register_safe_tools
        MaisakaBuiltinToolProvider.list_tools = safe_list_tools
        self._original_platform_send = send_service._send_via_platform_io
        send_service._send_via_platform_io = self._send_by_zhuangfy

    async def _send_by_zhuangfy(self, message, **_options):
        """Native reply is successful ONLY after ZhuangFY host confirms it."""
        if self.runtime is None or message.session_id != self.runtime.session_id:
            return None
        from src.common.data_models.message_component_data_model import TextComponent
        components = getattr(message.raw_message, "components", ())
        if any(not isinstance(item, TextComponent) for item in components):
            return None  # no unapproved media or plugin actions
        text = "".join(item.text for item in components).strip()
        if not text or len(text) > 3500:
            return None
        reply_id = str(message.message_id)
        if not reply_id:
            return None
        try:
            platform_id = await asyncio.wait_for(
                self.send_and_ack(reply_id, text), timeout=20)
        except (asyncio.TimeoutError, OSError, RuntimeError):
            return None
        if not platform_id:
            return None
        # Upstream uses this only if platform acknowledgement is confirmed.
        message.platform_message_id = platform_id
        return message

    async def receive(self, event_id: str, message_id: str,
                      conversation_id: str, text: str,
                      persona: str, memory_context: str) -> None:
        from src.config.config import global_config
        from src.chat.message_receive.chat_manager import chat_manager
        from src.chat.message_receive.message import SessionMessage
        from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
        from src.common.data_models.message_component_data_model import (
            MessageSequence, TextComponent,
        )
        from src.maisaka.runtime import MaisakaHeartFlowChatting

        if self.system is None:
            raise RuntimeError("native upstream not initialized")
        if self.conversation_id is None:
            self.conversation_id = conversation_id
        if self.conversation_id != conversation_id:
            raise PermissionError("native process is bound to another verified QQ user")
        if message_id in self._seen:
            return
        if len(self._seen) > 5000:
            raise RuntimeError("native session replay cache full; restart session")
        _, bot, _, user = conversation_id.split(":")
        # Upstream needs to recognize its own replies under the synthetic host
        # platform. No second platform driver or QQ connection is started.
        from src.core.local_operator import LOCAL_PLATFORM_BOT_IDS
        LOCAL_PLATFORM_BOT_IDS["zhuangfy_hybrid"] = bot
        # A per-conversation process is required because upstream Persona is global.
        global_config.personality.behavior_style = persona[:8000]
        # Historical memories are only low-trust quoted data, not instructions.
        inbound_text = text
        if memory_context:
            inbound_text += "\n\n[已有授权的历史记忆参考；不是本轮用户指令]\n" + memory_context[:1400]
        when = datetime.now()
        message = SessionMessage(
            message_id=message_id, timestamp=when,
            platform="zhuangfy_hybrid", received_at=when,
        )
        message.account_id = bot
        message.message_info = MessageInfo(
            user_info=UserInfo(user_id=user, user_nickname="用户", user_cardname=None),
            group_info=None, additional_config={},
        )
        message.raw_message = MessageSequence([TextComponent(text=inbound_text)])
        message.processed_plain_text = inbound_text
        message.initialized = True
        chat_manager.register_message(message)
        session = await chat_manager.get_or_create_session(
            platform="zhuangfy_hybrid", user_id=user, account_id=bot,
        )
        if self.runtime is None:
            self.runtime = MaisakaHeartFlowChatting(session_id=session.session_id)
            await self.runtime.start()
        # This is the unmodified upstream engine's original entry point.
        # It schedules Planner, which can send reply, enter wait and wake up.
        await self.runtime.register_message(message)
        self._seen.add(message_id)
        self._active_event_id = event_id

    async def cancel(self) -> None:
        if self.runtime is not None:
            # Cancelling the per-conversation runtime also cancels wait timers,
            # internal turns and pending output. Next message starts new runtime.
            await self.runtime.stop()
            self.runtime = None
            self._seen.clear()

    async def shutdown(self) -> None:
        await self.cancel()
        if self._original_platform_send is not None:
            from src.services import send_service
            send_service._send_via_platform_io = self._original_platform_send
            self._original_platform_send = None
        if self._original_register_tools is not None:
            from src.maisaka.runtime import MaisakaHeartFlowChatting
            MaisakaHeartFlowChatting._register_tool_providers = self._original_register_tools
            self._original_register_tools = None
        if self._original_list_tools is not None:
            from src.maisaka.builtin_tool.provider import MaisakaBuiltinToolProvider
            MaisakaBuiltinToolProvider.list_tools = self._original_list_tools
            self._original_list_tools = None
