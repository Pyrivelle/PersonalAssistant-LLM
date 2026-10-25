"""Native Maisaka adapter's offline delivery and wait handoff contracts."""
import asyncio
from io import BytesIO
from types import SimpleNamespace, ModuleType
import sys
import unittest
from unittest.mock import AsyncMock, patch

from zhuangfy.engine_fusion.ipc_contract import Envelope
from zhuangfy.engine_fusion.native_runtime_adapter import NativeMaisakaAdapter
from zhuangfy.engine_fusion.native_worker import NativeWorker
from zhuangfy.engine_fusion.native_worker import UPSTREAM_SHA


class Capture:
    def __init__(self):
        self.packets = []
    def write(self, data):
        self.packets.append(Envelope.from_line(data))
    def flush(self):
        pass


class NativeWorkerWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_hello_advertises_v2_contract(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        import os

        capture = Capture()
        worker = NativeWorker(sink=capture)
        worker.adapter.initialize = AsyncMock()
        with TemporaryDirectory() as temp, patch.dict(os.environ, {"MAIBOT_INSTANCE_DIR": temp}):
            await worker.handle(Envelope("hello", "hello-v2", "", {"upstream_sha": UPSTREAM_SHA}))
        self.assertEqual(capture.packets[-1].payload["protocol"], 2)
        self.assertTrue(worker.ready)
        worker.stopped = True
        worker.monitor_task.cancel()
        await asyncio.gather(worker.monitor_task, return_exceptions=True)

    async def test_successful_native_reply_requires_real_platform_ack(self):
        capture = Capture()
        worker = NativeWorker(sink=capture)
        worker.ready = True
        worker.conversation = "qq:456:private:123"
        pending = asyncio.create_task(worker.deliver("reply1", "想再聊几句"))
        await asyncio.sleep(0)
        outbound = capture.packets[-1]
        self.assertEqual(outbound.kind, "outbound")
        self.assertFalse(pending.done())
        bad = Envelope("delivery_ack", outbound.request_id, outbound.conversation_id, {
            "reply_id":"different-reply", "delivered":True,
            "platform_message_id":"qq-10",
        })
        await worker.handle(bad)
        self.assertFalse(pending.done())
        good = Envelope("delivery_ack", outbound.request_id, outbound.conversation_id, {
            "reply_id":"reply1", "delivered":True,
            "platform_message_id":"qq-10",
        })
        await worker.handle(good)
        self.assertEqual(await pending, "qq-10")

    async def test_negative_platform_ack_does_not_claim_success(self):
        capture = Capture()
        worker = NativeWorker(sink=capture)
        worker.ready = True
        worker.conversation = "qq:456:private:123"
        pending = asyncio.create_task(worker.deliver("reply2", "这次发送会失败"))
        await asyncio.sleep(0)
        outbound = capture.packets[-1]
        await worker.handle(Envelope(
            "delivery_ack", outbound.request_id, outbound.conversation_id, {
                "reply_id":"reply2", "delivered":False,
                "platform_message_id":"",
            }))
        self.assertEqual(await pending, "")


class NativeAdapterSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_planner_and_replyer_keep_same_host_social_facts(self):
        class Planner:
            def build_prompt_template_context(self):
                return {"personality": "庄方宜"}

        class Replyer:
            def _build_personality_prompt(self):
                return "你是庄方宜"

        adapter = NativeMaisakaAdapter(lambda *_: asyncio.sleep(0, result=""))
        adapter._install_social_context(Planner, Replyer)
        try:
            adapter._social_text = "已核验用户：Pyrivelle；关系 L4"
            first = Planner().build_prompt_template_context()["personality"]
            reply = Replyer()._build_personality_prompt()
            # A native wait timer calls Planner again without a new ingress.
            after_wait = Planner().build_prompt_template_context()["personality"]
            self.assertEqual(first, after_wait)
            self.assertIn("Pyrivelle；关系 L4", reply)
            self.assertEqual(first.count("关系 L4"), 1)
        finally:
            Planner.build_prompt_template_context = adapter._original_planner_context
            Replyer._build_personality_prompt = adapter._original_reply_identity

    async def test_shutdown_stops_native_plugin_runners(self):
        manager = SimpleNamespace(stop=AsyncMock())
        module = ModuleType("src.plugin_runtime.integration")
        module.get_plugin_runtime_manager = lambda: manager
        adapter = NativeMaisakaAdapter(lambda *_: asyncio.sleep(0, result=""))
        adapter.system = object()
        with patch.dict(sys.modules, {"src.plugin_runtime.integration": module}):
            await adapter.shutdown()
        manager.stop.assert_awaited_once()
        self.assertIsNone(adapter.system)

    async def test_adapter_does_not_mark_failed_send_as_delivered(self):
        class NativeText:
            def __init__(self, text):
                self.text = text
        mod = ModuleType("src.common.data_models.message_component_data_model")
        mod.TextComponent = NativeText
        async def negative_ack(*args):
            return ""
        adapter = NativeMaisakaAdapter(negative_ack)
        adapter.runtime = SimpleNamespace(session_id="native-session")
        message = SimpleNamespace(
            message_id="reply3", session_id="native-session",
            raw_message=SimpleNamespace(components=[NativeText("reply")]),
        )
        with patch.dict(sys.modules, {
            "src.common.data_models.message_component_data_model": mod
        }):
            self.assertIsNone(await adapter._send_by_zhuangfy(message))
            self.assertFalse(hasattr(message, "platform_message_id"))

    async def test_adapter_stamps_confirmed_platform_message_id(self):
        class NativeText:
            def __init__(self, text):
                self.text = text
        mod = ModuleType("src.common.data_models.message_component_data_model")
        mod.TextComponent = NativeText
        async def confirmed_ack(*args):
            return "onebot-123"
        adapter = NativeMaisakaAdapter(confirmed_ack)
        adapter.runtime = SimpleNamespace(session_id="native-session")
        message = SimpleNamespace(
            message_id="reply4", session_id="native-session",
            raw_message=SimpleNamespace(components=[NativeText("回复")]),
        )
        with patch.dict(sys.modules, {
            "src.common.data_models.message_component_data_model": mod
        }):
            self.assertIs(await adapter._send_by_zhuangfy(message), message)
            self.assertEqual(message.platform_message_id, "onebot-123")


if __name__ == "__main__":
    unittest.main()
