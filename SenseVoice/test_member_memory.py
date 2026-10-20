"""Offline tests for independent QQ member memories and evidence checks."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from member_memory import MemberKey, MemberMemoryStore, parse_reflection, reflect_member
from llm import LlmSettings


class MemberMemoryTests(unittest.TestCase):
    def test_identity_requires_positive_numeric_ids(self):
        with self.assertRaises(ValueError):
            MemberKey(0, 42)
        with self.assertRaises(ValueError):
            MemberKey(88, "42")

    def test_scoped_accounts_and_erase(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemberMemoryStore(Path(directory) / "members.sqlite3")
            own = MemberKey(88, 42)
            other_user = MemberKey(88, 43)
            other_bot = MemberKey(89, 42)
            store.record_turn(own, "我喜欢 C++ 和 Qt", "明白了")
            self.assertEqual(len(store.pending(own)), 0)  # 6-turn trigger
            store.apply(own, [{"id": 2}], {"profile_add": [
                {"text": "用户喜欢 Qt", "evidence": "我喜欢 C++ 和 Qt"}
            ]})
            self.assertIn("用户喜欢 Qt", store.snapshot(own))
            self.assertEqual(store.snapshot(other_user), "")
            self.assertEqual(store.snapshot(other_bot), "")
            store.erase(own)
            self.assertEqual(store.snapshot(own), "")

    def test_reflection_rejects_untraceable_model_claims(self):
        response = json.dumps({
            "profile_add": [
                {"text": "用户是物理教授", "evidence": "我是一名物理教授"},
                {"text": "用户使用 Python", "evidence": "我在学 Python"},
            ],
            "summary": {"text": "用户刚在练习 Python", "evidence": "我在学 Python"},
        }, ensure_ascii=False)
        notes = parse_reflection(response, ["我在学 Python"])
        self.assertEqual([x["text"] for x in notes["profile_add"]], ["用户使用 Python"])
        self.assertIn("summary", notes)

    def test_pattern_requires_two_independent_messages(self):
        response = json.dumps({"pattern_add": [
            {"text": "重复检查日志", "evidences": ["我先看日志", "还是先看日志"]}
        ]}, ensure_ascii=False)
        self.assertFalse(parse_reflection(response, ["我先看日志，还是先看日志"])["pattern_add"])
        self.assertEqual(len(parse_reflection(response, ["我先看日志", "还是先看日志"])["pattern_add"]), 1)

    def test_six_turn_reflection_and_no_bot_generated_fact(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemberMemoryStore(Path(directory) / "members.sqlite3")
            identity = MemberKey(88, 42)
            for index in range(6):
                store.record_turn(identity, f"我在做项目，第 {index} 次", "你是一名教授")
            self.assertEqual(len(store.pending(identity)), 12)
            plan = json.dumps({"profile_add": [
                {"text": "用户是教授", "evidence": "你是一名教授"},
                {"text": "用户在做项目", "evidence": "我在做项目"},
            ]}, ensure_ascii=False)
            with patch("llm.chat", return_value=plan) as chat:
                self.assertTrue(reflect_member(store, identity, LlmSettings(), "test-key"))
                self.assertTrue(chat.called)
            self.assertEqual(len(store.pending(identity)), 0)
            self.assertIn("用户在做项目", store.snapshot(identity))
            self.assertNotIn("用户是教授", store.snapshot(identity))

    def test_failed_reflection_keeps_pending_records(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemberMemoryStore(Path(directory) / "members.sqlite3")
            key = MemberKey(88, 42)
            for i in range(6):
                store.record_turn(key, f"今天写代码第{i}次", "好的")
            with patch("llm.chat", return_value="invalid JSON"):
                with self.assertRaises(ValueError):
                    reflect_member(store, key, LlmSettings(), "test-key")
            self.assertEqual(len(store.pending(key)), 12)

    def test_secrets_excluded_at_record_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemberMemoryStore(Path(directory) / "members.sqlite3")
            key = MemberKey(88, 42)
            self.assertEqual(store.record_turn(key, "api key 是 abc", "明白"), 0)
            self.assertEqual(store.record_turn(key, "今天挺好", "请不要暴露密码"), 0)


if __name__ == "__main__":
    unittest.main()
