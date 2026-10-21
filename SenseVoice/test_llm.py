import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import llm
from persona import DEFAULT_PROMPT, LEGACY_DEFAULT_PROMPT, PREVIOUS_SHIMANO_DEFAULT_PROMPT
from persona_packs import ZHUANG_FANGYI_V1, matching_pack


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

    def test_planning_response_is_visible_in_current_session(self):
        with patch.object(llm.urllib.request, "urlopen", return_value=FakeResponse()), \
                patch.object(llm, "log_event") as record:
            llm.chat(llm.LlmSettings(), "test-key", [], "你好", stage="思考")
        self.assertIn(("模型", "思考内容", "Hello from the model"),
                      [call.args for call in record.call_args_list])

    def test_old_default_upgrades_without_changing_model_or_custom_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(llm, "SETTINGS_FILE", Path(directory) / "settings.json"):
                llm.save_settings(llm.LlmSettings(model="chosen/model", system_prompt=LEGACY_DEFAULT_PROMPT))
                upgraded = llm.load_settings()
                self.assertEqual(upgraded.model, "chosen/model")
                self.assertEqual(upgraded.system_prompt, DEFAULT_PROMPT)
                llm.save_settings(llm.LlmSettings(model="chosen/model", system_prompt="用户自己的角色设定"))
                self.assertEqual(llm.load_settings().system_prompt, "用户自己的角色设定")

    def test_formal_persona_pack_and_previous_default_migration(self):
        self.assertEqual(DEFAULT_PROMPT, ZHUANG_FANGYI_V1.prompt())
        self.assertEqual(matching_pack(DEFAULT_PROMPT), ZHUANG_FANGYI_V1)
        self.assertIsNone(matching_pack(DEFAULT_PROMPT + "\n我的定制要求"))
        self.assertIn("她关心的是具体的人", DEFAULT_PROMPT)
        self.assertNotIn("Shimano", DEFAULT_PROMPT)
        self.assertNotIn("你是庄方宜", DEFAULT_PROMPT)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(llm, "SETTINGS_FILE", Path(directory) / "settings.json"):
                llm.save_settings(llm.LlmSettings(system_prompt=PREVIOUS_SHIMANO_DEFAULT_PROMPT))
                self.assertEqual(llm.load_settings().system_prompt, DEFAULT_PROMPT)
                customized = PREVIOUS_SHIMANO_DEFAULT_PROMPT + "\n用户自定义：称呼我为小朱"
                llm.save_settings(llm.LlmSettings(system_prompt=customized))
                self.assertEqual(llm.load_settings().system_prompt, customized)

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

    def test_emotion_context_is_bounded_and_keeps_current_user_last(self):
        captured = {}

        def fake_open(request, timeout):
            captured["messages"] = json.loads(request.data)["messages"]
            return FakeResponse()

        with patch.object(llm.urllib.request, "urlopen", side_effect=fake_open):
            llm.chat(llm.LlmSettings(), "test-key", [], "今天很累",
                     emotion_context="当前心境：关切" * 200)
        self.assertEqual(captured["messages"][-1],
                         {"role": "user", "content": "今天很累"})
        self.assertEqual(captured["messages"][1]["role"], "system")
        self.assertLessEqual(len(captured["messages"][1]["content"]), 2000)

    def test_remote_http_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            llm.LlmSettings(provider="custom", base_url="http://remote.example/v1").validated()

    def test_deepseek_official_endpoint_and_separate_key(self):
        settings = llm.LlmSettings(provider="deepseek", base_url=llm.DEEPSEEK_URL, model="deepseek-flash")
        self.assertEqual(settings.validated().base_url, "https://api.deepseek.com")
        with self.assertRaisesRegex(ValueError, "官方 API 地址"):
            llm.LlmSettings(provider="deepseek", base_url="https://example.com/v1", model="deepseek-flash").validated()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(llm, "ENV_FILE", Path(directory) / ".env.local"):
                llm.save_key("deepseek", "deepseek-test-key")
                self.assertEqual(llm.load_key("deepseek"), "deepseek-test-key")
                self.assertEqual(llm.load_key("siliconflow"), "")

    def test_kimi_regions_and_hachimi_official_endpoint(self):
        self.assertEqual(llm.LlmSettings(provider="kimi_cn", base_url=llm.KIMI_CN_URL,
                                         model="kimi-k2.6").validated().base_url, llm.KIMI_CN_URL)
        self.assertEqual(llm.LlmSettings(provider="kimi_global", base_url=llm.KIMI_GLOBAL_URL,
                                         model="kimi-k2.6").validated().base_url, llm.KIMI_GLOBAL_URL)
        with self.assertRaisesRegex(ValueError, "官方 API 地址"):
            llm.LlmSettings(provider="kimi_cn", base_url=llm.KIMI_GLOBAL_URL,
                            model="kimi-k2.6").validated()
        self.assertEqual(llm.LlmSettings(provider="hachimi", base_url=llm.HACHIMI_URL,
                                         model="DeepSeek-V4-Flash").validated().base_url, llm.HACHIMI_URL)
        with self.assertRaisesRegex(ValueError, "官方 API 地址"):
            llm.LlmSettings(provider="hachimi", base_url="https://other.example/v1",
                            model="DeepSeek-V4-Flash").validated()

    def test_hachimi_public_catalog_is_available_without_key(self):
        requested = {}

        class PublicResponse(FakeResponse):
            def read(self, _limit):
                return b'{"success":true,"data":[{"model_name":"DeepSeek-V4-Flash"},{"model_name":"GLM-5.1"}]}'

        def fake_open(request, timeout):
            requested["url"] = request.full_url
            requested["headers"] = request.headers
            return PublicResponse()

        settings = llm.LlmSettings(provider="hachimi", base_url=llm.HACHIMI_URL,
                                   model="DeepSeek-V4-Flash")
        with patch.object(llm.urllib.request, "urlopen", side_effect=fake_open):
            models = llm.list_models(settings, "")
        self.assertEqual(models, ["DeepSeek-V4-Flash", "GLM-5.1"])
        self.assertEqual(requested["url"], llm.HACHIMI_PUBLIC_MODELS_URL)
        self.assertNotIn("Authorization", requested["headers"])

    def test_model_catalog_reads_ids_without_chat_request(self):
        captured = {}

        class ModelsResponse(FakeResponse):
            def read(self, _limit):
                return b'{"data":[{"id":"deepseek-v4-pro"},{"id":"deepseek-flash"},{"id":"deepseek-flash"}]}'

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["headers"] = request.headers
            return ModelsResponse()

        with patch.object(llm.urllib.request, "urlopen", side_effect=fake_open):
            models = llm.list_models(llm.LlmSettings(provider="deepseek", base_url=llm.DEEPSEEK_URL,
                                                      model="deepseek-flash"), "test-key")
        self.assertEqual(models, ["deepseek-flash", "deepseek-v4-pro"])
        self.assertEqual(captured["url"], "https://api.deepseek.com/models")
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-key")


if __name__ == "__main__":
    unittest.main()
