"""The new memory route never reads or writes a legacy store in parallel."""

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from chat_worker import ChatWorker
import memory
import owner_memory
from llm import LlmSettings
from qq_bridge import QqBridge, QqSettings
from turn_planner import PlannerSettings, TurnPlan
from zhuangfy.memory.routing import legacy_memory_allowed, new_memory_selected


class MemoryCutoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_route_stays_on_new_service_when_it_is_offline(self):
        with patch.dict(os.environ, {"ZHUANGFY_MEMORY_QQ": "1",
                                     "ZHUANGFY_MEMORY_DESKTOP": "1"}):
            self.assertTrue(new_memory_selected("qq"))
            self.assertFalse(legacy_memory_allowed("qq"))
            self.assertFalse(legacy_memory_allowed("desktop"))
            with self.assertRaisesRegex(RuntimeError, "已接管"):
                memory.get_memory()
            self.assertEqual(owner_memory.memory_snapshot(owner_memory.OwnerLink(1, 42)), "")
            self.assertEqual(owner_memory.record_voice_turn(
                owner_memory.OwnerLink(1, 42), "你好", "你好", LlmSettings(), ""), 0)

    def test_qq_bridge_does_not_open_legacy_member_store(self):
        planner = PlannerSettings(memory_private_ids=frozenset({42}))
        with patch.dict(os.environ, {"ZHUANGFY_MEMORY_QQ": "1"}), \
             patch("qq_bridge.PlannerSettings.load", return_value=planner), \
             patch("qq_bridge.MemberMemoryStore") as old_store, \
             patch("qq_bridge.MemoryClient.from_environment", return_value=Mock()):
            bridge = QqBridge(QqSettings(allowed_private_ids=frozenset({42})))
        self.assertIsNone(bridge.member_store)
        old_store.assert_not_called()
        self.assertIsNotNone(bridge.memory_client)

    def test_desktop_uses_only_new_memory_for_reply(self):
        parent = QWidget()
        client = Mock()
        client.receive.return_value = {"conversation_id": "chat-1", "entry_id": "entry-1"}
        client.recall.return_value = {"memories": [{"content": "用户喜欢 Qt"}]}
        replies = []
        worker = ChatWorker(LlmSettings(memory_enabled=True), "key", [],
                            "继续讨论 Qt", parent, source="语音")
        worker.replied.connect(replies.append)
        captured = []
        with patch.dict(os.environ, {"ZHUANGFY_MEMORY_DESKTOP": "1",
                                     "ZHUANGFY_MEMORY_DESKTOP_USER_ID": "owner"}), \
             patch("memory.get_memory") as old_store, \
             patch("owner_memory.load_link") as old_owner, \
             patch("chat_worker.MemoryClient.from_environment", return_value=client), \
             patch("chat_worker.plan_turn", return_value=TurnPlan()), \
             patch("chat_worker.invoke_stage", side_effect=lambda *a, **kw: captured.append(kw) or "好的"):
            worker.run()
        self.assertEqual(replies, ["好的"])
        self.assertEqual(worker.remote_memory_ref["entry_id"], "entry-1")
        self.assertIn("用户喜欢 Qt", captured[0]["context_notes"])
        self.assertEqual(captured[0]["memories"], [])
        old_store.assert_not_called()
        old_owner.assert_not_called()
        parent.close()

    def test_explicit_remember_waits_for_new_curator(self):
        parent = QWidget()
        client = Mock()
        client.receive.return_value = {"conversation_id": "chat-1", "entry_id": "entry-1"}
        client.recall.return_value = {"memories": []}
        replies = []
        worker = ChatWorker(LlmSettings(memory_enabled=True), "key", [],
                            "记住我喜欢 Qt", parent)
        worker.replied.connect(replies.append)
        with patch.dict(os.environ, {"ZHUANGFY_MEMORY_DESKTOP": "1",
                                     "ZHUANGFY_MEMORY_DESKTOP_USER_ID": "owner"}), \
             patch("memory.get_memory") as old_store, \
             patch("chat_worker.MemoryClient.from_environment", return_value=client), \
             patch("chat_worker.invoke_stage") as reply_model:
            worker.run()
        self.assertIn("下一次整理", replies[0])
        self.assertFalse(worker.memory_command)
        self.assertIsNotNone(worker.remote_memory_ref)
        old_store.assert_not_called()
        reply_model.assert_not_called()
        parent.close()

    def test_explicit_forget_does_not_delete_from_old_store(self):
        parent = QWidget()
        replies = []
        worker = ChatWorker(LlmSettings(memory_enabled=True), "key", [],
                            "请忘记我喜欢 Qt", parent)
        worker.replied.connect(replies.append)
        with patch.dict(os.environ, {"ZHUANGFY_MEMORY_DESKTOP": "1"}), \
             patch("memory.get_memory") as old_store, \
             patch("chat_worker.MemoryClient.from_environment") as remote:
            worker.run()
        self.assertIn("没有执行删除", replies[0])
        old_store.assert_not_called()
        remote.assert_not_called()
        parent.close()


if __name__ == "__main__":
    unittest.main()
