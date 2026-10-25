"""Original QQ image -> SHA asset -> native Maisaka worker regression tests."""
import asyncio
import base64
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from zhuangfy.engine_fusion.image_assets import (
    stage_image, load_staged_image, discard_staged_image, validate_image,
)
from zhuangfy.engine_fusion.ipc_contract import Envelope, inbound_from_host, ProtocolError
from zhuangfy.engine_fusion.native_worker import NativeWorker
from zhuangfy.engine_fusion.native_transport import NativeMaisakaClient
from zhuangfy.engine_fusion.shared_context import SharedConversationContext

# Valid 1x1 PNG. The helper checks raw magic/size; QQ Qt reader separately
# validates format and pixel dimensions before staging the bytes.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/dAAAAABJRU5ErkJggg=="
)
CONV = "qq:456:private:123"


def context():
    return SharedConversationContext(
        1, CONV, "hybrid", None, "庄方宜", "", "qq:456")


class StageTests(unittest.TestCase):
    def test_duplicate_original_bytes_have_same_sha_and_stage_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = stage_image(root, PNG)
            self.assertEqual(asset, "img_" + hashlib.sha256(PNG).hexdigest())
            self.assertEqual(stage_image(root, PNG), asset)
            self.assertEqual(load_staged_image(root, asset), PNG)
            discard_staged_image(root, asset)
            with self.assertRaises(FileNotFoundError):
                load_staged_image(root, asset)

    def test_corrupt_stage_file_cannot_be_forwarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset = stage_image(root, PNG)
            (root / "incoming_images" / (asset + ".bin")).write_bytes(b"evil" + PNG)
            with self.assertRaises(ValueError):
                load_staged_image(root, asset)
            self.assertEqual(stage_image(root, PNG), asset)
            self.assertEqual(load_staged_image(root, asset), PNG)

    def test_invalid_payload_and_asset_escape_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                stage_image(Path(tmp), b"this is not a picture")
            with self.assertRaises(ValueError):
                load_staged_image(Path(tmp), "../other")
            with self.assertRaises(ValueError):
                validate_image(PNG + b"x" * 12_000_000)
            if hasattr(os, "symlink"):
                image = stage_image(Path(tmp), PNG)
                path = Path(tmp) / "incoming_images" / (image + ".bin")
                path.unlink()
                try:
                    path.symlink_to(Path(tmp) / "unrelated.txt")
                except (OSError, NotImplementedError):
                    return
                with self.assertRaises(ValueError):
                    stage_image(Path(tmp), PNG)


class NativeImageIPCWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_reads_only_staged_image_and_returns_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset = stage_image(Path(tmp), PNG)
            worker = NativeWorker()
            worker.ready = True
            seen = {}
            emitted = []
            async def receive(_event, message_id, _scope, text, persona, memory, images=None):
                seen["image"] = images[0][1]
                seen["text"] = text
                return [{"asset_id":asset,"description":"一朵玫瑰",
                         "cache_hit":False,"description_ready":True}]
            worker.adapter.receive = receive
            worker.emit = AsyncMock(side_effect=lambda evt: emitted.append(evt))
            event = inbound_from_host(context(), event_id="evt1",
                                      message_id="msg1",
                                      components=[{"id":"c1","type":"image","asset_id":asset}])
            with patch.dict(os.environ, {"MAIBOT_INSTANCE_DIR":tmp}):
                await worker.handle(event)
            self.assertEqual(seen["image"], PNG)
            self.assertEqual(seen["text"], "")
            self.assertEqual(emitted[-1].kind, "accepted")
            self.assertEqual(emitted[-1].payload["observations"][0]["description"],
                             "一朵玫瑰")

    async def test_native_worker_rejects_mismatched_hash_without_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset = stage_image(Path(tmp), PNG)
            path = Path(tmp) / "incoming_images" / (asset + ".bin")
            path.write_bytes(b"GIF89a" + b"bad" * 30)
            worker = NativeWorker()
            worker.ready = True
            worker.emit = AsyncMock()
            worker.adapter.receive = AsyncMock()
            with patch.dict(os.environ, {"MAIBOT_INSTANCE_DIR":tmp}):
                await worker.handle(inbound_from_host(
                    context(), event_id="evt2", message_id="msg2",
                    components=[{"id":"img1","type":"image","asset_id":asset}]))
            worker.adapter.receive.assert_not_awaited()
            self.assertEqual(worker.emit.await_args.args[0].kind, "unavailable")

    async def test_native_client_stages_and_cleans_up_after_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = object.__new__(NativeMaisakaClient)
            client.ready = True
            client.conversation = ""
            client.image_instance = Path(tmp)
            async def reply(env, timeout=30):
                self.assertEqual(env.payload["components"][0]["type"], "image")
                raw = load_staged_image(Path(tmp),
                                        env.payload["components"][0]["asset_id"])
                self.assertEqual(raw, PNG)
                return Envelope("accepted", env.request_id, env.conversation_id, {
                    "message_id":env.payload["message_id"],
                    "native_runtime":True,
                    "observations":[{"asset_id": env.payload["components"][0]["asset_id"],
                                     "description":"玫瑰花", "cache_hit":True,
                                     "description_ready":True}],
                })
            client.request = reply
            receipt = await client.submit(context(), event_id="ev3",
                                          message_id="msg3", images=[PNG])
            self.assertEqual(receipt.payload["observations"][0]["cache_hit"], True)
            self.assertEqual(list((Path(tmp)/"incoming_images").glob("*.bin")), [])

    async def test_native_client_rejects_incomplete_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = object.__new__(NativeMaisakaClient)
            client.ready = True
            client.conversation = ""
            client.image_instance = Path(tmp)
            async def reply(env, timeout=30):
                return Envelope("accepted", env.request_id, env.conversation_id, {
                    "message_id":env.payload["message_id"],
                    "native_runtime":True, "observations":[],
                })
            client.request = reply
            from zhuangfy.engine_fusion.ipc_transport import HybridTransportUnavailable
            with self.assertRaises(HybridTransportUnavailable):
                await client.submit(context(), event_id="ev4",
                                    message_id="msg4", text="你看", images=[PNG])
            self.assertEqual(list((Path(tmp)/"incoming_images").glob("*.bin")), [])


if __name__ == "__main__":
    unittest.main()
