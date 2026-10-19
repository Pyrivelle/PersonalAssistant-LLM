"""Offline OneBot event, rate-limit, and WebSocket exchange checks."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

import qq_bridge
from llm import LlmSettings
from qq_bridge import Incoming, QqBridge, QqSettings, ReplyLimiter, parse_message


class QqParsingTests(unittest.TestCase):
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


class QqSocketTests(unittest.IsolatedAsyncioTestCase):
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
                 patch.object(qq_bridge, "chat", return_value="你好，我是庄方宜"):
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
