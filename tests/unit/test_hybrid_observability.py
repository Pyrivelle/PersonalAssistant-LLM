import sqlite3
import tempfile
from pathlib import Path
import unittest

from zhuangfy.engine_fusion.ipc_contract import Envelope, ProtocolError
from zhuangfy.engine_fusion.native_monitor import planner_details, summary
from zhuangfy.observability.external_events import ExternalEvents


class HybridObservabilityTests(unittest.TestCase):
    def test_planner_details_show_selected_history_and_reasoning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "planner" / "session" / "one.json"
            target.parent.mkdir(parents=True)
            target.write_text(__import__("json").dumps({
                "request_items": [
                    {"item_type": "SystemMessageItem", "parts": [{"text": "SECRET SYSTEM"}]},
                    {"item_type": "UserMessageItem", "parts": [{"text": "actual history"}]},
                ],
                "output_items": [{"item_type": "ReasoningItem",
                                  "text_parts": ["reasoning text"]}],
            }), encoding="utf-8")
            payload = {"cycle_id": 2, "planner": {
                "prompt_html_uri": "/api/webui/config/maisaka-prompt-preview?path=planner%2Fsession%2Fone.json",
                "content": "planner decision"}}
            details = planner_details(payload, root)
            self.assertEqual([phase for phase, _ in details],
                             ["输入历史", "模型推理", "规划正文"])
            self.assertIn("actual history", details[0][1])
            self.assertIn("reasoning text", details[1][1])
            self.assertNotIn("SECRET SYSTEM", str(details))
            payload["planner"]["prompt_html_uri"] = (
                "/api/webui/config/maisaka-prompt-preview?path=planner%2F..%2Fsecret.json")
            self.assertEqual([phase for phase, _ in planner_details(payload, root)],
                             ["模型推理", "规划正文"])

    def test_planner_details_disclose_stop_even_when_body_looks_like_reply(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "planner" / "session" / "two.json"
            target.parent.mkdir(parents=True)
            target.write_text(__import__("json").dumps({
                "request_items": [],
                "output_items": [{"item_type": "AssistantMessageItem",
                                  "parts": [{"text": "中午好。"}]}],
            }), encoding="utf-8")
            payload = {"cycle_id": 1, "planner": {
                "prompt_html_uri": "/preview?path=planner%2Fsession%2Ftwo.json",
                "content": "中午好。"},
                "tools": [{"tool_name": "stop", "tool_args": {}, "success": True,
                           "summary": "已结束本轮思考，等待新消息。"}],
                "final_state": {"agent_state": "stop", "end_detail": "Planner 调用 stop。"}}
            details = planner_details(payload, root)
            self.assertEqual([phase for phase, _ in details],
                             ["模型推理", "规划正文", "工具执行", "行为结论"])
            self.assertIn("未返回独立的推理文本", details[0][1])
            self.assertIn("调用 stop()", details[2][1])
            self.assertIn("状态：stop", details[3][1])
            self.assertIn("正文未作为回复发送", details[3][1])

    def test_planner_summary_reports_counts_and_action_without_prompt(self):
        payload = {
            "cycle_id": 2,
            "request": {"selected_history_count": 11,
                        "context_sections": [{"key": "messages", "chars": 2083},
                                             {"key": "secret", "chars": 500}]},
            "planner": {"model_name": "deepseek-v4-flash", "total_tokens": 456,
                        "content": "PRIVATE THOUGHT", "tool_calls": [{"name": "wait",
                        "arguments": {"seconds": 180, "secret": "PRIVATE ARG"}}]},
            "final_state": {"agent_state": "wait"},
        }
        phase, text = summary("planner.finalized", payload)
        self.assertEqual(phase, "规划")
        self.assertIn("第2轮", text)
        self.assertIn("历史 11 条", text)
        self.assertIn("决策：wait", text)
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn("secret", text)

    def test_telemetry_envelope_accepts_only_bounded_summary(self):
        payload = {"phase": "规划", "summary": "第1轮 · 决策：reply",
                   "cause_event_id": "turn-1"}
        event = Envelope("telemetry", "event-1", "qq:456:private:123", payload)
        self.assertEqual(Envelope.from_line(event.to_line()).payload, payload)
        with self.assertRaises(ProtocolError):
            Envelope("telemetry", "event-1", "qq:456:private:123",
                     {**payload, "prompt": "raw"})

    def test_external_relay_reads_incrementally(self):
        with tempfile.TemporaryDirectory() as temp:
            relay = ExternalEvents(Path(temp) / "events.db")
            relay.append({"source": "QQ", "phase": "消息", "detail": "你好"})
            first = relay.since(0)
            self.assertEqual(len(first), 1)
            relay.append({"source": "QQ", "phase": "规划", "detail": "reply"})
            second = relay.since(first[0][0])
            self.assertEqual([event["phase"] for _, event in second], ["规划"])
