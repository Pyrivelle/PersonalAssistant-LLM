import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import llm
from persona import DEFAULT_PROMPT, LEGACY_DEFAULT_PROMPT


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return b'{"choices":[{"message":{"content":"Hello from the model"}}]}'


class LlmTests(unittest.TestCase):
    def test_settings_and_key_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(llm, "ENV_FILE", Path(directory) / ".env.local"), patch.object(llm, "SETTINGS_FILE", Path(directory) / "settings.json"):
                settings = llm.LlmSettings(model="test/model", memory_enabled=True)
                llm.save_settings(settings)
                llm.save_key("siliconflow", "test-only-secret")
                self.assertEqual(llm.load_key("siliconflow"), "test-only-secret")
                self.assertEqual(llm.load_settings().model, "test/model")
                self.assertTrue(llm.load_settings().memory_enabled)
                self.assertNotIn("test-only-secret", llm.SETTINGS_FILE.read_text(encoding="utf-8"))
                llm.save_key("siliconflow", "replacement-secret")
                self.assertEqual(llm.load_key("siliconflow"), "replacement-secret")

    def test_chat_builds_history_and_reads_reply(self):
        captured = {}

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = request.headers
            captured["body"] = json.loads(request.data)
            return FakeResponse()

        with patch.object(llm.urllib.request, "urlopen", side_effect=fake_open):
            reply = llm.chat(llm.LlmSettings(), "test-key", [{"role": "user", "content": "上句"}], "你好")
        self.assertEqual(reply, "Hello from the model")
        self.assertEqual(captured["url"], "https://api.siliconflow.cn/v1/chat/completions")
        self.assertEqual(captured["body"]["messages"][-1], {"role": "user", "content": "你好"})
        self.assertEqual(captured["body"]["messages"][1], {"role": "user", "content": "上句"})
        self.assertEqual(captured["body"]["messages"][0], {"role": "system", "content": DEFAULT_PROMPT})
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-key")

    def test_old_default_upgrades_without_changing_model_or_custom_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(llm, "SETTINGS_FILE", Path(directory) / "settings.json"):
                llm.save_settings(llm.LlmSettings(model="chosen/model", system_prompt=LEGACY_DEFAULT_PROMPT))
                upgraded = llm.load_settings()
                self.assertEqual(upgraded.model, "chosen/model")
                self.assertEqual(upgraded.system_prompt, DEFAULT_PROMPT)
                llm.save_settings(llm.LlmSettings(model="chosen/model", system_prompt="用户自己的角色设定"))
                self.assertEqual(llm.load_settings().system_prompt, "用户自己的角色设定")

    def test_recalled_memories_are_data_before_current_message(self):
        captured = {}

        def fake_open(request, timeout):
            captured["messages"] = json.loads(request.data)["messages"]
            return FakeResponse()

        with patch.object(llm.urllib.request, "urlopen", side_effect=fake_open):
            llm.chat(llm.LlmSettings(), "test-key", [], "你记得我吗？",
                     memories=[{"memory": "用户偏好简短解释"}])
        self.assertEqual(captured["messages"][-1]["content"], "你记得我吗？")
        self.assertIn("用户偏好简短解释", captured["messages"][-2]["content"])

    def test_remote_http_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            llm.LlmSettings(provider="custom", base_url="http://remote.example/v1").validated()


if __name__ == "__main__":
    unittest.main()
