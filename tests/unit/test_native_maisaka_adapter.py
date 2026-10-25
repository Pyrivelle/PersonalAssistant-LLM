"""Native Maisaka adapter's offline delivery and wait handoff contracts."""
import asyncio
from io import BytesIO
from types import SimpleNamespace, ModuleType
import sys
import unittest
from unittest.mock import patch

from zhuangfy.engine_fusion.ipc_contract import Envelope
from zhuangfy.engine_fusion.native_runtime_adapter import NativeMaisakaAdapter
from zhuangfy.engine_fusion.native_worker import NativeWorker


class Capture:
    def __init__(self):
        self.packets = []
    def write(self, data):
        self.packets.append(Envelope.from_line(data))
    def flush(self):
        pass


class NativeWorkerWireTests(unittest.IsolatedAsyncioTestCase):
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
