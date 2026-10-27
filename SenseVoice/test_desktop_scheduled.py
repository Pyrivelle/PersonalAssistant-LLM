"""Desktop timer display acknowledgement stays behind the visible UI."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from chat_page import ChatPage
from chat_sessions import ChatSessions
from zhuangfy.agent_v2.scheduled_tasks import parse_scheduled_intent


class DesktopScheduledTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_delivery_requires_visible_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = MagicMock(project_root=root, project_data_root=root)
            with patch("chat_page.Paths.current", return_value=paths), \
                 patch("chat_page.channel_user_id", return_value="a" * 32):
                page = ChatPage(store=ChatSessions(root / "chats.sqlite3"))
                try:
                    page._schedule("一分钟后提醒我喝水",
                                   parse_scheduled_intent("一分钟后提醒我喝水"))
                    task = page.scheduled_store.due_for_channel("desktop", 10**12)[0]
                    page._scheduled_reply(task.id, page.chat_id, "该喝水了。")
                    self.assertEqual(page.scheduled_store.get(task.id).status, "pending")
                    page.show()
                    self.app.processEvents()
                    page._scheduled_reply(task.id, page.chat_id, "该喝水了。")
                    self.assertEqual(page.store.load(page.chat_id)[2][-1]["content"],
                                     "该喝水了。")
                    self.assertEqual(page.scheduled_store.get(task.id).status, "delivered")
                finally:
                    page.shutdown()
                    page.close()
