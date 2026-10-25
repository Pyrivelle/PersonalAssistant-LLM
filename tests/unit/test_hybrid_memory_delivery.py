"""Hybrid records memory only after the real QQ send acknowledgement."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from hybrid_qq_bridge import HybridQQBridge
from zhuangfy.engine_fusion.ipc_contract import Envelope
from zhuangfy.engine_fusion.native_transport import NativeMaisakaClient


class HybridMemoryDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def bridge(self, action_result):
        bridge = object.__new__(HybridQQBridge)
        bridge.bot_id = 456
        bridge.test_user_id = 123
        bridge.settings = SimpleNamespace(allowed_private_ids={123})
        bridge.ws = object()
        bridge._reply_receipts = {}
        bridge._memory_ref_by_turn = {
            "turn1": {"conversation_id": "conv1", "entry_id": "entry1"}}
        bridge._user_text_by_turn = {"turn1": "你好"}
        bridge._relation_reviewed_turns = set()
        bridge._turn_by_scope = {}
        bridge.relationship_tasks = {}
        bridge.planner_settings = SimpleNamespace(
            relationship_behavior_r1_enabled=False,
            relationship_evolution_r1_enabled=False)
        bridge.memory_client = SimpleNamespace(complete_reply=Mock())
        bridge.send_lock = asyncio.Lock()
        bridge.next_send_at = 0
        bridge.limiter = SimpleNamespace(
            check=lambda *_: (0, False), mark_sent=lambda *_: None)
        bridge.action = AsyncMock(return_value=action_result)
        return bridge

    async def test_confirmed_qq_reply_commits_verified_timeline_once(self):
        bridge = self.bridge({"message_id": 9001})
        with patch("hybrid_qq_bridge.log_event"), patch(
                "hybrid_qq_bridge.owner_scope", return_value="other"):
            first = await bridge._qq_delivery(
                "qq:456:private:123", "reply1", "你好", "turn1")
            replay = await bridge._qq_delivery(
                "qq:456:private:123", "reply1", "你好", "turn1")
        self.assertEqual((first, replay), ("9001", "9001"))
        bridge.memory_client.complete_reply.assert_called_once()
        self.assertEqual(bridge.memory_client.complete_reply.call_args.kwargs,
                         {"conversation_id": "conv1", "entry_id": "entry1",
                          "reply_id": "reply1", "content": "你好"})

    async def test_failed_qq_send_never_commits_memory(self):
        bridge = self.bridge({})
        with patch("hybrid_qq_bridge.log_event"):
            self.assertEqual(await bridge._qq_delivery(
                "qq:456:private:123", "reply1", "你好", "turn1"), "")
        bridge.memory_client.complete_reply.assert_not_called()

    async def test_native_outbound_preserves_cause_event_id(self):
        client = object.__new__(NativeMaisakaClient)
        client.delivery = AsyncMock(return_value="9001")
        client._ack = AsyncMock()
        packet = Envelope("outbound", "request1", "qq:456:private:123", {
            "reply_id": "reply1", "text": "你好", "cause_event_id": "turn1"})
        await client._deliver(packet)
        client.delivery.assert_awaited_once_with(
            "qq:456:private:123", "reply1", "你好", "turn1")
        client._ack.assert_awaited_once_with(packet, platform_id="9001")
