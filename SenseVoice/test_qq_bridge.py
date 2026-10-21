"""Offline OneBot event, rate-limit, and WebSocket exchange checks."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlparse
from unittest.mock import AsyncMock, patch

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

import qq_bridge
from llm import LlmSettings
from qq_bridge import Conversation, Incoming, QqBridge, QqSettings, ReplyLimiter, parse_message, spoken_reply
from turn_planner import PlannerSettings, TurnPlan


class QqParsingTests(unittest.TestCase):
    def test_spoken_reply_removes_only_leading_stage_directions(self):
        self.assertEqual(spoken_reply("发语音了，轻声清楚的说，晚安，早点休息。"), "晚安，早点休息。")
        self.assertEqual(spoken_reply("（轻声说）晚安，今天辛苦了。"), "晚安，今天辛苦了。")
        self.assertEqual(spoken_reply("我发语音了这件事，你不用担心。"), "我发语音了这件事，你不用担心。")
        self.assertEqual(spoken_reply("晚安，轻声说也没关系。"), "晚安，轻声说也没关系。")

    def setUp(self):
        self.settings = QqSettings(allowed_private_ids=frozenset({42}),
                                   allowed_group_ids=frozenset({9}))

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
