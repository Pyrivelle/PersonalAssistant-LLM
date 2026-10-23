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
        self.assertEqual(parse_plan('{"tone":"自然","confidence":0.9,"delivery":"voice"}').delivery, "voice")
        self.assertEqual(parse_plan('{"tone":"自然","confidence":0.9,"delivery":"video"}').delivery, "text")

    def test_agent_v2_explicit_flags_and_wait_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            values = {
                "agent_v2_enabled": True,
                "agent_v2_allow_silence_group": True,
                "agent_v2_wait_max_seconds": 7,
            }
            path.write_text(json.dumps(values), encoding="utf-8")
            settings = PlannerSettings.load(path)
            self.assertTrue(settings.agent_v2_enabled)
            self.assertTrue(settings.agent_v2_allow_silence_group)
            self.assertFalse(settings.agent_v2_allow_silence_private)
            self.assertEqual(settings.agent_v2_wait_max_seconds, 7)
            for bad in ({"agent_v2_enabled": "yes"},
                        {"agent_v2_allow_silence_private": 1},
                        {"agent_v2_wait_max_seconds": 0},
                        {"agent_v2_wait_max_seconds": 50}):
                path.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(ValueError):
                    PlannerSettings.load(path)

    def test_character_core_v2_is_explicit_opt_in_and_type_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cognition.json"
            self.assertFalse(PlannerSettings.load(path).character_core_v2_enabled)
            path.write_text(json.dumps({"character_core_v2_enabled": True}), encoding="utf-8")
            self.assertTrue(PlannerSettings.load(path).character_core_v2_enabled)
            for invalid in ("true", 1, None, []):
                path.write_text(json.dumps(
                    {"character_core_v2_enabled": invalid}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    PlannerSettings.load(path)

    def test_character_knowledge_v1_is_opt_in_and_validated(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cognition.json"
            self.assertFalse(PlannerSettings.load(path).character_knowledge_v1_enabled)
            path.write_text(json.dumps({"character_knowledge_v1_enabled": True}), encoding="utf-8")
            self.assertTrue(PlannerSettings.load(path).character_knowledge_v1_enabled)
            path.write_text('{"character_knowledge_v1_enabled": "yes"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                PlannerSettings.load(path)

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
