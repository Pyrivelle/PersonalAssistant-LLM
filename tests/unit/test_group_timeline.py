"""Opt-in QQ group messages stay group-scoped and retain sender provenance."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zhuangfy.group_timeline import GroupTimeline


class GroupTimelineTests(unittest.TestCase):
    def test_all_messages_saved_and_last_sixty_are_formatted(self):
        with tempfile.TemporaryDirectory() as folder:
            store = GroupTimeline(Path(folder) / "group.sqlite3")
            for index in range(65):
                self.assertTrue(store.record(
                    bot_id=88, group_id=9, message_id=str(index),
                    sender_id=42 + index % 2, nickname=f"群友{index % 2}",
                    content=f"消息{index}"))
            self.assertFalse(store.record(
                bot_id=88, group_id=9, message_id="64", sender_id=42,
                nickname="重复", content="重复"))
            rows = store.recent(bot_id=88, group_id=9, limit=60)
            self.assertEqual(len(rows), 60)
            self.assertEqual(rows[0]["content"], "消息5")
            self.assertEqual(rows[-1]["content"], "消息64")
            context = store.context(bot_id=88, group_id=9,
                                    before_message_id="64")
            self.assertIn("群友1（QQ 43）：消息63", context)
            self.assertNotIn("消息64", context)
            self.assertEqual(store.recent(bot_id=88, group_id=10), [])
            long_text = "长" * 2000
            store.record(bot_id=88, group_id=9, message_id="long",
                         sender_id=42, nickname="群友0", content=long_text)
            self.assertEqual(store.recent(bot_id=88, group_id=9, limit=1)[0]["content"],
                             long_text)
            members = store.members(bot_id=88, group_id=9)
            self.assertEqual(len(members), 2)
            self.assertEqual(sum(item["message_count"] for item in members), 66)
            pending = store.pending_capture(bot_id=88, group_ids=frozenset({9}))
            self.assertEqual(len(pending), 66)
            store.mark_capture(bot_id=88, group_id=9, message_id="long",
                               state="captured")
            self.assertEqual(len(store.pending_capture(
                bot_id=88, group_ids=frozenset({9}))), 65)

    def test_existing_group_database_is_migrated(self):
        import sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "old.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("""CREATE TABLE group_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
                    message_id TEXT NOT NULL, sender_id INTEGER NOT NULL,
                    nickname TEXT NOT NULL, content TEXT NOT NULL,
                    image_count INTEGER NOT NULL DEFAULT 0,
                    received_at REAL NOT NULL,
                    UNIQUE(bot_id,group_id,message_id))""")
            store = GroupTimeline(path)
            self.assertTrue(store.record(bot_id=88, group_id=9, message_id="first",
                                         sender_id=42, nickname="阿蓝", content="你好"))
            self.assertEqual(store.pending_capture(bot_id=88,
                                                   group_ids=frozenset({9}))[0]["content"],
                             "你好")
