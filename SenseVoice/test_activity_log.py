"""The console stream is bounded, private to this launch, and redacted."""

import json
import unittest
from collections import deque
from unittest.mock import patch

import activity_log


class ActivityLogTests(unittest.TestCase):
    def setUp(self):
        self.events_patch = patch.object(activity_log, "_EVENTS", deque(maxlen=2000))
        self.revision_patch = patch.object(activity_log, "_REVISION", 0)
        self.env_patch = patch.dict("os.environ", {"ZHUANGFY_EVENT_PORT": "", "ZHUANGFY_EVENT_TOKEN": ""})
        self.events_patch.start()
        self.revision_patch.start()
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.revision_patch.stop()
        self.events_patch.stop()

    def test_current_session_stream_redacts_credentials(self):
        activity_log.log_event("QQ", "消息", "你好，sk-test-secret-12345678")
        activity_log.log_event("模型", "调用", "Bearer test-secret-12345678")
        revision, entries = activity_log.session_snapshot()
        self.assertEqual(revision, 2)
        self.assertEqual([entry["source"] for entry in entries], ["QQ", "模型"])
        raw = json.dumps(entries, ensure_ascii=False)
        self.assertNotIn("sk-test-secret-12345678", raw)
        self.assertNotIn("test-secret-12345678", raw)
        self.assertIn("[已隐藏]", raw)

    def test_child_events_require_session_token_and_keep_bounded_memory(self):
        entry = {"time": "2026-10-20T22:59:04.588+08:00", "pid": 42,
                 "source": "模型", "phase": "思考内容", "detail": '{"tone":"关切"}'}
        packet = json.dumps({"token": "secret", "event": entry}).encode("utf-8")
        self.assertFalse(activity_log.receive_event(packet, "wrong"))
        self.assertTrue(activity_log.receive_event(packet, "secret"))
        self.assertIn("关切", activity_log.format_event(activity_log.session_snapshot()[1][0]))
        for index in range(2001):
            activity_log.log_event("测试", "事件", index)
        revision, entries = activity_log.session_snapshot()
        self.assertEqual(revision, 2002)
        self.assertEqual(len(entries), 2000)
        self.assertEqual(entries[-1]["detail"], "2000")
