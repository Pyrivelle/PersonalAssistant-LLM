"""Persistent dashboard chat history and conversation isolation."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from chat_sessions import ChatSessions
from chat_worker import ChatWorker
from llm import LlmSettings
from turn_planner import PlannerSettings


class ChatSessionsTests(unittest.TestCase):
    def test_sessions_keep_separate_transcripts_and_contexts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatSessions(Path(directory) / "chats.sqlite3")
            first = store.create()
            second = store.create()
            store.append(first, "user", "今天先聊记忆设计")
            store.append(first, "assistant", "我们从身份开始。")
            _, context, _ = store.load(first)
            context.commit("今天先聊记忆设计", "我们从身份开始。")
            store.save_context(first, context)
            store.append(second, "user", "另一个对话")

            reloaded = ChatSessions(Path(directory) / "chats.sqlite3")
            title, saved, messages = reloaded.load(first)
            self.assertEqual(title, "今天先聊记忆设计")
            self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
            self.assertEqual(saved.history[-1]["content"], "我们从身份开始。")
            self.assertEqual(len(reloaded.load(second)[2]), 1)

            reloaded.delete(first)
            self.assertEqual(reloaded.list_chats()[0]["id"], second)
            with self.assertRaises(KeyError):
                reloaded.load(first)

    def test_text_chat_uses_its_session_id_with_shared_desktop_identity(self):
        client = MagicMock()
        client.receive.return_value = {"conversation_id": "c", "entry_id": "e"}
        client.recall.return_value = {"memories": []}
        replies = []
        worker = ChatWorker(LlmSettings(), "test-key", [], "你好", None,
                            source="文字", conversation_id="desktop:chat:abc")
        worker.replied.connect(replies.append)
        with patch.dict("os.environ", {"ZHUANGFY_MEMORY_DESKTOP_USER_ID": "owner-id"}), \
             patch("chat_worker.MemoryClient.from_environment", return_value=client), \
             patch("chat_worker.PlannerSettings.load", return_value=PlannerSettings()), \
             patch("chat_worker.invoke_stage", return_value="你好，我在。"), \
             patch("chat_worker.fangyi_guidance", return_value=""):
            worker.run()
        self.assertEqual(replies, ["你好，我在。"])
        self.assertEqual(client.receive.call_args.kwargs["chat_id"], "desktop:chat:abc")
        self.assertEqual(client.receive.call_args.args[0].external_user_id, "owner-id")

    def test_image_stays_with_local_message_and_is_deleted_with_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chats.sqlite3"
            store = ChatSessions(path)
            chat_id = store.create()
            store.append(chat_id, "user", "这是什么？", image=b"image-data")
            self.assertEqual(store.load(chat_id)[2][0]["image"], b"image-data")
            store.delete(chat_id)
            with self.assertRaises(KeyError):
                store.load(chat_id)

    def test_image_worker_uses_vision_then_reply_without_putting_binary_in_memory(self):
        worker = ChatWorker(LlmSettings(), "test-key", [], "这是什么？", None,
                            source="文字", image=b"sample-image")
        replies = []
        worker.replied.connect(replies.append)
        with patch("chat_worker.MemoryClient.from_environment", return_value=None), \
             patch("chat_worker.PlannerSettings.load", return_value=PlannerSettings()), \
             patch("chat_worker.invoke_stage", side_effect=["图片里有一只猫", "这是一只猫。"] ) as request, \
             patch("chat_worker.fangyi_guidance", return_value=""):
            worker.run()
        self.assertEqual(replies, ["这是一只猫。"])
        self.assertEqual([call.args[0] for call in request.call_args_list], ["vision", "reply"])
        self.assertEqual(request.call_args_list[1].kwargs["visual_context"], "图片里有一只猫")
        self.assertNotIn("sample-image", request.call_args_list[1].kwargs["visual_context"])


if __name__ == "__main__":
    unittest.main()
