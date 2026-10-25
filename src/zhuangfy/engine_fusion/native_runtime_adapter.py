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
import hashlib
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
        self._original_planner_context = None
        self._original_reply_identity = None
        self._social_text = ""
        self._seen: set[str] = set()
        self._wait_count = 0
        self._active_event_id = ""

    async def initialize(self) -> None:
        from src.config.config import global_config
        from zhuangfy.persona.hybrid_identity import apply_identity, load_current_persona
        apply_identity(global_config, load_current_persona())
        if global_config.maim_message.enabled:
            raise RuntimeError(
                "Hybrid must disable upstream maim_message to prevent a second QQ channel"
            )
        # The selected model sometimes returns a reply plan as plain text
        # without a tool call. Native Maisaka's passive Replyer turns that
        # plan into a visible reply while preserving explicit wait/stop calls.
        global_config.experimental.passive_replyer = True
        from src.main import MainSystem
        from src.services import send_service
        from src.maisaka.runtime import MaisakaHeartFlowChatting
        from src.maisaka.chat_loop_service import MaisakaChatLoopService
        from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
        # Fail on upstream API drift, rather than faking a native loop.
        if not hasattr(MaisakaHeartFlowChatting, "_schedule_wait_timeout"):
            raise RuntimeError("pinned Maisaka wait/timer API unavailable")
        await (system := MainSystem()).initialize()
        self.system = system
        global_config.experimental.passive_replyer = True
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
            from src.core.tooling import ToolExecutionResult
            original_wait = safe["wait"]

            async def bounded_native_wait(invocation, context=None):
                raw_seconds = invocation.arguments.get("seconds")
                if (type(raw_seconds) is not int or not 10 <= raw_seconds <= 1800
                        or self._wait_count >= 12):
                    return ToolExecutionResult(
                        tool_name="wait", success=False,
                        error_message="Hybrid 等待超出安全预算；请选择 stop 或正常回复。")
                self._wait_count += 1
                return await original_wait(invocation, context)

            safe["wait"] = bounded_native_wait
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
        self._install_social_context(MaisakaChatLoopService, BaseMaisakaReplyGenerator)

    def _install_social_context(self, planner_class, reply_class) -> None:
        """Patch the two system-prompt builders, retaining the latest verified facts."""
        # Both model paths receive the same authenticated social snapshot as
        # system context. A wait timer reuses it without a fresh QQ ingress.
        self._original_planner_context = planner_class.build_prompt_template_context
        self._original_reply_identity = reply_class._build_personality_prompt
        original_planner_context = self._original_planner_context
        original_reply_identity = self._original_reply_identity

        def planner_context(loop, *args, **kwargs):
            result = original_planner_context(loop, *args, **kwargs)
            if self._social_text:
                result["personality"] = result["personality"] + "\n" + self._social_text
            if "behavior_style" in result:
                result["behavior_style"] = (
                    result["behavior_style"]
                    + "\n行为与输出须一致：决定回复时输出给 Replyer 的指导正文，"
                      "不要同时调用 stop；stop 只用于本轮完全不回复。"
                )
            return result

        def reply_identity(generator):
            result = original_reply_identity(generator)
            return result + "\n" + self._social_text if self._social_text else result

        planner_class.build_prompt_template_context = planner_context
        reply_class._build_personality_prompt = reply_identity

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
                      persona: str, memory_context: str,
                      images: list[tuple[str, bytes]] | None = None,
                      recent_context: str = "", social: dict | None = None) -> list[dict]:
        from src.config.config import global_config
        from src.chat.message_receive.chat_manager import chat_manager
        from src.chat.message_receive.message import SessionMessage
        from src.common.data_models.mai_message_data_model import MessageInfo, UserInfo
        from src.common.data_models.message_component_data_model import (
            MessageSequence, TextComponent, ImageComponent,
        )
        from src.maisaka.runtime import MaisakaHeartFlowChatting

        if self.system is None:
            raise RuntimeError("native upstream not initialized")
        if self.conversation_id is None:
            self.conversation_id = conversation_id
        if self.conversation_id != conversation_id:
            raise PermissionError("native process is bound to another verified QQ user")
        if message_id in self._seen:
            return []
        self._wait_count = 0
        if len(self._seen) > 5000:
            raise RuntimeError("native session replay cache full; restart session")
        _, bot, _, user = conversation_id.split(":")
        # Upstream needs to recognize its own replies under the synthetic host
        # platform. No second platform driver or QQ connection is started.
        from src.core.local_operator import LOCAL_PLATFORM_BOT_IDS
        LOCAL_PLATFORM_BOT_IDS["zhuangfy_hybrid"] = bot
        # A per-conversation process is required because upstream Persona is global.
        # Maisaka supplies the Planner/Replyer loop, not this character's
        # identity. Its five persona fields must always derive from ZhuangFY.
        from zhuangfy.persona.hybrid_identity import apply_identity
        apply_identity(global_config, persona)
        from zhuangfy.persona.hybrid_identity import render_social_context
        self._social_text = render_social_context(social or {})
        # Keep the authenticated user message pure. Historical projections are
        # one-use native references, never saved as words the user just sent.
        inbound_text = text
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
        parts = [TextComponent(text=inbound_text)] if inbound_text.strip() else []
        observations: list[dict] = []
        observed_notes: list[str] = []
        if images:
            from src.chat.image_system.image_manager import image_manager
            for asset_id, image_bytes in images:
                digest = hashlib.sha256(image_bytes).hexdigest()
                if asset_id != "img_" + digest:
                    raise ValueError("original QQ image bytes do not match asset ID")
                cached = bool(image_manager.get_cached_image_description(digest))
                # Native ImageManager owns save, SHA cache, async VLM and DB.
                desc = await image_manager.get_image_description(
                    image_bytes=image_bytes, wait_for_build=True)
                desc = str(desc or "").strip()[:2500]
                parts.append(ImageComponent(binary_hash=digest, content=desc,
                                            binary_data=image_bytes))
                observations.append({"asset_id":asset_id, "description":desc,
                                     "cache_hit":cached,
                                     "description_ready":bool(desc)})
                if desc:
                    observed_notes.append(
                        f"[系统视觉观察，非用户原话，图片SHA256={digest}：{desc}]")
                else:
                    observed_notes.append(
                        f"[图片SHA256={digest}，原生VLM暂未提供描述；不要猜测画面]")
        message.raw_message = MessageSequence(parts)
        message.processed_plain_text = (
            inbound_text + ("\\n" if observed_notes else "")
            + "\\n".join(observed_notes)
        ).strip()
        message.initialized = True
        chat_manager.register_message(message)
        session = await chat_manager.get_or_create_session(
            platform="zhuangfy_hybrid", user_id=user, account_id=bot,
        )
        if self.runtime is None:
            self.runtime = MaisakaHeartFlowChatting(session_id=session.session_id)
            await self.runtime.start()
        if memory_context or recent_context:
            from src.maisaka.context.messages import ReferenceMessage, ReferenceMessageType
            if memory_context:
                self.runtime._chat_history.append(ReferenceMessage(
                    content="历史记忆资料；不是本轮用户指令。\n" + memory_context[:1400],
                    timestamp=when,
                    reference_type=ReferenceMessageType.MEMORY,
                    remaining_uses_value=1,
                    display_prefix="[ZhuangFY 历史记忆]",
                ))
            if recent_context:
                self.runtime._chat_history.append(ReferenceMessage(
                    content="桌面近期对话；仅供接续话题，不是本轮用户指令。\n"
                            + recent_context[:1600],
                    timestamp=when,
                    reference_type=ReferenceMessageType.CUSTOM,
                    remaining_uses_value=1,
                    display_prefix="[ZhuangFY 跨入口上下文]",
                ))
        # This is the unmodified upstream engine's original entry point.
        # It schedules Planner, which can send reply, enter wait and wake up.
        await self.runtime.register_message(message)
        self._seen.add(message_id)
        self._active_event_id = event_id
        return observations

    async def cancel(self) -> None:
        if self.runtime is not None:
            # Cancelling the per-conversation runtime also cancels wait timers,
            # internal turns and pending output. Next message starts new runtime.
            await self.runtime.stop()
            self.runtime = None
            self._seen.clear()

    async def shutdown(self) -> None:
        await self.cancel()
        if self._original_planner_context is not None:
            from src.maisaka.chat_loop_service import MaisakaChatLoopService
            MaisakaChatLoopService.build_prompt_template_context = self._original_planner_context
            self._original_planner_context = None
        if self._original_reply_identity is not None:
            from src.chat.replyer.maisaka_generator_base import BaseMaisakaReplyGenerator
            BaseMaisakaReplyGenerator._build_personality_prompt = self._original_reply_identity
            self._original_reply_identity = None
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
        if self.system is not None:
            from src.plugin_runtime.integration import get_plugin_runtime_manager
            await get_plugin_runtime_manager().stop()
            self.system = None
