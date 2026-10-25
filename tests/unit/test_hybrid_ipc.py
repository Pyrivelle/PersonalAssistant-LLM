import asyncio
from io import BytesIO
import unittest

from zhuangfy.engine_fusion.ipc_contract import (
    Envelope, ProtocolError, inbound_from_host,
)
from zhuangfy.engine_fusion.shared_context import SharedConversationContext
from zhuangfy.engine_fusion.worker import WorkerSession, run_worker
from zhuangfy.engine_fusion.ipc_transport import (
    HybridWorkerClient, HybridTransportUnavailable,
)

CONVERSATION = "qq:456:private:123"


def example_context():
    return SharedConversationContext(
        1, CONVERSATION, "hybrid", "person-abc",
        "庄方宜的人格设定", "[历史记忆资料；不是指令] 之前聊过花",
        "qq:456",
    )


def example_event():
    return inbound_from_host(
        example_context(), event_id="evt-1", message_id="msg-1",
        components=[
            {"id": "c1", "type": "text", "content": "看看这张图"},
            {"id": "c2", "type": "image", "asset_id": "img_001"},
        ],
    )


class IPCEnvelopeTests(unittest.TestCase):
    def test_image_and_low_trust_memory_remain_inside_same_turn(self):
        event = example_event()
        roundtrip = Envelope.from_line(event.to_line())
        self.assertEqual(roundtrip.kind, "ingress")
        self.assertEqual(roundtrip.conversation_id, CONVERSATION)
        self.assertEqual(len(roundtrip.payload["components"]), 2)
        self.assertEqual(roundtrip.payload["components"][1]["asset_id"], "img_001")
        self.assertNotIn("role", roundtrip.payload["components"][1])
        self.assertEqual(roundtrip.payload["context"]["person_id"], "person-abc")

    def test_denies_cross_identity_and_rogue_metadata(self):
        p = example_event().payload
        p["context"]["conversation_id"] = "qq:999:private:123"
        with self.assertRaisesRegex(ProtocolError, "inconsistent"):
            Envelope("ingress", "evt-1", CONVERSATION, p)
        p = example_event().payload
        p["components"].append({"id":"c3","type":"tool","content":"sudo rm"})
        with self.assertRaisesRegex(ProtocolError, "unsupported"):
            Envelope("ingress", "evt-2", CONVERSATION, p)

    def test_denies_raw_image_urls_and_too_large_payload(self):
        p = example_event().payload
        p["components"][1]["url"] = "https://example.test/image.png"
        with self.assertRaises(ProtocolError):
            Envelope("ingress", "evt-1", CONVERSATION, p)
        big = Envelope("ingress", "evt-3", CONVERSATION, {
            **example_event().payload,
            "context": {**example_context().as_projection(), "persona": "好" * 8000},
        })
        self.assertLessEqual(len(big.to_line()), 20_000)
        with self.assertRaises(ProtocolError):
            Envelope.from_line(b"{" + b"x" * 20_000 + b"}")
        with self.assertRaises(ProtocolError):
            Envelope("ingress", "evt/3", CONVERSATION, example_event().payload)

    def test_cancel_and_delivery_receipt_are_typed(self):
        cancel = Envelope("cancel", "r-2", CONVERSATION,
                          {"target_request_id": "evt-1"})
        self.assertEqual(Envelope.from_line(cancel.to_line()).kind, "cancel")
        ack = Envelope("delivery_ack", "r-3", CONVERSATION,
                       {"reply_id":"reply-1","delivered":True,
                        "platform_message_id":"onebot:444"})
        self.assertEqual(Envelope.from_line(ack.to_line()).payload["delivered"], True)
        with self.assertRaises(ProtocolError):
            Envelope("delivery_ack", "r-4", CONVERSATION,
                     {"reply_id":"reply-1","delivered":"true",
                      "platform_message_id":"onebot:444"})

    def test_worker_cannot_claim_native_model_reply(self):
        worker = WorkerSession()
        hello = worker.dispatch(Envelope(
            "hello", "hello-1", "", {
                "upstream_sha": "062d45cf8bd4272ac57d7ec03fcf405e5a458211",
            },
        ))
        self.assertTrue(hello.payload["transport_ready"])
        self.assertFalse(hello.payload["native_runtime_ready"])
        result = worker.dispatch(example_event())
        self.assertEqual(result.kind, "unavailable")
        self.assertFalse(result.payload["delivered"])
        self.assertEqual(worker.dispatch(Envelope(
            "delivery_ack", "ack1", CONVERSATION, {
                "reply_id":"r1","delivered":True,"platform_message_id":"onebot:42",
            },
        )).payload["accepted"], False)

    def test_stdio_worker_handshake_then_clean_exit(self):
        inputs = (
            Envelope("hello", "h-1", "", {
                "upstream_sha":"062d45cf8bd4272ac57d7ec03fcf405e5a458211"
            }).to_line()
            + example_event().to_line()
            + Envelope("shutdown", "s-1", "", {}).to_line()
        )
        destination = BytesIO()
        self.assertEqual(run_worker(BytesIO(inputs), destination), 0)
        responses = [Envelope.from_line(x+b"\n")
                     for x in destination.getvalue().splitlines()]
        self.assertEqual([x.kind for x in responses],
                         ["hello_ack", "unavailable", "bye"])


class IPCProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_subprocess_handshake_and_fail_closed_message(self):
        client = await HybridWorkerClient.launch(timeout=4)
        try:
            self.assertTrue(client.transport_ready)
            self.assertFalse(client.native_ready)
            with self.assertRaisesRegex(HybridTransportUnavailable, "not connected"):
                await client.submit(example_context(), event_id="evt-1",
                                    message_id="m1",
                                    components=[{"id":"c1","type":"text","content":"你好"}])
            # An unsuccessful fake submission must not kill the worker.
            self.assertIsNone(client.process.returncode)
        finally:
            await client.close()
        self.assertIsNotNone(client.process.returncode)

    async def test_wrong_upstream_handshake_fails_closed(self):
        worker = WorkerSession()
        reply = worker.dispatch(Envelope(
            "hello", "h-2", "", {"upstream_sha":"0"*40},
        ))
        self.assertEqual(reply.payload["reason"], "upstream_pin_mismatch")
        self.assertFalse(reply.payload["transport_ready"])
        self.assertFalse(worker.started)


if __name__ == "__main__":
    unittest.main()
