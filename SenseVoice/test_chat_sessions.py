"""Persistent dashboard chat history and conversation isolation."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from chat_sessions import ChatSessions
from chat_worker import ChatWorker
from llm import LlmSettings
from turn_planner import PlannerSettings
from zhuangfy.agent_v2.planner import PlanDecision


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
        with tempfile.TemporaryDirectory() as directory:
            worker = ChatWorker(LlmSettings(), "test-key", [], "这是什么？", None,
                                source="文字", image=b"sample-image")
            replies = []
            worker.replied.connect(replies.append)
            paths = MagicMock(project_data_root=Path(directory))
            with patch("chat_worker.Paths.current", return_value=paths), \
                 patch("chat_worker.MemoryClient.from_environment", return_value=None), \
                 patch("chat_worker.PlannerSettings.load", return_value=PlannerSettings()), \
                 patch("chat_worker.invoke_stage", side_effect=["图片里有一只猫", "这是一只猫。"] ) as request, \
                 patch("chat_worker.fangyi_guidance", return_value=""):
                worker.run()
        self.assertEqual(replies, ["这是一只猫。"])
        self.assertEqual([call.args[0] for call in request.call_args_list], ["vision", "reply"])
        self.assertEqual(request.call_args_list[1].kwargs["visual_context"], "图片里有一只猫")
        self.assertNotIn("sample-image", request.call_args_list[1].kwargs["visual_context"])

    def test_desktop_v3_executes_builtin_tool_and_passes_receipt_to_replyer(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = ChatWorker(LlmSettings(), "test-key", [], "请算 2+2", None,
                                source="文字", conversation_id="desktop:chat:tool-test")
            replies = []
            worker.replied.connect(replies.append)
            plans = [PlanDecision(action="tool", tool_name="calculator.evaluate",
                                  tool_arguments={"expression": "2+2"}),
                     PlanDecision(action="reply", reply_act="给出计算结果")]
            paths = MagicMock(project_data_root=Path(directory))
            config = PlannerSettings(agent_v2_enabled=True, agent_v3_enabled=True,
                                     agent_v2_tools_enabled=True)
            with patch("chat_worker.Paths.current", return_value=paths), \
                 patch("chat_worker.MemoryClient.from_environment", return_value=None), \
                 patch("chat_worker.PlannerSettings.load", return_value=config), \
                 patch("chat_worker.plan_turn_v3", side_effect=plans) as planner, \
                 patch("chat_worker.invoke_stage", return_value="结果是四。") as replyer, \
                 patch("chat_worker.fangyi_guidance", return_value=""):
                worker.run()
            self.assertEqual(replies, ["结果是四。"])
            self.assertEqual(planner.call_count, 2)
            self.assertIn("4", replyer.call_args.kwargs["tool_context"])


if __name__ == "__main__":
    unittest.main()
