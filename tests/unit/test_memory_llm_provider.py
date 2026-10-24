"""Curator request options stay scoped to supported Kimi endpoints."""
import io
import json
import unittest
from unittest.mock import patch

from zhuangfy.memory.llm_provider import ChatCompletionsCurator


class _Opener:
    def __init__(self):
        self.body = None

    def open(self, request, timeout):
        self.body = json.loads(request.data)
        return io.BytesIO(b'{"choices":[{"message":{"content":"{\\"operations\\":[]}"}}]}')


class CuratorProviderTests(unittest.TestCase):
    def test_siliconflow_kimi_requests_bounded_direct_json(self):
        opener = _Opener()
        with patch("zhuangfy.memory.llm_provider.build_opener", return_value=opener), \
             patch.dict("os.environ", {"ZHUANGFY_MEMORY_ALLOW_REMOTE": "1"}):
            model = ChatCompletionsCurator(
                "https://api.siliconflow.cn/v1", "Pro/moonshotai/Kimi-K2.6", "private-key")
            self.assertEqual(model.extract(system_prompt="return JSON", payload={}), '{"operations":[]}')
        self.assertEqual(opener.body["response_format"], {"type": "json_object"})
        self.assertFalse(opener.body["enable_thinking"])
        self.assertEqual(opener.body["max_tokens"], 3072)


if __name__ == "__main__":
    unittest.main()
