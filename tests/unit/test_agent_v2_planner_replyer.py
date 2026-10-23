"""A3/A4 Planner/Replyer tests: no credentials, network, or QQ account."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from zhuangfy.agent_v2.planner import (
    PlanDecision, _valid_plan_v2, choose_action, parse_plan_v2, plan_turn_v2,
)
from zhuangfy.agent_v2.replyer import build_replyer_guidance


class PlannerV2Tests(unittest.TestCase):
    def test_reply_with_social_intent(self):
        data = {
            "action": "reply", "confidence": 0.92, "social_intent": "通过表情包表达好感",
            "reply_act": "轻轻回应", "scene": "私聊分享图片", "tone": "温柔俏皮",
            "attention": "表达对象尚未完全明确", "reply_style": "short",
            "prefer": ["接纳好感", "轻松"], "avoid": ["罗列图片物件"],
            "delivery": "voice",
        }
        raw = json.dumps(data, ensure_ascii=False)
        self.assertTrue(_valid_plan_v2(raw))
        plan = parse_plan_v2(raw)
        self.assertEqual(plan.action, "reply")
        self.assertEqual(plan.delivery, "text")  # no voice without channel opt-in
        self.assertEqual(parse_plan_v2(raw, allow_voice=True).delivery, "voice")
        hint = build_replyer_guidance(plan)
        self.assertIn("通过表情包表达好感", hint)
        self.assertIn("罗列图片物件", hint)
        self.assertIn("简短", hint)
        self.assertNotIn("判断为真实赠花", hint)

    def test_wait_is_bounded_and_single_use(self):
        plan = parse_plan_v2('{"action":"wait","confidence":0.9,"wait_seconds":999}')
        self.assertEqual(plan.wait_seconds, 12)
        self.assertEqual(choose_action(plan), "wait")
        self.assertEqual(choose_action(plan, already_waited=True), "reply")
        self.assertEqual(parse_plan_v2('{"action":"wait","confidence":0.9,"wait_seconds":0}').wait_seconds, 1)

    def test_stop_is_opt_in(self):
        plan = parse_plan_v2('{"action":"stop","confidence":0.95}')
        self.assertEqual(choose_action(plan), "reply")
        self.assertEqual(choose_action(plan, allow_silence=True), "stop")

    def test_tool_never_runs_without_host_authorization(self):
        plan = parse_plan_v2('{"action":"tool","confidence":0.9,"tool_name":"mijia.turn_on","tool_arguments":{"device":"lamp"}}')
        self.assertEqual(plan.tool_name, "mijia.turn_on")
        self.assertEqual(choose_action(plan), "reply")
        self.assertEqual(choose_action(plan, tools_enabled=True), "tool")

    def test_invalid_or_low_confidence_falls_back(self):
        self.assertEqual(parse_plan_v2("not json"), PlanDecision())
        self.assertEqual(parse_plan_v2('{"action":"unknown","confidence":1}'), PlanDecision())
        self.assertEqual(parse_plan_v2('{"action":"reply","confidence":true}'), PlanDecision())
        self.assertEqual(parse_plan_v2('{"action":"wait","confidence":0.2}').action, "reply")
        self.assertFalse(_valid_plan_v2('{"action":"reply","confidence":99}'))

    def test_guidance_is_bounded_and_does_not_claim_tools_succeeded(self):
        long = "a" * 3000
        plan = parse_plan_v2(json.dumps({"action":"reply","confidence":0.9,"attention":long,
            "prefer":[long] * 20,"avoid":[long] * 20}))
        hint = plan.prompt()
        self.assertLessEqual(len(hint), 1400)
        self.assertIn("只有真正使用且成功完成的工具", hint)

    def test_existing_stage_router_is_reused(self):
        captured = {}
        def invoke(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return '{"action":"reply","confidence":0.88,"social_intent":"好感表达"}'
        with patch.dict(sys.modules, {"model_routes": SimpleNamespace(invoke_stage=invoke)}):
            plan = plan_turn_v2(
                SimpleNamespace(system_prompt="庄方宜的人格"), "fake", [],
                "[图片]", visual_context="一张表达好感的卡通图片", trace_id="turn1")
        self.assertEqual(plan.social_intent, "好感表达")
        self.assertEqual(captured["args"][0], "planner")
        self.assertIn("庄方宜的人格", captured["kwargs"]["system_prompt"])
        self.assertIn("卡通图片", captured["kwargs"]["visual_context"])
        self.assertTrue(captured["kwargs"]["response_validator"](
            '{"action":"reply","confidence":0.9}'))


if __name__ == "__main__":
    unittest.main()
