"""Ensure Hybrid calls ORIGINAL ImageManager, not a simplified local VLM.

Offline upstream module doubles only: the real pinned Maisaka VLM and database
are covered by the user's follow-up local runtime acceptance test.
"""
import asyncio
import base64
import hashlib
import sys
import types
import unittest
from unittest.mock import patch

from zhuangfy.engine_fusion.native_runtime_adapter import NativeMaisakaAdapter

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/dAAAAABJRU5ErkJggg=="
)
DIGEST = hashlib.sha256(PNG).hexdigest()


class FakeText:
    def __init__(self, text):
        self.text = text


class FakeImage:
    def __init__(self, *, binary_hash, content, binary_data):
        self.binary_hash = binary_hash
        self.content = content
        self.binary_data = binary_data


class FakeMessage:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeRuntime:
    last = None
    def __init__(self, session_id):
        self.session_id = session_id
        self.messages = []
        self.started = False
        FakeRuntime.last = self
    async def start(self):
        self.started = True
    async def register_message(self, msg):
        self.messages.append(msg)
    async def stop(self):
        self.started = False


class FakeImageManager:
    def __init__(self):
        self.cached = {}
        self.model_requests = 0
        self.saved = []
    def get_cached_image_description(self, digest):
        return self.cached.get(digest, "")
    async def get_image_description(self, *, image_bytes, wait_for_build):
        assert wait_for_build is True
        digest = hashlib.sha256(image_bytes).hexdigest()
        self.saved.append(image_bytes)
        if digest not in self.cached:
            self.model_requests += 1
            self.cached[digest] = "一朵玫瑰，画面中带着玩笑意味"
        return self.cached[digest]


def module_doubles(manager):
    modules = {}
    for pkg in ("src", "src.config", "src.chat", "src.chat.message_receive",
                "src.chat.image_system", "src.common", "src.common.data_models",
                "src.core", "src.maisaka"):
        m = types.ModuleType(pkg)
        m.__path__ = []
        modules[pkg] = m
    for name in ("src.config.config", "src.chat.message_receive.chat_manager",
                 "src.chat.message_receive.message",
                 "src.common.data_models.mai_message_data_model",
                 "src.common.data_models.message_component_data_model",
                 "src.core.local_operator", "src.maisaka.runtime",
                 "src.chat.image_system.image_manager"):
        modules[name] = types.ModuleType(name)
    modules["src.config.config"].global_config = types.SimpleNamespace(
        personality=types.SimpleNamespace(behavior_style=""))
    chat = types.SimpleNamespace(registered=[])
    chat.register_message = chat.registered.append
    async def session(**kw):
        return types.SimpleNamespace(session_id="native-session")
    chat.get_or_create_session = session
    modules["src.chat.message_receive.chat_manager"].chat_manager = chat
    modules["src.chat.message_receive.message"].SessionMessage = FakeMessage
    common = modules["src.common.data_models.mai_message_data_model"]
    common.MessageInfo = FakeMessage
    common.UserInfo = FakeMessage
    comp = modules["src.common.data_models.message_component_data_model"]
    comp.MessageSequence = lambda items: types.SimpleNamespace(components=items)
    comp.TextComponent = FakeText
    comp.ImageComponent = FakeImage
    modules["src.core.local_operator"].LOCAL_PLATFORM_BOT_IDS = {}
    modules["src.maisaka.runtime"].MaisakaHeartFlowChatting = FakeRuntime
    modules["src.chat.image_system.image_manager"].image_manager = manager
    return modules


class OriginalImageManagerUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_manager_save_cache_and_same_turn_image_component(self):
        manager = FakeImageManager()
        modules = module_doubles(manager)
        async def send(*args):
            return "onebot-42"
        adapter = NativeMaisakaAdapter(send)
        adapter.system = object()  # isolate upstream full MainSystem initialization
        with patch.dict(sys.modules, modules):
            obs1 = await adapter.receive(
                "event1", "message1", "qq:456:private:123",
                "你看看这张图", "庄方宜的人格", "",
                images=[("img_" + DIGEST, PNG)])
            self.assertEqual(manager.model_requests, 1)
            self.assertEqual(manager.saved, [PNG])
            self.assertFalse(obs1[0]["cache_hit"])
            self.assertTrue(obs1[0]["description_ready"])
            self.assertEqual(FakeRuntime.last.messages[0].raw_message.components[0].text,
                             "你看看这张图")
            picture = FakeRuntime.last.messages[0].raw_message.components[1]
            self.assertIsInstance(picture, FakeImage)
            self.assertEqual(picture.binary_data, PNG)
            self.assertEqual(picture.binary_hash, DIGEST)
            self.assertEqual(picture.content, obs1[0]["description"])
            self.assertIn("系统视觉观察", FakeRuntime.last.messages[0].processed_plain_text)
            obs2 = await adapter.receive(
                "event2", "message2", "qq:456:private:123", "同一张图",
                "庄方宜的人格", "", images=[("img_" + DIGEST, PNG)])
            self.assertTrue(obs2[0]["cache_hit"])
            self.assertEqual(manager.model_requests, 1)

    async def test_mismatched_digest_never_invokes_native_image_manager(self):
        manager = FakeImageManager()
        adapter = NativeMaisakaAdapter(lambda *_: asyncio.sleep(0))
        adapter.system = object()
        with patch.dict(sys.modules, module_doubles(manager)):
            with self.assertRaises(ValueError):
                await adapter.receive(
                    "evt", "msg", "qq:456:private:123", "图片",
                    "Persona", "", images=[("img_" + "f"*64, PNG)])
            self.assertEqual(manager.model_requests, 0)


if __name__ == "__main__":
    unittest.main()
