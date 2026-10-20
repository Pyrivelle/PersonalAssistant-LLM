"""Offline checks for the explicit QQ-to-voice memory link."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import owner_memory
from chat_worker import ChatWorker
from llm import LlmSettings
from member_memory import MemberMemoryStore
from turn_planner import TurnPlan
from PySide6.QtWidgets import QApplication, QWidget


class OwnerMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_link_requires_allowlist_memory_consent_and_matching_bot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "link.json"
            with patch("qq_bridge.QqSettings.load", return_value=SimpleNamespace(allowed_private_ids={42})), \
                 patch("owner_memory.PlannerSettings.load", return_value=SimpleNamespace(memory_private_ids={42})), \
                 patch("owner_memory.current_bot_id", return_value=88):
                self.assertEqual(owner_memory.save_link(88, 42, path), owner_memory.OwnerLink(88, 42))
                self.assertEqual(owner_memory.load_link(path), owner_memory.OwnerLink(88, 42))
                with self.assertRaisesRegex(ValueError, "机器人账号"):
                    owner_memory.save_link(89, 42, path)
                with self.assertRaisesRegex(ValueError, "白名单"):
                    owner_memory.save_link(88, 43, path)
            with patch("qq_bridge.QqSettings.load", return_value=SimpleNamespace(allowed_private_ids={42})), \
                 patch("owner_memory.PlannerSettings.load", return_value=SimpleNamespace(memory_private_ids=set())), \
                 patch("owner_memory.current_bot_id", return_value=88):
                self.assertIsNone(owner_memory.load_link(path))

    def test_voice_reads_and_writes_only_linked_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "members.sqlite3"
            link = owner_memory.OwnerLink(88, 42)
            other = owner_memory.OwnerLink(88, 43)
            store = MemberMemoryStore(db_path)
            store.apply(link.key, [{"id": 0}], {"profile_add": [
                {"text": "用户喜欢 Qt", "evidence": "我喜欢 Qt"}]})
            settings = LlmSettings(memory_enabled=False)
            parent = QWidget()
            replies = []
            captured = []
            with patch.object(owner_memory, "MEMORY_FILE", db_path), \
                 patch("owner_memory.load_link", return_value=link), \
                 patch("chat_worker.plan_turn", return_value=TurnPlan()), \
                 patch("chat_worker.chat", side_effect=lambda *_args, **kwargs: captured.append(kwargs) or "好，我们继续。"):
                worker = ChatWorker(settings, "test-key", [], "我回家了", parent, source="语音")
                worker.replied.connect(replies.append)
                worker.run()
                self.assertEqual(replies, ["好，我们继续。"])
                self.assertIn("用户喜欢 Qt", captured[0]["context_notes"])
                owner_memory.record_voice_turn(link, "我回家了", replies[0], settings, "")
            self.assertEqual(store.pending(other.key), [])
            self.assertIn("我回家了", store.recent_inbound(link.key))
            self.assertEqual(store.recent_inbound(other.key), "")
            with store._connect() as db:
                own_count = db.execute("SELECT COUNT(*) FROM inbox WHERE bot_id=88 AND sender_id=42").fetchone()[0]
                other_count = db.execute("SELECT COUNT(*) FROM inbox WHERE bot_id=88 AND sender_id=43").fetchone()[0]
            self.assertEqual((own_count, other_count), (2, 0))
            parent.close()
