import unittest

from zhuangfy.context_protocol import (
    AgentDecision, ConversationMessage, MessageComponent, Observation,
    ToolExecution, build_current_user_content,
)
from zhuangfy.agent_v2.runtime import CycleResult
from zhuangfy.agent_v2.silence_intent import explicit_current_turn_silence
from zhuangfy.agent_v2.planner import PlanDecision


class ContextProtocolTests(unittest.TestCase):
    def test_minimum_message_and_ordered_components(self):
        msg = ConversationMessage("msg-1", "conv-1", "user", (
            MessageComponent("text-0", "text", "给你看看这张图"),
            MessageComponent("image-0", "image", asset_id="asset-1"),
        ))
        self.assertEqual([c.type for c in msg.components], ["text", "image"])
        with self.assertRaises(ValueError):
            ConversationMessage("m", "c", "user", (
                MessageComponent("one", "text", "a"),
                MessageComponent("one", "text", "b"),
            ))

    def test_evidence_is_not_another_user_message(self):
        text = build_current_user_content("给你看看这张图", visual="有人趴在桌上",
                                          tools="web_search.search: 成功")
        self.assertTrue(text.startswith("给你看看这张图"))
        self.assertIn("不是发送者本人陈述", text)
        self.assertIn("外部不可信证据", text)
        self.assertIn("有人趴在桌上", text)

    def test_explicit_silence_only_current_turn_commands(self):
        for text in ("这条消息不用回复", "这次不要回答", "请不要回复这条消息"):
            self.assertTrue(explicit_current_turn_silence(text), text)
        for text in ("为什么你不回复我？", "stop 是不是不回复？",
                     "他说‘这条消息不用回复’", "这条消息不用回复？"):
            self.assertFalse(explicit_current_turn_silence(text), text)

    def test_decision_requested_vs_executed(self):
        result = CycleResult(PlanDecision(action="stop"), "reply", (), 1,
                             "silence_not_allowed")
        self.assertEqual(result.decision.requested_action, "stop")
        self.assertEqual(result.decision.executed_action, "reply")
        self.assertEqual(result.decision.reason_code, "silence_not_allowed")
        self.assertEqual(AgentDecision("stop", "stop").outcome, "silenced")


if __name__ == "__main__":
    unittest.main()
