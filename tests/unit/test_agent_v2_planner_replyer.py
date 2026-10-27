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
            "delivery": "voice", "reply_parts": 2,
            "emotion": "欣喜", "emotion_intensity": 0.4,
        }
        raw = json.dumps(data, ensure_ascii=False)
        self.assertTrue(_valid_plan_v2(raw))
        plan = parse_plan_v2(raw)
        self.assertEqual(plan.action, "reply")
        self.assertEqual(plan.reply_parts, 2)
        self.assertEqual(plan.emotion, "欣喜")
        self.assertAlmostEqual(plan.emotion_intensity, 0.4)
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



class PlannerV3Tests(unittest.TestCase):
    @staticmethod
    def decision(**changes):
        value = {
            "schema_version": 3, "action": "reply", "confidence": 0.94,
            "intent": "朋友分享近况", "tone": "自然", "attention": "",
            "reply_style": "short", "delivery": "text", "wait_seconds": 0,
            "tool_name": "", "tool_arguments_json": "{}",
            "selection_reason": "普通交流，无需使用工具",
        }
        value.update(changes)
        return json.dumps(value, ensure_ascii=False)

    def test_v3_strict_envelope_and_v2_replyer_compatibility(self):
        from zhuangfy.agent_v2.planner_v3 import valid_plan_v3, parse_plan_v3
        raw = self.decision()
        self.assertTrue(valid_plan_v3(raw))
        plan = parse_plan_v3(raw)
        self.assertEqual(plan.action, "reply")
        self.assertEqual(plan.social_intent, "朋友分享近况")
        self.assertIn("朋友分享近况", plan.prompt())
        self.assertEqual(plan.selection_reason, "普通交流，无需使用工具")
        self.assertFalse(valid_plan_v3(self.decision(schema_version=2)))
        self.assertFalse(valid_plan_v3(self.decision(confidence=True)))
        self.assertFalse(valid_plan_v3(self.decision(confidence=float("nan"))))
        self.assertFalse(valid_plan_v3(self.decision(tool_name="secret.command")))
        self.assertFalse(valid_plan_v3(self.decision(tool_arguments_json="not json")))
        self.assertFalse(valid_plan_v3(self.decision(tool_arguments_json="[]")))
        self.assertFalse(valid_plan_v3(self.decision(wait_seconds=5)))
        self.assertFalse(valid_plan_v3(self.decision(action="wait", wait_seconds=60)))
        self.assertTrue(valid_plan_v3(self.decision(action="wait", wait_seconds=30)))
        self.assertEqual(parse_plan_v3(self.decision(action="wait", wait_seconds=30)).wait_seconds, 30)
        self.assertFalse(valid_plan_v3(self.decision(action="wait", wait_seconds=601)))
        self.assertFalse(valid_plan_v3(self.decision(unexpected="injection")))
        self.assertEqual(parse_plan_v3("garbage"), PlanDecision())

    def test_v3_tool_is_explicitly_shown_and_still_requires_executor(self):
        from zhuangfy.agent_v2.planner_v3 import valid_plan_v3, parse_plan_v3
        raw = self.decision(
            action="tool", tool_name="text_stats.length",
            tool_arguments_json='{"text":"你好"}',
            selection_reason="用户要求统计字符")
        self.assertFalse(valid_plan_v3(raw))
        self.assertTrue(valid_plan_v3(raw, allowed_tools=("text_stats.length",)))
        plan = parse_plan_v3(raw, allowed_tools=("text_stats.length",))
        self.assertEqual(plan.tool_arguments, {"text": "你好"})
        self.assertEqual(choose_action(plan), "reply")
        self.assertEqual(choose_action(plan, tools_enabled=True), "tool")

    def test_v3_router_receives_first_turn_schema(self):
        from zhuangfy.agent_v2.planner import plan_turn_v3
        from zhuangfy.agent_v2.planner_v3 import DECISION_SCHEMA
        from zhuangfy.persona.packs import ZHUANG_FANGYI_V1
        captured = {}
        def invoke(*args, **kwargs):
            captured.update(kwargs)
            return self.decision()
        with patch.dict(sys.modules, {"model_routes": SimpleNamespace(invoke_stage=invoke)}):
            plan = plan_turn_v3(SimpleNamespace(system_prompt=ZHUANG_FANGYI_V1.prompt()), "key",
                                [], "今天怎么样？")
        self.assertEqual(plan.action, "reply")
        self.assertEqual(captured["planner_schema"], DECISION_SCHEMA)
        self.assertIn(ZHUANG_FANGYI_V1.prompt(), captured["system_prompt"])
        self.assertTrue(captured["response_validator"](self.decision()))
        self.assertFalse(captured["response_validator"]('{"action":"reply"}'))


if __name__ == "__main__":
    unittest.main()
