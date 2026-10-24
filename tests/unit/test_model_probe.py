"""A connection test exercises one selected completion route without chat state."""
import unittest
from unittest.mock import patch

from zhuangfy.llm.model_probe import PROBE_PROMPT, describe_probe_error, probe_model
from zhuangfy.llm.model_registry import ProviderConnection, RegisteredModel
from zhuangfy.llm.providers import ModelRequestError


class ModelProbeTests(unittest.TestCase):
    def setUp(self):
        self.connection = ProviderConnection("provider_1", "硅基流动", "siliconflow",
                                             "https://api.siliconflow.cn/v1")
        self.model = RegisteredModel("probe_one", "测试模型", "provider_1",
                                     "deepseek-ai/DeepSeek-V4-Flash", thinking="disabled")

    def test_probe_uses_selected_model_and_builtin_prompt(self):
        with patch("zhuangfy.llm.model_probe.load_route_key", return_value="test-key"), \
             patch("zhuangfy.llm.model_probe.chat", return_value="连接正常") as request:
            result = probe_model(self.connection, self.model)
        self.assertEqual(result.answer, "连接正常")
        args, kwargs = request.call_args
        self.assertEqual(args[0].model, self.model.model_id)
        self.assertEqual(args[1], "test-key")
        self.assertEqual(args[2], [])
        self.assertEqual(args[3], PROBE_PROMPT)
        self.assertEqual(kwargs["thinking"], "disabled")
        self.assertEqual(kwargs["timeout"], 45.0)

    def test_missing_key_does_not_send_request(self):
        with patch("zhuangfy.llm.model_probe.load_route_key", return_value=""), \
             patch("zhuangfy.llm.model_probe.chat") as request:
            with self.assertRaisesRegex(ValueError, "API Key"):
                probe_model(self.connection, self.model)
        request.assert_not_called()

    def test_error_messages_are_actionable(self):
        self.assertIn("编辑厂商", describe_probe_error(
            ModelRequestError("hidden", kind="http", status=401)))
        self.assertIn("模型 ID", describe_probe_error(
            ModelRequestError("hidden", kind="http", status=404)))
        self.assertNotIn("hidden", describe_probe_error(
            ModelRequestError("hidden", kind="network")))


if __name__ == "__main__":
    unittest.main()
