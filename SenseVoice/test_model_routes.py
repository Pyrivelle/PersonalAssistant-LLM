"""Fault-injection tests for stage-specific fallback and safe API settings."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import model_routes
from llm import LlmSettings, ModelRequestError
from model_routes import (
    ModelCandidate, ModelRoutes, ModelRoutesFailed, StagePolicy,
    invoke_stage, load_routes, save_routes,
)
from turn_planner import PlannerSettings, plan_turn


def candidate(route_id, model="model-1"):
    return ModelCandidate(route_id, "custom", "http://127.0.0.1:11434/v1", model)


class ModelRoutesTests(unittest.TestCase):
    def test_missing_config_falls_back_to_legacy_model(self):
        settings = LlmSettings(model="existing-model")
        routes = ModelRoutes()
        seen = []
        def fake_chat(s, key, _history, _text, **kwargs):
            seen.append((s.model, key, kwargs["stage"]))
            return "已经回复"
        with patch.object(model_routes, "chat", side_effect=fake_chat):
            output = invoke_stage("reply", settings, "old-key", [], "你好", routes=routes)
        self.assertEqual(output, "已经回复")
        self.assertEqual(seen, [("existing-model", "old-key", "回复")])

    def test_vision_requires_explicit_model_and_passes_image_to_it(self):
        settings = LlmSettings()
        with patch.object(model_routes, "load_routes", return_value=ModelRoutes()):
            self.assertFalse(model_routes.can_attempt_stage("vision", settings, "old-key"))
        with self.assertRaises(ModelRoutesFailed):
            invoke_stage("vision", settings, "old-key", [], "看图", routes=ModelRoutes(),
                         image_data_url="data:image/jpeg;base64,YWJj")
        routes = ModelRoutes(vision=StagePolicy((candidate("vision_1", "vision-model"),), retries=0))
        with patch.object(model_routes, "chat", return_value="图片里有一只猫") as request, \
             patch.object(model_routes, "load_route_key", return_value="vision-key"):
            result = invoke_stage("vision", settings, "old-key", [], "看图", routes=routes,
                                  image_data_url="data:image/jpeg;base64,YWJj")
        self.assertEqual(result, "图片里有一只猫")
        self.assertEqual(request.call_args.kwargs["image_data_url"], "data:image/jpeg;base64,YWJj")

    def test_transient_failure_retries_then_switches_api(self):
        routes = ModelRoutes(reply=StagePolicy(
            (candidate("reply_1", "server-a"), candidate("reply_2", "server-b")),
            retries=1, retry_delay=0, timeout=6, total_timeout=30))
        attempts = []
        def fake_chat(s, key, _history, _text, **kwargs):
            attempts.append((s.model, key, kwargs["timeout"]))
            if s.model == "server-a":
                raise ModelRequestError("temporary", kind="http", status=503, retryable=True)
            return "备用服务回复成功"
        with patch.object(model_routes, "chat", side_effect=fake_chat), \
             patch.object(model_routes, "load_route_key", side_effect=lambda id, *_args: "key-" + id):
            result = invoke_stage("reply", LlmSettings(), "old-key", [], "你好", routes=routes)
        self.assertEqual(result, "备用服务回复成功")
        self.assertEqual([item[0] for item in attempts], ["server-a", "server-a", "server-b"])
        self.assertEqual(attempts[-1][1], "key-reply_2")

    def test_auth_errors_skip_retries_but_allow_another_provider(self):
        routes = ModelRoutes(reply=StagePolicy(
            (candidate("reply_1"), candidate("reply_2")),
            retries=3, retry_delay=0))
        attempts = []
        def fake_chat(s, *_args, **_kwargs):
            attempts.append(s.model)
            if len(attempts) == 1:
                raise ModelRequestError("invalid key", kind="http", status=401, retryable=False)
            return "ok"
        with patch.object(model_routes, "chat", side_effect=fake_chat), \
             patch.object(model_routes, "load_route_key", return_value="token"):
            self.assertEqual(invoke_stage("reply", LlmSettings(), "", [], "hi", routes=routes), "ok")
        self.assertEqual(len(attempts), 2)

    def test_empty_and_malformed_model_answers_trigger_next_route(self):
        routes = ModelRoutes(reply=StagePolicy(
            (candidate("reply_1"), candidate("reply_2")),
            retries=0))
        with patch.object(model_routes, "chat", side_effect=[
            ModelRequestError("empty", kind="empty", retryable=True), "成功"
        ]), patch.object(model_routes, "load_route_key", return_value="key"):
            self.assertEqual(invoke_stage("reply", LlmSettings(), "x", [], "hi", routes=routes), "成功")

    def test_all_routes_failed_is_explicit_and_bounded(self):
        routes = ModelRoutes(reply=StagePolicy(
            (candidate("reply_1"), candidate("reply_2")),
            retries=0))
        error = ModelRequestError("server failed", kind="network", retryable=True)
        with patch.object(model_routes, "chat", side_effect=error), \
             patch.object(model_routes, "load_route_key", return_value="SECRET_DO_NOT_PRINT"):
            with self.assertRaises(ModelRoutesFailed) as thrown:
                invoke_stage("reply", LlmSettings(), "", [], "hi", routes=routes)
        self.assertEqual(thrown.exception.stage, "reply")
        self.assertEqual(len(thrown.exception.attempts), 2)
        self.assertNotIn("SECRET_DO_NOT_PRINT", str(thrown.exception))
        self.assertIn("网络错误", str(thrown.exception))

    def test_planner_malformed_json_fails_over_before_reply_phase(self):
        from turn_planner import parse_plan
        valid = '{"should_reply":true,"tone":"关切","attention":"用户疲惫","confidence":0.9,"delivery":"text"}'
        config = ModelRoutes(planner=StagePolicy(
            (candidate("planner_1"), candidate("planner_2")), retries=0, timeout=5, total_timeout=12))
        with patch.object(model_routes, "load_routes", return_value=config), \
             patch.object(model_routes, "load_route_key", return_value="key"), \
             patch.object(model_routes, "chat", side_effect=["not-json", valid]) as fn:
            result = plan_turn(LlmSettings(), "old-key", [], "我好累",
                               PlannerSettings(enabled=True))
        self.assertEqual(result.tone, "关切")
        self.assertEqual(fn.call_count, 2)

    def test_planner_no_route_success_raises_for_caller_to_downgrade(self):
        config = ModelRoutes(planner=StagePolicy((candidate("planner_1"),), retries=0))
        with patch.object(model_routes, "load_routes", return_value=config), \
             patch.object(model_routes, "load_route_key", return_value="key"), \
             patch.object(model_routes, "chat", return_value="invalid JSON"):
            with self.assertRaises(ModelRoutesFailed):
                plan_turn(LlmSettings(), "key", [], "你好", PlannerSettings(enabled=True))

    def test_round_trip_does_not_write_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            routes = ModelRoutes(
                planner=StagePolicy((candidate("planner_1"),), retries=2),
                reply=StagePolicy((candidate("reply_1"), candidate("reply_2"))))
            save_routes(routes, path)
            self.assertEqual(load_routes(path), routes)
            self.assertNotIn("API_KEY", path.read_text(encoding="utf-8"))
            self.assertNotIn("token", path.read_text(encoding="utf-8"))

    def test_malformed_and_remote_http_configuration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            candidate("reply_1").__class__(
                "reply_1", "custom", "http://notlocal.example/v1", "my-model").validated()
        with self.assertRaises(ValueError):
            ModelRoutes(reply=StagePolicy((candidate("reply_1"), candidate("reply_1")))).validated()
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "routes.json"
            file.write_text('{"version":2}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "版本"):
                load_routes(file)

    def test_per_candidate_api_key_is_not_saved_in_json(self):
        with tempfile.TemporaryDirectory() as directory:
            keypath = Path(directory) / ".env.local"
            with patch.object(model_routes, "ENV_FILE", keypath), \
                 patch.object(model_routes, "load_key", return_value=""):
                model_routes.save_route_key("reply_1", "private-key-123", "custom", "http://127.0.0.1:11434/v1")
                model_routes.save_route_key("planner_1", "different-key-234", "custom", "http://127.0.0.1:11434/v1")
                self.assertIn("private-key-123", keypath.read_text(encoding="utf-8"))
                self.assertEqual(model_routes.load_route_key("reply_1", "custom", "http://127.0.0.1:11434/v1"), "private-key-123")
                self.assertEqual(model_routes.load_route_key("planner_1", "custom", "http://127.0.0.1:11434/v1"), "different-key-234")
                self.assertEqual(model_routes.load_route_key(
                    "reply_1", "custom", "https://different-provider.example/v1"), "")
                self.assertEqual(model_routes.load_route_key(
                    "reply_1", "siliconflow", "https://api.siliconflow.cn/v1"), "")


if __name__ == "__main__":
    unittest.main()
