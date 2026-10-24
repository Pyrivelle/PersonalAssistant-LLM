"""Offline OneBot event, rate-limit, and WebSocket exchange checks."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlparse
from unittest.mock import AsyncMock, Mock, patch

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

import qq_bridge
import activity_log
from llm import LlmSettings
from qq_bridge import (Conversation, Incoming, QqBridge, QqSettings, ReplyLimiter,
                       parse_message, spoken_reply, starting_relationship_tier)
from turn_planner import PlannerSettings, TurnPlan
from zhuangfy.channels.qq.images import ImageReference, _allowed_url, _encode_jpeg



class QqMemoryCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_private_text_event_has_its_own_memory_receipt(self):
        # Calls the same function scheduled by accept() before debounce.
        bridge = object.__new__(QqBridge)
        bridge.memory_client = Mock()
        bridge.memory_client.onboard_qq.return_value = {
            "created": True, "timeline_consent": True,
            "curation_consent": False,
        }
        bridge.memory_client.receive.side_effect = [
            {"conversation_id": "conv", "entry_id": "entry-1"},
            {"conversation_id": "conv", "entry_id": "entry-2"},
        ]
        bridge._memory_onboarded_accounts = set()
        bridge._memory_timeline_accounts = set()
        bridge._memory_capture_locks = {}
        one = Incoming("private", 42, None, "m1", "第一句话", nickname="梨愿")
        two = Incoming("private", 42, None, "m2", "第二句话", nickname="梨愿")
        first, second = await asyncio.gather(
            bridge._capture_private_message(one, bot_id=88),
            bridge._capture_private_message(two, bot_id=88),
        )
        self.assertEqual(first["entry_id"], "entry-1")
        self.assertEqual(second["entry_id"], "entry-2")
        self.assertEqual(bridge.memory_client.onboard_qq.call_count, 1)
        self.assertEqual([c.kwargs["message_id"] for c in
                          bridge.memory_client.receive.call_args_list],
                         ["m1", "m2"])

    async def test_unconsented_private_member_is_created_but_not_captured(self):
        bridge = object.__new__(QqBridge)
        bridge.memory_client = Mock()
        bridge.memory_client.onboard_qq.return_value = {
            "created": True, "timeline_consent": False,
            "curation_consent": False,
        }
        bridge._memory_onboarded_accounts = set()
        bridge._memory_timeline_accounts = set()
        bridge._memory_capture_locks = {}
        msg = Incoming("private", 42, None, "m3", "不能存储", nickname="梨愿")
        self.assertIsNone(await bridge._capture_private_message(msg, bot_id=88))
        bridge.memory_client.receive.assert_not_called()



class QqParsingTests(unittest.TestCase):
    def test_initial_l0_is_private_only_and_requires_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qq_settings.json"
            path.write_text(json.dumps({"allowed_private_ids": [42],
                                        "initial_l0_private_ids": [42]}), encoding="utf-8")
            settings = QqSettings.load(path)
            self.assertEqual(starting_relationship_tier(settings, "private", 42), "L0")
            self.assertEqual(starting_relationship_tier(settings, "group", 42), "unassigned")
            self.assertEqual(starting_relationship_tier(settings, "private", 43), "L0")
            path.write_text(json.dumps({"allowed_private_ids": [42],
                                        "initial_l0_private_ids": [43]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                QqSettings.load(path)

    def test_spoken_reply_removes_only_leading_stage_directions(self):
        self.assertEqual(spoken_reply("发语音了，轻声清楚的说，晚安，早点休息。"), "晚安，早点休息。")
        self.assertEqual(spoken_reply("（轻声说）晚安，今天辛苦了。"), "晚安，今天辛苦了。")
        self.assertEqual(spoken_reply("我发语音了这件事，你不用担心。"), "我发语音了这件事，你不用担心。")
        self.assertEqual(spoken_reply("晚安，轻声说也没关系。"), "晚安，轻声说也没关系。")

    def setUp(self):
        self.settings = QqSettings(allowed_private_ids=frozenset({42}),
                                   allowed_group_ids=frozenset({9}))

    def test_open_private_access_is_explicit_and_preserves_qq_nickname(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qq_settings.json"
            path.write_text(json.dumps({"allowed_private_ids": [],
                                        "allow_all_private": True}), encoding="utf-8")
            settings = QqSettings.load(path)
        private = {"post_type": "message", "message_type": "private",
                   "user_id": 42, "message_id": 123,
                   "sender": {"nickname": "梨愿"},
                   "message": [{"type": "text", "data": {"text": "你好"}}]}
        incoming = parse_message(private, 88, settings)
        self.assertIsNotNone(incoming)
        self.assertEqual(incoming.nickname, "梨愿")
        self.assertEqual(incoming.user_id, 42)
        self.assertIsNone(parse_message({**private, "user_id": 88}, 88, settings))
        self.assertIsNone(parse_message({**private, "message_type": "group",
                                         "group_id": 7}, 88, settings))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qq_settings.json"
            path.write_text(json.dumps({"allow_all_private": "yes"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                QqSettings.load(path)

    def test_private_allowlist_and_group_mention(self):
        private = {"post_type": "message", "message_type": "private", "user_id": 42,
                   "message_id": 1, "message": [{"type": "text", "data": {"text": "你好"}}]}
        self.assertEqual(parse_message(private, 88, self.settings).text, "你好")
        self.assertIsNone(parse_message({**private, "user_id": 43}, 88, self.settings))
        self.assertIsNone(parse_message({**private, "user_id": 88}, 88, self.settings))
        group = {**private, "message_type": "group", "group_id": 9,
                 "message": [{"type": "at", "data": {"qq": "88"}},
                             {"type": "text", "data": {"text": " 帮我看看"}}]}
        self.assertEqual(parse_message(group, 88, self.settings).text, "帮我看看")
        self.assertIsNone(parse_message({**group, "group_id": 10}, 88, self.settings))
        self.assertIsNone(parse_message({**group, "message": "没有艾特"}, 88, self.settings))
        self.assertEqual(parse_message({**group, "message": "[CQ:at,qq=88] 你好"}, 88, self.settings).text, "你好")

    def test_focus_groups_must_be_explicitly_allowlisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qq_settings.json"
            base = {"allowed_private_ids": [42], "allowed_group_ids": [9]}
            valid = {**base, "focus_mode": "active", "focus_groups": [
                {"id": "friends", "sessions": ["private:42", "group:9"]}
            ]}
            path.write_text(json.dumps(valid), encoding="utf-8")
            cfg = QqSettings.load(path)
            self.assertEqual(cfg.focus_mode, "active")
            self.assertEqual(cfg.focus_groups[0][1], ("private:42", "group:9"))
            for bad in (
                {**base, "focus_mode": "invalid"},
                {**base, "focus_groups": [{"id": "x", "sessions": ["private:43"]}]},
                {**base, "focus_groups": [{"id": "x", "sessions": ["group:99"]}]},
                {**base, "focus_groups": [{"id": "x", "sessions": ["group:9"]},
                                           {"id": "y", "sessions": ["group:9"]}]},
            ):
                path.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(ValueError):
                    QqSettings.load(path)

    def test_focus_checkpoint_must_be_opted_in_and_account_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qq_settings.json"
            allow = {"allowed_private_ids": [42], "focus_mode": "active",
                     "focus_groups": [{"id": "test", "sessions": ["private:42"]}],
                     "focus_checkpoint_enabled": True,
                     "focus_checkpoint_account": "bot_1234"}
            path.write_text(json.dumps(allow), encoding="utf-8")
            settings = QqSettings.load(path)
            self.assertTrue(settings.focus_checkpoint_enabled)
            self.assertEqual(settings.focus_checkpoint_account, "bot_1234")
            for invalid in (
                {**allow, "focus_checkpoint_account": ""},
                {**allow, "focus_mode": "shadow"},
                {**allow, "focus_checkpoint_account": "../../bad"},
                {**allow, "focus_checkpoint_enabled": "true"},
            ):
                path.write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaises(ValueError):
                    QqSettings.load(path)

    def test_passive_group_requires_explicit_allowlist(self):
        settings = QqSettings(allowed_group_ids=frozenset({9}),
                              passive_group_ids=frozenset({9}))
        group = {"post_type": "message", "message_type": "group",
                 "group_id": 9, "user_id": 43, "message_id": 1,
                 "message": [{"type": "text", "data": {"text": "下午好"}}]}
        parsed = parse_message(group, 88, settings)
        self.assertIsNotNone(parsed)
        self.assertFalse(parsed.directed)
        self.assertIsNone(parse_message(group, 88, self.settings))
        self.assertIsNone(parse_message({**group, "group_id": 10}, 88, settings))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qq_settings.json"
            for values in ({"allowed_group_ids": [9], "passive_group_ids": [10]},
                           {"group_reply_frequency": 1.5},
                           {"group_reply_frequency": "NaN"}):
                path.write_text(json.dumps(values), encoding="utf-8")
                with self.assertRaises(ValueError):
                    QqSettings.load(path)

    def test_qq_bridge_loads_only_explicitly_enabled_local_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "skills" / "demo_skill"
            folder.mkdir(parents=True)
            (folder / "skill.json").write_text(json.dumps({
                "id":"demo_skill", "name":"Test skill", "version":"1.0.0",
                "description":"offline", "entry":"skill.py", "api_version":1
            }), encoding="utf-8")
            (folder / "skill.py").write_text(
                "from zhuangfy.agent_v2.runtime import ToolBinding\n"
                "async def call(args): return 'ok'\n"
                "def setup(context):\n"
                " return [ToolBinding('demo_skill.read',call,"
                "frozenset({'private','group'}),description='read-only',"
                "parameters={'type':'object','properties':{},"
                "'additionalProperties':False})]\n",
                encoding="utf-8")
            config = root / "skills_settings.json"
            config.write_text(json.dumps({"skills":{"demo_skill":{
                "enabled":True,"scopes":["private"],"allow_mutation":False}}}),
                encoding="utf-8")
            flags = PlannerSettings(agent_v2_enabled=True, agent_v2_tools_enabled=True)
            with patch.object(qq_bridge, "ROOT", root), \
                 patch.object(qq_bridge.PlannerSettings, "load", return_value=flags):
                bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42})))
                self.assertIn("demo_skill.read",
                              bridge.tool_registry.public_names(scope="private"))
                self.assertNotIn("demo_skill.read",
                                 bridge.tool_registry.public_names(scope="group"))
                self.assertTrue(bridge.skill_manager.statuses[0].loaded)
                config.write_text(json.dumps({"skills":{"demo_skill":{
                    "enabled":False,"scopes":["private"],"allow_mutation":False}}}),
                    encoding="utf-8")
                restarted = QqBridge(QqSettings(allowed_private_ids=frozenset({42})))
                self.assertNotIn("demo_skill.read",
                                 restarted.tool_registry.public_names(scope="private"))

    def test_optional_agent_v2_debounce_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qq_settings.json"
            path.write_text(json.dumps({"message_debounce_mode": "natural"}), encoding="utf-8")
            self.assertEqual(QqSettings.load(path).message_debounce_mode, "natural")
            path.write_text(json.dumps({"message_debounce_mode": "unknown"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "message_debounce_mode"):
                QqSettings.load(path)

    def test_agent_v2_debounce_is_opt_in(self):
        from zhuangfy.agent_v2 import MessageDebouncer
        with patch.object(qq_bridge.PlannerSettings, "load", return_value=PlannerSettings()):
            legacy = QqBridge(QqSettings(allowed_private_ids=frozenset({42})))
            enabled = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                           message_debounce_mode="natural"))
        self.assertEqual(legacy.settings.message_debounce_mode, "off")
        self.assertEqual(enabled.settings.message_debounce_mode, "natural")

    def test_rate_limit_is_per_private_chat_and_per_group(self):
        settings = QqSettings(private_cooldown_seconds=5, group_cooldown_seconds=12,
                              private_max_replies_per_hour=2, group_max_replies_per_hour=1)
        limiter = ReplyLimiter(settings)
        owner = Incoming("private", 42, None, "1", "你好")
        other = Incoming("private", 43, None, "2", "你好")
        group_a = Incoming("group", 42, 9, "3", "你好")
        group_b = Incoming("group", 43, 9, "4", "你好")
        limiter.mark_sent(owner, 100)
        self.assertEqual(limiter.check(owner, 101), (4, False))
        self.assertEqual(limiter.check(other, 101), (0, False))
        limiter.mark_sent(owner, 105)
        self.assertEqual(limiter.check(owner, 110), (0, True))
        self.assertEqual(limiter.check(owner, 3701), (0, False))
        limiter.mark_sent(group_a, 100)
        self.assertEqual(limiter.check(group_b, 120), (0, True))

    def test_private_and_mentioned_group_images_enter_vision_flow(self):
        picture = {"type": "image", "data": {"file": "cache-key", "url": "https://gchat.qpic.cn/picture"}}
        event = {"post_type": "message", "message_type": "private", "user_id": 42,
                 "message_id": 5, "message": [picture]}
        incoming = parse_message(event, 88, self.settings)
        self.assertEqual(incoming.text, qq_bridge.IMAGE_ONLY_PROMPT)
        self.assertEqual(incoming.image, ImageReference("cache-key", "https://gchat.qpic.cn/picture"))
        self.assertEqual(incoming.image_count, 1)
        group = {**event, "message_type": "group", "group_id": 9,
                 "message": [{"type": "at", "data": {"qq": "88"}}, picture]}
        self.assertIsNotNone(parse_message(group, 88, self.settings))
        self.assertIsNone(parse_message({**group, "message": [picture]}, 88, self.settings))
        self.assertIsNone(parse_message({**event, "user_id": 43}, 88, self.settings))
        cq = {**event, "message": "看看这个[CQ:image,file=cache-key,url=https://gchat.qpic.cn/picture]"}
        self.assertEqual(parse_message(cq, 88, self.settings).image.file, "cache-key")
        self.assertEqual(parse_message({**event, "message": [picture, picture]}, 88, self.settings).image_count, 2)

    def test_qq_image_download_host_is_restricted(self):
        self.assertTrue(_allowed_url("https://multimedia.nt.qq.com.cn/image"))
        self.assertTrue(_allowed_url("http://gchat.qpic.cn/gchatpic_new/image"))
        self.assertFalse(_allowed_url("http://127.0.0.1/admin"))
        self.assertFalse(_allowed_url("https://qpic.cn.evil.example/image"))
        self.assertFalse(_allowed_url("file:///C:/secret"))

    def test_png_is_converted_to_bounded_jpeg_for_vision(self):
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage
        picture = QImage(32, 24, QImage.Format.Format_ARGB32)
        picture.fill(0x00FFFFFF)
        source = QBuffer()
        source.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(picture.save(source, "PNG"))
        encoded = _encode_jpeg(bytes(source.data()))
        self.assertTrue(encoded.startswith(b"\xff\xd8"))
        self.assertLess(len(encoded), 2_500_000)
        with self.assertRaisesRegex(ValueError, "尺寸无效"):
            _encode_jpeg(b"not an image")

    def test_settings_reject_remote_endpoint_and_non_finite_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qq_settings.json"
            path.write_text(json.dumps({"ws_url": "ws://example.com:3001/"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "本机"):
                QqSettings.load(path)
            path.write_text(json.dumps({"quiet_seconds": "NaN"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "quiet_seconds"):
                QqSettings.load(path)
            path.write_text(json.dumps({"allowed_private_ids": [42], "voice_private_ids": [43]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "私聊白名单"):
                QqSettings.load(path)
            path.write_text(json.dumps({"private_max_reply_parts": 4}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "private_max_reply_parts"):
                QqSettings.load(path)
            path.write_text(json.dumps({"private_max_reply_parts": 1}), encoding="utf-8")
            self.assertEqual(QqSettings.load(path).private_max_reply_parts, 1)

    def test_explicit_private_context_opt_in_is_scoped(self):
        from dialogue_context import DialogueContext
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / "cognition_settings.json").write_text(
                json.dumps({"enabled": False, "persist_private_ids": [42]}),
                encoding="utf-8",
            )
            settings = QqSettings(allowed_private_ids=frozenset({42}),
                                  allowed_group_ids=frozenset({9}))
            with patch.object(qq_bridge, "ROOT", directory):
                bridge = QqBridge(settings)
                self.assertIsNotNone(bridge.context_store)
                context = DialogueContext()
                context.commit("Qt Bug 报错", "我们一起看看")
                bridge.context_store.save("private:0:42", context)
                other = QqBridge(settings)
                self.assertEqual(other.context_store.load("private:0:42").events[0].topic,
                                 "编程或调试")
                self.assertFalse(other.context_store.load("private:0:43").events)
                self.assertFalse(other.context_store.load("group:9:42").events)

    def test_unapproved_private_persistence_id_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / "cognition_settings.json").write_text(
                json.dumps({"persist_private_ids": [43]}), encoding="utf-8"
            )
            with patch.object(qq_bridge, "ROOT", directory):
                with self.assertRaisesRegex(ValueError, "私聊白名单"):
                    QqBridge(QqSettings(allowed_private_ids=frozenset({42})))

    def test_member_memory_requires_whitelisted_private_user(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cognition_settings.json").write_text(
                json.dumps({"memory_private_ids": [43]}), encoding="utf-8"
            )
            with patch.object(qq_bridge, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "私聊白名单"):
                    QqBridge(QqSettings(allowed_private_ids=frozenset({42})))

    def test_second_bridge_instance_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(qq_bridge, "LOCK_FILE", Path(directory) / "bridge.lock"):
                with qq_bridge.single_instance():
                    with self.assertRaisesRegex(SystemExit, "已经运行"):
                        with qq_bridge.single_instance():
                            pass
                with qq_bridge.single_instance():
                    pass


class QqSocketTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Simulated QQ accounts must never enter the user's durable chat stream.
        self.journal_patch = patch.object(activity_log, "_CHAT_JOURNAL", None)
        self.journal_patch.start()

    def tearDown(self):
        self.journal_patch.stop()

    async def test_snowluma_image_action_refreshes_download_url(self):
        with patch.object(qq_bridge.PlannerSettings, "load", return_value=PlannerSettings()):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42})))
        bridge.action = AsyncMock(return_value={"url": "https://gchat.qpic.cn/new-picture"})
        with patch.object(qq_bridge, "image_data_url", return_value="data:image/jpeg;base64,dGVzdA==") as fetch:
            result = await bridge._image_data_url(ImageReference("cache-key", "https://gchat.qpic.cn/old"))
        self.assertEqual(result, "data:image/jpeg;base64,dGVzdA==")
        bridge.action.assert_awaited_once_with("get_image", {"file": "cache-key"})
        fetch.assert_called_once_with("https://gchat.qpic.cn/new-picture")

    async def test_image_runs_vision_then_reply_with_visual_context(self):
        with patch.object(qq_bridge.PlannerSettings, "load", return_value=PlannerSettings()):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         private_cooldown_seconds=0, quiet_seconds=0))
        image = ImageReference("cache-key", "https://gchat.qpic.cn/picture")
        incoming = Incoming("private", 42, None, "image-1", "这是什么？", image, 1)
        state = Conversation(incoming, pending=[incoming.text], pending_images=[image])
        bridge.send_reply = AsyncMock(return_value=True)
        bridge._image_data_url = AsyncMock(return_value="data:image/jpeg;base64,dGVzdA==")
        calls = []

        def fake_stage(stage, _settings, _key, _history, _text, **kwargs):
            calls.append((stage, kwargs))
            return "图中是一只猫" if stage == "vision" else "这是一只猫。"

        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "can_attempt_stage", return_value=True), \
             patch.object(qq_bridge, "invoke_stage", side_effect=fake_stage):
            await bridge.process(state)
        self.assertEqual([item[0] for item in calls], ["vision", "reply"])
        self.assertIn("data:image/jpeg", calls[0][1]["image_data_url"])
        self.assertEqual(calls[1][1]["visual_context"], "图中是一只猫")
        self.assertEqual(bridge.send_reply.await_args.args[1], "这是一只猫。")
        self.assertEqual(state.context.history[-1]["content"], "这是一只猫。")

    async def test_image_only_is_treated_as_a_shared_picture(self):
        with patch.object(qq_bridge.PlannerSettings, "load", return_value=PlannerSettings()):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         private_cooldown_seconds=0, quiet_seconds=0))
        image = ImageReference("cache-key", "https://gchat.qpic.cn/picture")
        incoming = Incoming("private", 42, None, "image-2", qq_bridge.IMAGE_ONLY_PROMPT, image, 1)
        state = Conversation(incoming, pending=[incoming.text], pending_images=[image])
        bridge.send_reply = AsyncMock(return_value=True)
        bridge._image_data_url = AsyncMock(return_value="data:image/jpeg;base64,dGVzdA==")
        calls = []

        def fake_stage(stage, _settings, _key, _history, user_text, **kwargs):
            calls.append((stage, user_text, kwargs))
            return "粉色背景、玫瑰和爱心营造出轻快的亲近感" if stage == "vision" else "这朵玫瑰像是特意留给人的。"

        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "can_attempt_stage", return_value=True), \
             patch.object(qq_bridge, "invoke_stage", side_effect=fake_stage):
            await bridge.process(state)
        self.assertEqual(calls[0][1], "给你看看这张图。")
        self.assertIn("画面的主体", calls[0][2]["system_prompt"])
        self.assertIn("像回应朋友分享", calls[1][2]["emotion_context"])
        self.assertEqual(bridge.send_reply.await_args.args[1], "这朵玫瑰像是特意留给人的。")

    async def test_image_failure_retries_same_image(self):
        from model_routes import ModelRoutesFailed
        with patch.object(qq_bridge.PlannerSettings, "load", return_value=PlannerSettings()):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         private_cooldown_seconds=0, quiet_seconds=0))
        image = ImageReference("cache-key", "https://gchat.qpic.cn/picture")
        incoming = Incoming("private", 42, None, "image-1", "这是什么？", image, 1)
        state = Conversation(incoming, pending=[incoming.text], pending_images=[image])
        bridge.send_reply = AsyncMock(return_value=True)
        bridge._image_data_url = AsyncMock(return_value="data:image/jpeg;base64,dGVzdA==")
        outputs = [ModelRoutesFailed("vision", [("vision_1", "网络错误")]),
                   "图中是一只猫", "这是一只猫。"]
        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "can_attempt_stage", return_value=True), \
             patch.object(qq_bridge, "invoke_stage", side_effect=outputs):
            await bridge.process(state)
            self.assertEqual(state.last_failed_image, image)
            bridge.send_reply.assert_not_awaited()
            state.incoming = Incoming("private", 42, None, "retry-1", "/retry")
            state.pending = ["/retry"]
            await bridge.process(state)
        self.assertIsNone(state.last_failed_image)
        bridge.send_reply.assert_awaited_once()
        self.assertEqual(bridge.send_reply.await_args.args[1], "这是一只猫。")
        self.assertEqual(bridge._image_data_url.await_count, 2)

    async def test_planner_voice_choice_routes_only_short_private_reply(self):
        with patch.object(qq_bridge.PlannerSettings, "load", return_value=PlannerSettings(enabled=True)):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         voice_private_ids=frozenset({42}),
                                         private_cooldown_seconds=0, quiet_seconds=0))
        bridge.send_reply = AsyncMock(return_value=True)
        state = Conversation(Incoming("private", 42, None, "1", "说给我听"), pending=["说给我听"])
        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "plan_turn", return_value=TurnPlan(confidence=0.9, delivery="voice")), \
             patch.object(qq_bridge, "invoke_stage", return_value="发语音了，轻声清楚的说，好好休息，晚安。"):
            await bridge.process(state)
        bridge.send_reply.assert_awaited_once()
        self.assertEqual(bridge.send_reply.await_args.args[1], "好好休息，晚安。")
        self.assertTrue(bridge.send_reply.await_args.kwargs["voice"])
        self.assertEqual(state.context.history[-1]["content"], "好好休息，晚安。")

    async def test_failed_generation_can_be_retried_without_reentering_text(self):
        from model_routes import ModelRoutesFailed
        with patch.object(qq_bridge.PlannerSettings, "load", return_value=PlannerSettings()):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         private_cooldown_seconds=0, quiet_seconds=0))
        incoming = Incoming("private", 42, None, "a1", "原始问题")
        state = Conversation(incoming, pending=["原始问题"])
        bridge.send_reply = AsyncMock(return_value=True)
        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "can_attempt_stage", return_value=True), \
             patch.object(qq_bridge, "invoke_stage", side_effect=[
                 ModelRoutesFailed("reply", [("reply_1", "网络错误")]), "备用模型恢复了",
             ]) as model:
            await bridge.process(state)
            self.assertEqual(state.last_failed_generation, "原始问题")
            self.assertFalse(state.context.history)
            bridge.send_reply.assert_not_awaited()
            state.incoming = Incoming("private", 42, None, "a2", "/retry")
            state.pending = ["/retry"]
            await bridge.process(state)
            self.assertEqual(state.last_failed_generation, "")
            self.assertEqual(state.context.history[-2]["content"], "原始问题")
            self.assertEqual(state.context.history[-1]["content"], "备用模型恢复了")
            self.assertEqual(model.call_count, 2)
        self.assertEqual(bridge.send_reply.await_count, 1)

    async def test_stage_direction_is_removed_before_snowluma_and_memory(self):
        with patch.object(qq_bridge.PlannerSettings, "load", return_value=PlannerSettings()):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         private_cooldown_seconds=0, quiet_seconds=0))
        incoming = Incoming("private", 42, None, "m1", "你在做什么")
        state = Conversation(incoming, pending=[incoming.text])
        bridge.send_reply = AsyncMock(return_value=True)
        original = "（语气平稳，带点询问）  \n你自己在写哪些方向的东西？"
        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "invoke_stage", return_value=original):
            await bridge.process(state)
        self.assertEqual(bridge.send_reply.await_args.args[1], "你自己在写哪些方向的东西？")
        self.assertEqual(state.context.history[-1]["content"], "你自己在写哪些方向的东西？")

    async def test_two_private_bubbles_commit_once_and_stay_in_order(self):
        with patch.object(qq_bridge.PlannerSettings, "load", return_value=PlannerSettings()):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         private_cooldown_seconds=0, quiet_seconds=0))
        incoming = Incoming("private", 42, None, "m2", "最近如何")
        state = Conversation(incoming, pending=[incoming.text])
        bridge.send_reply = AsyncMock(return_value=True)
        response = ("每天上班之后也要留一点时间给自己，备考和写程序都需要慢慢来。"
                    "也不一定今天就能把所有事情解决。" * 2
                    + "\n\n" + "把自己的工具放到 GitHub 上，让别人有机会看到你的想法。"
                    "这样认识志同道合的人，或许会更自然。" * 2)
        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "invoke_stage", return_value=response) as model:
            await bridge.process(state)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(bridge.send_reply.await_count, 2)
        parts = [call.args[1] for call in bridge.send_reply.await_args_list]
        self.assertIn("上班", parts[0])
        self.assertIn("GitHub", parts[1])
        self.assertEqual(len(state.context.history), 2)
        self.assertEqual(state.context.history[-1]["content"], "\n\n".join(parts))
        self.assertFalse(bridge.limiter.sent_hours[incoming.scope])  # send_reply was mocked

    async def test_failed_second_bubble_is_saved_and_continue_skips_first(self):
        with patch.object(qq_bridge.PlannerSettings, "load", return_value=PlannerSettings()):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         private_cooldown_seconds=0, quiet_seconds=0))
        incoming = Incoming("private", 42, None, "m3", "谈谈我的项目")
        state = Conversation(incoming, pending=[incoming.text])
        bridge.send_reply = AsyncMock(side_effect=[True, False, True])
        response = ("写个人项目需要慢慢积累，能坚持写下来已经很不容易。" * 2 +
                    "\n\n" + "找到兴趣相同的人，也可以从公开一些工具开始。" * 4)
        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "invoke_stage", return_value=response) as model:
            await bridge.process(state)
            self.assertEqual(len(state.partial_replies), 1)
            self.assertEqual(state.partial_replies[0].delivered, 1)
            self.assertFalse(state.context.history)
            state.pending = ["/continue"]
            state.incoming = Incoming("private", 42, None, "m4", "/continue")
            await bridge.process(state)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(bridge.send_reply.await_count, 3)
        self.assertEqual(bridge.send_reply.await_args_list[1].args[1],
                         bridge.send_reply.await_args_list[2].args[1])
        self.assertNotEqual(bridge.send_reply.await_args_list[0].args[1],
                            bridge.send_reply.await_args_list[2].args[1])
        self.assertTrue(bridge.send_reply.await_args_list[2].kwargs["continuation"])
        self.assertFalse(state.partial_replies)
        self.assertEqual(len(state.context.history), 2)
        self.assertEqual(state.context.history[0]["content"], incoming.text)

    async def test_passive_group_frequency_gate_holds_without_calling_llm(self):
        # Passive groups are opt-in and never enter the legacy always-reply path.
        base = QqSettings(allowed_group_ids=frozenset({9}),
                          passive_group_ids=frozenset({9}),
                          group_reply_frequency=0.0, quiet_seconds=0,
                          group_cooldown_seconds=0)
        message = {"post_type": "message", "message_type": "group",
                   "group_id": 9, "user_id": 42, "message_id": 501,
                   "message": [{"type": "text", "data": {"text": "你们好"}}]}
        with patch.object(qq_bridge.PlannerSettings, "load",
                          return_value=PlannerSettings()):
            legacy = QqBridge(base)
        legacy.bot_id = 88
        self.assertFalse(legacy.accept(message))
        with patch.object(qq_bridge.PlannerSettings, "load",
                          return_value=PlannerSettings(agent_v2_enabled=True)):
            bridge = QqBridge(base)
        bridge.bot_id = 88
        bridge.send_reply = AsyncMock(return_value=True)
        with patch.object(qq_bridge, "plan_turn_v2") as planner:
            self.assertTrue(bridge.accept(message))
            state = bridge.conversations["bot:88:group:9"]
            await asyncio.wait_for(state.task, timeout=3)
        planner.assert_not_called()
        bridge.send_reply.assert_not_awaited()
        self.assertTrue(state.group_recent)
        self.assertFalse(state.context.history)
        # Different senders must share one group conversation identity.
        self.assertTrue(bridge.accept({**message, "user_id": 43, "message_id": 502}))
        await asyncio.wait_for(state.task, timeout=3)
        self.assertEqual(len(bridge.conversations), 1)

    async def test_opted_in_passive_group_can_generate_one_reply(self):
        from zhuangfy.agent_v2.planner import PlanDecision
        settings = QqSettings(allowed_group_ids=frozenset({9}),
                              passive_group_ids=frozenset({9}),
                              group_reply_frequency=1.0,
                              quiet_seconds=0, group_cooldown_seconds=0)
        with patch.object(qq_bridge.PlannerSettings, "load",
                          return_value=PlannerSettings(agent_v2_enabled=True)):
            bridge = QqBridge(settings)
        bridge.bot_id = 88
        bridge.send_reply = AsyncMock(return_value=True)
        event = {"post_type":"message", "message_type":"group",
                 "group_id":9, "user_id":42, "message_id":601,
                 "message":[{"type":"text","data":{"text":"大家觉得这个技术怎么样？"}}]}
        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "plan_turn_v2", return_value=PlanDecision(
                 action="reply", confidence=.98)), \
             patch.object(qq_bridge, "invoke_stage", return_value="我觉得值得试试。"):
            self.assertTrue(bridge.accept(event))
            state = bridge.conversations["bot:88:group:9"]
            await asyncio.wait_for(state.task, timeout=3)
        bridge.send_reply.assert_awaited_once()
        self.assertEqual(len(state.context.history), 2)
        self.assertEqual(bridge.turn_scheduler.metrics("qq:88:group:9",
                         now=__import__("time").monotonic())["actual_replies"], 1.0)
        self.assertTrue(any(is_bot for _, is_bot in state.group_activity))

    async def test_v2_multi_round_tools_feed_receipt_to_replyer(self):
        from zhuangfy.agent_v2.planner import PlanDecision
        with patch.object(qq_bridge.PlannerSettings, "load",
                          return_value=PlannerSettings(agent_v2_enabled=True,
                                                       agent_v2_tools_enabled=True)):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         quiet_seconds=0, private_cooldown_seconds=0))
        incoming = Incoming("private", 42, None, "calc-1", "十加十五是多少？")
        state = Conversation(incoming, pending=[incoming.text])
        bridge.send_reply = AsyncMock(return_value=True)
        requests = []
        def planner(*args, **kwargs):
            requests.append(kwargs)
            if not kwargs.get("tool_feedback"):
                return PlanDecision(action="tool", confidence=.96,
                                    tool_name="calculator.evaluate",
                                    tool_arguments={"expression":"10+15"})
            return PlanDecision(action="reply", confidence=.96,
                                social_intent="计算并回答")
        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "plan_turn_v2", side_effect=planner), \
             patch.object(qq_bridge, "invoke_stage", return_value="结果是 25。") as reply_model:
            await bridge.process(state)
        self.assertEqual(len(requests), 2)
        self.assertIn("calculator.evaluate", requests[0]["available_tools"])
        self.assertIn("expression", requests[0]["tool_catalog"])
        self.assertIn("25", requests[1]["tool_feedback"])
        self.assertIn("25", reply_model.call_args.kwargs["context_notes"])
        bridge.send_reply.assert_awaited_once()
        self.assertEqual(state.context.history[-1]["content"], "结果是 25。")

    async def test_v3_calls_memory_only_when_planner_chooses(self):
        from zhuangfy.agent_v2.planner import PlanDecision
        with patch.object(qq_bridge.PlannerSettings, "load",
                          return_value=PlannerSettings(
                              agent_v2_enabled=True, agent_v2_tools_enabled=True,
                              agent_v3_enabled=True)):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         quiet_seconds=0, private_cooldown_seconds=0))
        bridge.bot_id = 88
        incoming = Incoming("private", 42, None, "mem-001",
                            "方宜，还记得之前的 OSG 项目吗？")
        state = Conversation(incoming, pending=[incoming.text],
                             pending_event_ids=["mem-001"])
        captured = asyncio.get_running_loop().create_future()
        captured.set_result({"entry_id": "entry-001", "conversation_id": "conv-001"})
        bridge._memory_capture_tasks[(88, 42, "mem-001")] = captured
        bridge.memory_client = Mock()
        bridge.memory_client.task_recall.return_value = {"tasks": [{
            "task_id": "goal-osg",
            "origin": {"excerpt": "我们讨论过 OSG 港口性能优化"},
            "reported_progress": {"state": "active", "status_basis": "user_report"},
            "history": [],
        }]}
        bridge.send_reply = AsyncMock(return_value=True)
        decisions = []
        def planner(*args, **kwargs):
            decisions.append(kwargs)
            if not kwargs.get("tool_feedback"):
                return PlanDecision(action="tool", confidence=.98,
                                    tool_name="task_memory.search",
                                    tool_arguments={"query": "OSG 港口优化"})
            return PlanDecision(action="reply", confidence=.95,
                                social_intent="回忆曾经讨论的项目")
        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "plan_turn_v3", side_effect=planner), \
             patch.object(qq_bridge, "invoke_stage", return_value="当然记得，我们之前讨论过港口优化。"):
            await bridge.process(state)
        self.assertEqual(len(decisions), 2)
        self.assertIn("task_memory.search", decisions[0]["available_tools"])
        self.assertIn("query", decisions[0]["tool_catalog"])
        self.assertIn("OSG 港口性能优化", decisions[1]["tool_feedback"])
        self.assertEqual(bridge.memory_client.task_recall.call_count, 1)
        passed_identity = bridge.memory_client.task_recall.call_args.args[0]
        self.assertEqual(passed_identity.channel_account_id, "88")
        self.assertEqual(passed_identity.external_user_id, "42")
        # The V3 Skill is the only trigger. No eager recall of older memories.
        bridge.memory_client.recall.assert_not_called()
        self.assertIn("用户报告", decisions[1]["tool_feedback"])
        bridge.send_reply.assert_awaited_once()

    async def test_v3_social_reply_does_not_search_memory(self):
        from zhuangfy.agent_v2.planner import PlanDecision
        with patch.object(qq_bridge.PlannerSettings, "load",
                          return_value=PlannerSettings(
                              agent_v2_enabled=True, agent_v2_tools_enabled=True,
                              agent_v3_enabled=True)):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         quiet_seconds=0, private_cooldown_seconds=0))
        bridge.bot_id = 88
        incoming = Incoming("private", 42, None, "mem-002", "今天心情不错。")
        state = Conversation(incoming, pending=[incoming.text],
                             pending_event_ids=["mem-002"])
        capture = asyncio.get_running_loop().create_future()
        capture.set_result({"entry_id": "entry-002", "conversation_id": "conv-002"})
        bridge._memory_capture_tasks[(88, 42, "mem-002")] = capture
        bridge.memory_client = Mock()
        bridge.send_reply = AsyncMock(return_value=True)
        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "plan_turn_v3", return_value=PlanDecision(
                 action="reply", confidence=.98, social_intent="开心分享")), \
             patch.object(qq_bridge, "invoke_stage", return_value="听起来今天挺愉快呀。"):
            await bridge.process(state)
        bridge.memory_client.task_recall.assert_not_called()
        bridge.memory_client.recall.assert_not_called()
        bridge.send_reply.assert_awaited_once()

    async def test_v2_disabled_tools_never_execute_device_actions(self):
        from zhuangfy.agent_v2.planner import PlanDecision
        with patch.object(qq_bridge.PlannerSettings, "load",
                          return_value=PlannerSettings(agent_v2_enabled=True,
                                                       agent_v2_tools_enabled=True)):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         quiet_seconds=0, private_cooldown_seconds=0))
        incoming = Incoming("private", 42, None, "device-1", "打开我的灯")
        state = Conversation(incoming, pending=[incoming.text])
        bridge.send_reply = AsyncMock(return_value=True)
        bridge.tool_registry.invoke = AsyncMock()
        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "plan_turn_v2", return_value=PlanDecision(
                 action="tool", confidence=.96, tool_name="mijia.turn_on",
                 tool_arguments={"device":"lamp"})), \
             patch.object(qq_bridge, "invoke_stage", return_value="还没有接入控制台灯的工具。"):
            await bridge.process(state)
        bridge.tool_registry.invoke.assert_not_awaited()
        bridge.send_reply.assert_awaited_once()

    async def test_qq_focus_shadow_observes_real_reply_without_sharing_history(self):
        from zhuangfy.agent_v2.planner import PlanDecision
        settings = QqSettings(allowed_private_ids=frozenset({42}),
                              quiet_seconds=0, private_cooldown_seconds=0,
                              focus_mode="shadow",
                              focus_groups=(("test", ("private:42",)),))
        with patch.object(qq_bridge.PlannerSettings, "load",
                          return_value=PlannerSettings(agent_v2_enabled=True)):
            bridge = QqBridge(settings)
        incoming = Incoming("private", 42, None, "focus-msg", "你好")
        state = Conversation(incoming, pending=["你好"])
        bridge.send_reply = AsyncMock(return_value=True)
        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "plan_turn_v2",
                          return_value=PlanDecision(action="reply", confidence=.99)), \
             patch.object(qq_bridge, "invoke_stage", return_value="你好呀。"):
            await bridge.process(state)
        self.assertEqual(len(bridge.focus_runtime.decisions), 1)
        self.assertTrue(bridge.focus_runtime.decisions[0].admitted)
        self.assertEqual(len(state.context.history), 2)
        self.assertFalse(bridge.focus_runtime.snapshot()["test"]["pending_sessions"])

    async def test_agent_v2_planner_wait_then_reply_once(self):
        from zhuangfy.agent_v2.planner import PlanDecision
        with patch.object(qq_bridge.PlannerSettings, "load",
                          return_value=PlannerSettings(agent_v2_enabled=True,
                                                       agent_v2_wait_max_seconds=1)):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         quiet_seconds=0, private_cooldown_seconds=0))
        incoming = Incoming("private", 42, None, "v2-1", "还有一句")
        state = Conversation(incoming, pending=["还有一句"])
        bridge.send_reply = AsyncMock(return_value=True)
        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "plan_turn_v2", return_value=PlanDecision(
                 action="wait", confidence=0.9, wait_seconds=1)) as planner, \
             patch.object(qq_bridge, "invoke_stage", return_value="嗯，我听着。") as model:
            await bridge.process(state)
        self.assertEqual(planner.call_count, 2)
        self.assertEqual(model.call_count, 1)
        bridge.send_reply.assert_awaited_once()
        self.assertFalse(state.pending)
        self.assertEqual(state.planner_waits, 0)

    async def test_agent_v2_stop_requires_explicit_permission(self):
        from zhuangfy.agent_v2.planner import PlanDecision
        for permitted in (False, True):
            with self.subTest(permitted=permitted):
                with patch.object(qq_bridge.PlannerSettings, "load",
                                  return_value=PlannerSettings(agent_v2_enabled=True,
                                                               agent_v2_allow_silence_group=permitted)):
                    bridge = QqBridge(QqSettings(allowed_group_ids=frozenset({9}),
                                                 group_cooldown_seconds=0, quiet_seconds=0))
                current = Incoming("group", 42, 9, "v2-g", "我也想听听")
                state = Conversation(current, pending=[current.text])
                bridge.send_reply = AsyncMock(return_value=True)
                with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
                     patch.object(qq_bridge, "load_key", return_value="test-key"), \
                     patch.object(qq_bridge, "plan_turn_v2",
                                  return_value=PlanDecision(action="stop", confidence=0.95)), \
                     patch.object(qq_bridge, "invoke_stage", return_value="我也有想法。") as model:
                    await bridge.process(state)
                self.assertEqual(model.call_count, 0 if permitted else 1)
                self.assertEqual(bridge.send_reply.await_count, 0 if permitted else 1)

    async def test_agent_v2_replyer_receives_social_intent(self):
        from zhuangfy.agent_v2.planner import PlanDecision
        with patch.object(qq_bridge.PlannerSettings, "load",
                          return_value=PlannerSettings(agent_v2_enabled=True)):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         quiet_seconds=0, private_cooldown_seconds=0))
        current = Incoming("private", 42, None, "v2-affection", "好喜欢")
        state = Conversation(current, pending=[current.text])
        bridge.send_reply = AsyncMock(return_value=True)
        with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
             patch.object(qq_bridge, "load_key", return_value="test-key"), \
             patch.object(qq_bridge, "plan_turn_v2",
                          return_value=PlanDecision(
                              action="reply", confidence=0.9,
                              social_intent="对角色表达好感", reply_act="温柔接纳",
                              reply_style="short", avoid=("不要罗列图片",))), \
             patch.object(qq_bridge, "invoke_stage", return_value="我也很开心。") as model:
            await bridge.process(state)
        self.assertEqual(model.call_count, 1)
        self.assertIn("对角色表达好感", model.call_args.kwargs["emotion_context"])
        self.assertIn("不要罗列图片", model.call_args.kwargs["emotion_context"])
        bridge.send_reply.assert_awaited_once()

    async def test_continuation_bubble_is_one_logical_rate_limit_slot(self):
        with patch.object(qq_bridge.PlannerSettings, "load", return_value=PlannerSettings()):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         private_cooldown_seconds=0, quiet_seconds=0))
        incoming = Incoming("private", 42, None, "m5", "你好")
        bridge.action = AsyncMock(return_value={"message_id": 1})
        sent, uncertain = await bridge._send_reply_parts(incoming, ("第一段。", "第二段。"))
        self.assertEqual((sent, uncertain), (2, False))
        self.assertEqual(bridge.action.await_count, 2)
        self.assertEqual(len(bridge.limiter.sent_hours[incoming.scope]), 1)

    async def test_voice_reply_uses_record_segment_once_and_cleans_temporary_wav(self):
        with patch.object(qq_bridge.PlannerSettings, "load", return_value=PlannerSettings()):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         voice_private_ids=frozenset({42}),
                                         private_cooldown_seconds=0))
        incoming = Incoming("private", 42, None, "1", "给我听听")
        paths = []
        async def fake_action(name, params, **kwargs):
            self.assertEqual(name, "send_private_msg")
            self.assertEqual(params["message"][0]["type"], "record")
            url = params["message"][0]["data"]["file"]
            uri_path = unquote(urlparse(url).path)
            path = Path(uri_path.lstrip("/") if os.name == "nt" else uri_path)
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), b"RIFF1234WAVE")
            paths.append(path)
            return {"message_id": 1}
        bridge.action = fake_action
        with patch.object(qq_bridge, "synthesize_qq_voice", return_value=b"RIFF1234WAVE"):
            self.assertTrue(await bridge.send_reply(incoming, "你好", voice=True))
        self.assertEqual(len(paths), 1)
        self.assertFalse(paths[0].exists())

    async def test_voice_synthesis_failure_falls_back_to_single_text_reply(self):
        with patch.object(qq_bridge.PlannerSettings, "load", return_value=PlannerSettings()):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                         voice_private_ids=frozenset({42}),
                                         private_cooldown_seconds=0))
        bridge.action = AsyncMock(return_value={"message_id": 1})
        with patch.object(qq_bridge, "synthesize_qq_voice", side_effect=RuntimeError("offline")):
            self.assertTrue(await bridge.send_reply(Incoming("private", 42, None, "1", "你好"),
                                                    "文字回退", voice=True))
        bridge.action.assert_awaited_once_with("send_private_msg", {
            "user_id": 42, "message": "文字回退", "auto_escape": True})

    async def test_private_forget_command_clears_both_scoped_stores(self):
        from dialogue_context import DialogueContext
        from member_memory import MemberKey
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cognition_settings.json").write_text(
                json.dumps({"memory_private_ids": [42], "persist_private_ids": [42]}),
                encoding="utf-8")
            with patch.object(qq_bridge, "ROOT", root):
                bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                             private_cooldown_seconds=0, quiet_seconds=0))
                bridge.bot_id = 88
                identity = MemberKey(88, 42)
                bridge.member_store.apply(identity, [{"id": 2}],
                    {"profile_add": [{"text": "用户喜欢编程", "evidence": "我喜欢编程"}]})
                context = DialogueContext()
                context.commit("代码遇到问题", "我帮你看看")
                bridge.context_store.save("bot:88:private:0:42", context)
                incoming = Incoming("private", 42, None, "1", "/forget_me")
                state = Conversation(incoming, pending=["/forget_me"], context=context)
                bridge.send_reply = AsyncMock(return_value=True)
                await bridge.process(state)
                self.assertEqual(bridge.member_store.snapshot(identity), "")
                self.assertFalse(bridge.context_store.load("bot:88:private:0:42").history)
                self.assertFalse(state.context.history)
                bridge.send_reply.assert_awaited_once()

    async def test_private_reply_receives_only_its_own_member_profile(self):
        from member_memory import MemberKey
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cognition_settings.json").write_text(
                json.dumps({"memory_private_ids": [42]}), encoding="utf-8")
            with patch.object(qq_bridge, "ROOT", root):
                bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42}),
                                             private_cooldown_seconds=0, quiet_seconds=0))
                bridge.bot_id = 88
                bridge.member_store.apply(MemberKey(88, 42), [{"id": 1}], {
                    "profile_add": [{"text": "用户在学习 C++", "evidence": "我在学习 C++"}],
                })
                bridge.member_store.apply(MemberKey(89, 42), [{"id": 2}], {
                    "profile_add": [{"text": "另一个机器人账号的秘密", "evidence": "我的秘密"}],
                })
                seen = {}
                def fake_chat(_stage, _settings, _key, _history, _message, **kwargs):
                    seen["notes"] = kwargs.get("context_notes", "")
                    return "我们继续聊吧"
                incoming = Incoming("private", 42, None, "123", "你好")
                state = Conversation(incoming, pending=["你好"])
                bridge.send_reply = AsyncMock(return_value=True)
                with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
                     patch.object(qq_bridge, "load_key", return_value="test-key"), \
                     patch.object(qq_bridge, "invoke_stage", side_effect=fake_chat):
                    await bridge.process(state)
                self.assertIn("用户在学习 C++", seen["notes"])
                self.assertNotIn("另一个机器人账号的秘密", seen["notes"])
                bridge.send_reply.assert_awaited_once()

    async def test_mock_snowluma_receives_one_reply_and_ignores_duplicate(self):
        sent = asyncio.Event()
        actions = []

        async def fake_snowluma(ws):
            try:
                async for raw in ws:
                    request = json.loads(raw)
                    actions.append(request)
                    if request["action"] == "get_login_info":
                        await ws.send(json.dumps({"status": "ok", "retcode": 0,
                                                  "data": {"user_id": 88}, "echo": request["echo"]}))
                        event = {"post_type": "message", "message_type": "private",
                                 "user_id": 42, "message_id": 123,
                                 "message": [{"type": "text", "data": {"text": "你好"}}]}
                        await ws.send(json.dumps(event, ensure_ascii=False))
                        await ws.send(json.dumps(event, ensure_ascii=False))
                    elif request["action"] == "send_private_msg":
                        await ws.send(json.dumps({"status": "ok", "retcode": 0,
                                                  "data": {"message_id": 456}, "echo": request["echo"]}))
                        sent.set()
            except ConnectionClosed:
                pass

        async with serve(fake_snowluma, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            settings = QqSettings(ws_url=f"ws://127.0.0.1:{port}/",
                                  allowed_private_ids=frozenset({42}),
                                  private_cooldown_seconds=0, quiet_seconds=0)
            with patch.object(qq_bridge, "load_settings", return_value=LlmSettings()), \
                 patch.object(qq_bridge, "load_key", return_value="test-key"), \
                 patch.object(qq_bridge.PlannerSettings, "load", return_value=PlannerSettings()), \
                 patch.object(qq_bridge, "invoke_stage", return_value="你好，我是庄方宜"), \
                 patch.object(qq_bridge, "IDENTITY_FILE", Path(tempfile.gettempdir()) / "shimano-test-qq-identity.json"):
                bridge = QqBridge(settings)
                task = asyncio.create_task(bridge.run_forever())
                try:
                    await asyncio.wait_for(sent.wait(), timeout=5)
                    await asyncio.sleep(0.05)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            self.assertEqual([item["action"] for item in actions],
                             ["get_login_info", "send_private_msg"])
            self.assertEqual(actions[1]["params"]["user_id"], 42)
            self.assertEqual(actions[1]["params"]["message"], "你好，我是庄方宜")


if __name__ == "__main__":
    unittest.main()
