"""Offline E1.2 report and synthetic fixture regression tests."""
from pathlib import Path
import importlib.util
import json
import unittest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/character_e12_private_trace.py"
CASES = ROOT / "resources/evaluation/character_e12_private_cases.json"

class E12PrivateTraceTests(unittest.TestCase):
    def test_all_cases_are_synthetic_and_private(self):
        data = json.loads(CASES.read_text(encoding="utf-8"))
        cases = data["cases"]
        self.assertEqual(data["schema"], 1)
        self.assertEqual(len(cases), 14)
        self.assertEqual(len({c["id"] for c in cases}), len(cases))
        self.assertTrue(all(c["channel"] == "qq_private" for c in cases))
        self.assertTrue(all(c["category"] == "private" for c in cases))
        self.assertTrue(all("lore" not in c["id"] for c in cases))

    def test_html_escapes_untrusted_model_text(self):
        spec = importlib.util.spec_from_file_location("e12_runner", RUNNER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        row = {
            "case_id": "mock",
            "message": "<img src=x onerror=alert(1)>",
            "background": [],
            "planner": {"action": "reply"},
            "action": "reply",
            "guidance": "不要输出动作",
            "raw": "<script>alert(1)</script>",
            "final": "（微笑）你好",
            "warnings": ["raw_parentheses_review"],
            "error": "",
        }
        html = module.render([row], "fake", "fake")
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("Planner 决策", html)
        self.assertIn("R1 最终回答", html)

if __name__ == "__main__":
    unittest.main()
