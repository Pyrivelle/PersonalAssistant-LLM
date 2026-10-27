"""Persistence and shared numbered model routing."""
import tempfile
import unittest
from pathlib import Path

from zhuangfy.llm.api_profiles import ApiProfile, ApiProfiles, load_profiles, save_profiles
from zhuangfy.llm.routes import ModelRoutes
from zhuangfy.observability.chat_journal import ChatJournal


class ApiProfilesAndJournalTests(unittest.TestCase):
    def test_numbered_api_can_serve_multiple_stages_without_duplicate_definition(self):
        profile = ApiProfile("api_1", "custom", "http://127.0.0.1:11434/v1", "local")
        config = ApiProfiles((profile,), ("api_1",), ("api_1",), (), "api_1")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            save_profiles(config, path)
            self.assertEqual(load_profiles(path), config)
            self.assertNotIn("secret", path.read_text(encoding="utf-8"))
        routes = config.materialize(ModelRoutes())
        self.assertEqual(routes.planner.candidates[0], routes.reply.candidates[0])

    def test_chat_events_survive_reopening_local_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.sqlite3"
            event = {"turn_id": "turn-1", "channel": "QQ", "source": "QQ",
                     "phase": "消息", "detail": "你好"}
            journal = ChatJournal(path)
            journal.append(event)
            journal.append({"turn_id": "", "channel": "", "source": "模型",
                            "phase": "调用", "detail": "background"})
            self.assertEqual(ChatJournal(path).recent(), [event])

    def test_model_input_for_chat_turn_survives_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.sqlite3"
            event = {"turn_id": "turn-1", "channel": "QQ", "source": "模型",
                     "phase": "输入原句",
                     "detail": '{"stage":"回复","model":"replyer","text":"你好"}'}
            ChatJournal(path).append(event)
            self.assertEqual(ChatJournal(path).recent(), [event])

    def test_system_prompt_receipt_survives_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.sqlite3"
            event = {"turn_id": "turn-1", "channel": "QQ", "source": "模型",
                     "phase": "系统提示词",
                     "detail": '{"stage":"思考","system_sha256":"abc"}'}
            ChatJournal(path).append(event)
            self.assertEqual(ChatJournal(path).recent(), [event])

    def test_search_receipt_survives_reopening_local_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.sqlite3"
            event = {"turn_id": "turn-search", "channel": "QQ", "source": "QQ",
                     "phase": "搜索结果",
                     "detail": "private:0:42 · 第1次搜索 · 成功\n"
                               "[1] 官方资料 | https://example.org/news | 摘要"}
            ChatJournal(path).append(event)
            self.assertEqual(ChatJournal(path).recent(), [event])

    def test_image_tool_and_delivery_events_survive_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.sqlite3"
            events = [
                {"turn_id": "turn-1", "channel": "QQ", "source": "QQ",
                 "phase": "识图内容", "detail": '{"description":"一只猫"}'},
                {"turn_id": "turn-1", "channel": "QQ", "source": "Agent",
                 "phase": "工具开始", "detail": "P1 · search"},
                {"turn_id": "turn-1", "channel": "QQ", "source": "QQ",
                 "phase": "未完全投递", "detail": "0/2 段已确认"},
            ]
            journal = ChatJournal(path)
            for item in events:
                journal.append(item)
            self.assertEqual(ChatJournal(path).recent(), events)


if __name__ == "__main__":
    unittest.main()
