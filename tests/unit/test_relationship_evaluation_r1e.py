"""R1-E offline smoke test for the production E1.2/1.3 reporting path."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]


class RelationshipEvaluationTests(unittest.TestCase):
    def test_pinned_private_pipeline_includes_planner_reply_and_event_evidence(self):
        spec = importlib.util.spec_from_file_location(
            "e13_report", ROOT / "scripts/character_e12_private_trace.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)

        calls = []
        class FakeSettings:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
            def validated(self):
                return self

        def fake_stage(stage, settings, key, history, message, **kwargs):
            calls.append((stage, kwargs.get("system_prompt", "")))
            if stage == "reply":
                return "你今天说的这件事，我听到了。"
            if "关系事件候选分类器" in kwargs.get("system_prompt", ""):
                return json.dumps({"event_kind": "none", "confidence": .99,
                                   "evidence_quote": ""}, ensure_ascii=False)
            return json.dumps({"action":"reply", "confidence":.98,
                               "reply_style":"short", "social_intent":"分享"})
        fake_llm = SimpleNamespace(LlmSettings=FakeSettings,
                                   PROVIDER_BASE_URLS={"siliconflow":"https://mock.invalid"})
        fake_routes = SimpleNamespace(invoke_stage=fake_stage)
        with tempfile.TemporaryDirectory() as d:
            opts = SimpleNamespace(provider="siliconflow", model="mock-model", max_cases=3,
                                  relationship_tier="L2", evaluate_events=True,
                                  out=Path(d), allow_network=True)
            with patch.dict(sys.modules, {"llm": fake_llm, "model_routes": fake_routes}), \
                 patch.dict(os.environ, {"ZHUANGFY_E1_API_KEY":"test-never-sent"}):
                code = script.run(opts)
            self.assertEqual(code, 0)
            payload = json.loads((Path(d) / "trace.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["synthetic_relationship_tier"], "L2")
            self.assertEqual(len(payload["rows"]), 3)
            self.assertTrue(all(r["relation_verdict"] == "none" for r in payload["rows"]))
            self.assertTrue(all("L2" in r["relation_hint"] for r in payload["rows"]))
            self.assertTrue(all(r["final"] for r in payload["rows"]))
            self.assertEqual(sum(s == "reply" for s, _ in calls), 3)
            self.assertEqual(sum(s == "planner" for s, _ in calls), 6)
            self.assertIn("关系事件候选", (Path(d) / "report.html").read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
