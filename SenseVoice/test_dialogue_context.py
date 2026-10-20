"""Offline regression tests for rolling context and isolated persistence."""
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from dialogue_context import DialogueContext, DialogueStore, RECENT_MESSAGES, MAX_NOTE_CHARS


class DialogueContextTests(unittest.TestCase):
    def test_more_than_six_turns_still_have_older_cues(self):
        state = DialogueContext()
        for i in range(18):
            state.commit(f"问题编号 {i}，我现在正在写程序", f"我了解第 {i} 个问题")
        self.assertEqual(len(state.history), RECENT_MESSAGES)
        self.assertIn("问题编号 11", state.prompt())
        self.assertIn("问题编号 17", state.history[-2]["content"])
        self.assertLessEqual(len("\n".join(state.older_notes)), MAX_NOTE_CHARS)

    def test_emotional_event_resolves_only_on_user_report(self):
        state = DialogueContext()
        state.commit("这个 Qt Bug 一直报错", "我们查一下日志")
        self.assertEqual(state.events[-1].status, "待跟进")
        state.commit("先换个话题", "当然可以")
        self.assertEqual(state.events[-1].status, "待跟进")
        state.commit("终于解决了！", "太好了")
        self.assertEqual(state.events[-1].status, "已解决（用户报告）")

    def test_cannot_archive_raw_secrets(self):
        state = DialogueContext()
        state.commit("我的 api key 是 123", "收到")
        for i in range(6):
            state.commit(f"闲聊{i}", "好的")
        self.assertNotIn("api key", state.prompt().lower())

    def test_store_keeps_each_scope_isolated_and_can_delete(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = DialogueStore(Path(temporary) / "memory.sqlite")
            one = DialogueContext()
            one.commit("Python 报错", "查一下")
            store.save("private:0:42", one)
            self.assertEqual(store.load("private:0:42").events[-1].topic, "编程或调试")
            self.assertFalse(store.load("private:0:43").events)
            store.delete("private:0:42")
            self.assertFalse(store.load("private:0:42").history)

    def test_expired_scope_not_loaded(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = DialogueStore(Path(temporary) / "memory.sqlite")
            context = DialogueContext()
            context.commit("今天好开心", "真好")
            store.save("private:0:42", context)
            with sqlite3.connect(store.path) as connection:
                connection.execute("UPDATE sessions SET updated_at = 1")
            self.assertFalse(store.load("private:0:42").history)

    def test_bad_or_old_data_loads_as_empty(self):
        self.assertFalse(DialogueContext.from_dict({"version": 9}).history)
        self.assertFalse(DialogueContext.from_dict(None).history)


if __name__ == "__main__":
    unittest.main()
