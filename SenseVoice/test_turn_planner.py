"""Planner output is only a hint and cannot silence directed replies."""
import json
import tempfile
import unittest
from pathlib import Path

from turn_planner import PlannerSettings, TurnPlan, parse_plan, plan_turn


class PlannerTests(unittest.TestCase):
    def test_direct_message_cannot_be_silenced(self):
        reply = '{"should_reply": false, "tone": "关切", "attention": "关注工作疲惫", "confidence": 0.9}'
        self.assertTrue(parse_plan(reply).should_reply)
        self.assertFalse(parse_plan(reply, direct_message=False).should_reply)

    def test_invalid_or_unconfident_plan_is_harmless(self):
        self.assertEqual(parse_plan("not json"), TurnPlan())
        self.assertEqual(parse_plan('{"tone":"讨好","confidence":1}'), TurnPlan())
        self.assertEqual(parse_plan('{"tone":"自然","confidence":0.3}').prompt(), "")

    def test_optional_planner_is_off_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(PlannerSettings.load(Path(directory) / "missing.json").enabled)
        self.assertEqual(plan_turn(None, "", [], "你好", PlannerSettings()), TurnPlan())

    def test_settings_validate_explicit_private_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"enabled": True, "persist_private_ids": [42]}), encoding="utf-8")
            self.assertEqual(PlannerSettings.load(path).persist_private_ids, frozenset({42}))
            path.write_text('{"persist_private_ids":["42"]}', encoding="utf-8")
            with self.assertRaises(ValueError):
                PlannerSettings.load(path)


if __name__ == "__main__":
    unittest.main()
