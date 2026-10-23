import io
import unittest
import json
from unittest.mock import patch

from zhuangfy.llm import providers
from zhuangfy.llm.providers import _read_completion_stream, ModelRequestError


class PlannerStreamTests(unittest.TestCase):
    def test_collects_reasoning_and_content_until_done(self):
        stream = io.BytesIO(
            b'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"hello "}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"world"},"finish_reason":"stop"}]}\n\n'
            b'data: [DONE]\n\n')
        self.assertEqual(_read_completion_stream(stream), ("hello world", "thinking"))

    def test_disconnect_during_reasoning_is_not_success(self):
        stream = io.BytesIO(b'data: {"choices":[{"delta":{"reasoning_content":"partial"}}]}\n\n')
        with self.assertRaises(ModelRequestError):
            _read_completion_stream(stream)

    def test_finish_reason_without_done_is_complete(self):
        stream = io.BytesIO(b'data: {"choices":[{"delta":{"content":"yes"},"finish_reason":"stop"}]}\n')
        self.assertEqual(_read_completion_stream(stream)[0], "yes")

    def test_planner_requests_stream_and_returns_only_complete_content(self):
        class Response(io.BytesIO):
            headers = {"Content-Type": "text/event-stream; charset=utf-8"}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                self.close()

        request_payload = {}
        def fake_open(request, timeout):
            request_payload.update(json.loads(request.data))
            self.assertEqual(request.get_header("Accept"), "text/event-stream")
            return Response(b'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}\n\n'
                            b'data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}\n\n'
                            b'data: [DONE]\n\n')
        with patch.object(providers.urllib.request, "urlopen", side_effect=fake_open):
            result = providers.chat(providers.LlmSettings(), "test-key", [], "hello", stage="思考")
        self.assertTrue(request_payload["stream"])
        self.assertEqual(result, "done")
