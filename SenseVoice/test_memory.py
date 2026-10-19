import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import memory
from chat_worker import ChatWorker
from llm import LlmSettings


class MemoryTests(unittest.TestCase):
    def test_explicit_commands_and_secret_rejection(self):
        self.assertEqual(memory.memory_command("记住我喜欢清淡口味"), ("remember", "我喜欢清淡口味"))
        self.assertEqual(memory.memory_command("请忘记我喜欢清淡口味"), ("forget", "我喜欢清淡口味"))
        self.assertIsNone(memory.memory_command("我忘记带钥匙了"))
        service = memory.MemoryService()
        with self.assertRaisesRegex(ValueError, "密钥"):
            service.remember("API Key 是 test-only-secret")

    def test_correction_replaces_only_confirmed_old_memory(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE messages (content TEXT)")
        connection.execute("CREATE TABLE history (memory_id TEXT, new_memory TEXT)")
        connection.execute("INSERT INTO history VALUES ('old-id', '旧偏好')")
        backend = MagicMock()
        backend.db = SimpleNamespace(connection=connection)
        backend.search.return_value = {"results": [{"id": "old-id", "memory": "用户使用扬声器"}]}
        backend.add.return_value = {"results": [{"id": "new-id", "memory": "用户现在用耳机"}]}
        service = memory.MemoryService()
        service._backend = backend
        with patch.object(service, "_select_ids", return_value=["old-id"]):
            service.record_turn("更正，我现在用耳机", "好的")
        backend.delete.assert_called_once_with("old-id")
        self.assertEqual(connection.execute("SELECT count(*) FROM history WHERE memory_id='old-id'").fetchone()[0], 0)
        self.assertEqual(connection.execute("SELECT count(*) FROM messages").fetchone()[0], 0)
        connection.close()

    def test_unconfirmed_correction_does_not_delete_old_memory(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE messages (content TEXT)")
        backend = MagicMock()
        backend.db = SimpleNamespace(connection=connection)
        backend.search.return_value = {"results": [{"id": "old-id", "memory": "旧偏好"}]}
        backend.add.return_value = {"results": []}
        service = memory.MemoryService()
        service._backend = backend
        service.record_turn("更正，我现在用耳机", "好的")
        backend.delete.assert_not_called()
        connection.close()

    def test_forget_only_deletes_ids_returned_from_candidates(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE history (memory_id TEXT, new_memory TEXT)")
        connection.execute("INSERT INTO history VALUES ('known-id', '旧记忆')")
        backend = MagicMock()
        backend.db = SimpleNamespace(connection=connection)
        backend.search.return_value = {"results": [{"id": "known-id", "memory": "用户喜欢清淡口味"}]}
        service = memory.MemoryService()
        service._backend = backend
        with patch.object(memory, "chat", return_value='{"ids":["known-id","made-up-id"]}'), \
             patch.object(memory, "load_key", return_value="test-only-key"):
            count = service.forget_matching("我喜欢清淡口味")
        self.assertEqual(count, 1)
        backend.delete.assert_called_once_with("known-id")
        self.assertEqual(connection.execute("SELECT count(*) FROM history").fetchone()[0], 0)
        connection.close()

    def test_remember_command_writes_before_acknowledging(self):
        replies = []
        backend = MagicMock()
        worker = ChatWorker(LlmSettings(memory_enabled=True), "test-only-key", [],
                            "记住我喜欢简短解释", None)
        worker.replied.connect(replies.append)
        with patch.object(memory, "get_memory", return_value=backend), \
             patch("chat_worker.chat") as model_chat:
            worker.run()
        backend.remember.assert_called_once_with("我喜欢简短解释")
        model_chat.assert_not_called()
        self.assertIn("记住", replies[0])


if __name__ == "__main__":
    unittest.main()
