"""Offline regression checks for Maisaka-ported gate and Agent V2 tool loop."""
import asyncio
import unittest

from zhuangfy.agent_v2.maisaka_port.reply_likelihood import (
    ReplyLikelihoodInput, estimate_reply_probability,
)
from zhuangfy.agent_v2.maisaka_port.dynamic_gate import DynamicReplyGate
from zhuangfy.agent_v2.turn_trigger import EventTurnGate, TurnEvent
from zhuangfy.agent_v2.runtime import (
    AgentCycleEngine, AuthorizedToolRegistry, ToolBinding,
)
from zhuangfy.agent_v2.planner import PlanDecision


def event(event_id="1", **kwargs):
    return TurnEvent(session_id="qq:group:99", event_id=event_id, kind="message",
                     session_kind="group", **kwargs)


class PortedMaisakaTests(unittest.TestCase):
    def test_upstream_reply_estimator_is_bounded(self):
        low = ReplyLikelihoodInput(False, False, False, True, 0.0, 10, 3600, 1)
        high = ReplyLikelihoodInput(True, False, True, False, 0.8, 3, 20, 3)
        self.assertTrue(0 < estimate_reply_probability(low) < estimate_reply_probability(high) < 1)

    def test_upstream_dynamic_gate_frequency(self):
        gate = DynamicReplyGate()
        gate.record_proactive_demand(.7, 100.0)
        self.assertTrue(gate.evaluate(.7, 1.0, 100.0).should_trigger)
        self.assertFalse(gate.evaluate(.7, 0.0, 100.0).should_trigger)
        self.assertEqual(gate.take_uncounted_message_ids(["a", "a", "b"]), ["a", "b"])
        self.assertEqual(gate.take_uncounted_message_ids(["a"]), [])

    def test_direct_private_or_at_bypasses_group_gate(self):
        gate = EventTurnGate()
        private = TurnEvent("qq:private:7", "a", "message", "private", text="你好")
        self.assertEqual(gate.evaluate(private, now=20, frequency=0).decision, "trigger")
        self.assertEqual(gate.evaluate(private, now=20, frequency=1).decision, "ignore")
        self.assertEqual(gate.evaluate(event("at", directed=True), now=20, frequency=0).decision, "trigger")

    def test_group_frequency_and_independent_sessions(self):
        gate = EventTurnGate()
        self.assertEqual(gate.evaluate(event("a", text="今天天气？"), now=1, frequency=0).decision, "hold")
        self.assertEqual(gate.evaluate(event("a", text="今天天气？"), now=2, frequency=1).decision, "trigger")
        self.assertEqual(gate.evaluate(event("a", text="今天天气？"), now=3, frequency=1).decision, "ignore")
        self.assertEqual(gate.evaluate(event("b", text="嗯"), now=5, frequency=.0).decision, "hold")
        self.assertEqual(gate.evaluate(event("b", text="嗯"), now=6, frequency=1).decision, "trigger")
        self.assertEqual(gate.evaluate(event("c", text="在吗？"), now=7, frequency=1, debounce_remaining=2).decision, "delay")
        self.assertEqual(gate.evaluate(event("c", text="在吗？"), now=9, frequency=1).decision, "trigger")
        self.assertEqual(gate.evaluate(event("d", text="？"), now=10, frequency=1, backoff_remaining=2).decision, "delay")
        self.assertEqual(gate.evaluate(event("d", text="？"), now=12, frequency=1).decision, "trigger")

    def test_tool_and_plugin_events_require_active_parent(self):
        gate = EventTurnGate()
        base = TurnEvent("qq:group:99", "tool-1", "tool_result", "group")
        self.assertEqual(gate.evaluate(base, now=1).decision, "hold")
        active = TurnEvent("qq:group:99", "tool-2", "tool_result", "group",
                           parent_turn_active=True)
        self.assertEqual(gate.evaluate(active, now=1).decision, "trigger")
        unsolicited = TurnEvent("qq:group:99", "plugin-1", "plugin", "group")
        self.assertEqual(gate.evaluate(unsolicited, now=1).decision, "hold")
        unauthorized = TurnEvent("qq:group:99", "malicious", "message", "group", authorized=False)
        self.assertEqual(gate.evaluate(unauthorized, now=1).decision, "ignore")


class MultiRoundTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_tool_feeds_into_next_planner_round(self):
        registry = AuthorizedToolRegistry()
        calls = []
        async def lookup(args):
            calls.append(args["topic"])
            return "找到相关资料"
        registry.register(ToolBinding("knowledge.find", lookup, frozenset({"private", "group"})))
        feedback = []
        async def planner(receipts):
            feedback.append(receipts)
            if not receipts:
                return PlanDecision(action="tool", confidence=.92, tool_name="knowledge.find",
                                    tool_arguments={"topic": "osg"})
            return PlanDecision(action="reply", confidence=.92, reply_act="结合结果回答")
        result = await AgentCycleEngine().run(planner, scope="private", registry=registry,
                                              allow_tools=True)
        self.assertEqual(result.rounds, 2)
        self.assertEqual(result.action, "reply")
        self.assertEqual(calls, ["osg"])
        self.assertEqual(len(result.receipts), 1)
        self.assertIn("找到相关资料", result.tool_feedback())
        self.assertEqual(len(feedback[1]), 1)

    async def test_tool_permission_and_mutations_are_denied(self):
        registry = AuthorizedToolRegistry()
        called = []
        async def turn_on(args):
            called.append(args)
            return "ok"
        registry.register(ToolBinding("mijia.turn_on", turn_on,
                                      scopes=frozenset({"private"}), mutating=True))
        async def planner(receipts):
            return PlanDecision(action="tool", confidence=.9, tool_name="mijia.turn_on",
                                tool_arguments={"device": "lamp"})
        blocked = await AgentCycleEngine().run(planner, registry=registry,
                                               allow_tools=True, scope="group")
        self.assertEqual(blocked.reason, "tool_denied")
        allowed_scope_but_no_mutation = await AgentCycleEngine().run(planner,
                                               registry=registry,
                                               allow_tools=True, scope="private")
        self.assertEqual(allowed_scope_but_no_mutation.reason, "tool_denied")
        self.assertEqual(called, [])

    async def test_duplicate_tool_and_budget_prevent_infinite_loop(self):
        registry = AuthorizedToolRegistry()
        calls = []
        async def fake(args):
            calls.append(args)
            return "ok"
        registry.register(ToolBinding("test.read", fake))
        async def planner(receipts):
            return PlanDecision(action="tool", confidence=.9, tool_name="test.read",
                                tool_arguments={"i":1})
        result = await AgentCycleEngine(max_rounds=5, max_tools=3).run(
            planner, registry=registry, allow_tools=True)
        self.assertEqual(result.reason, "duplicate_tool")
        self.assertEqual(len(calls), 1)

    async def test_no_tools_and_cancellation(self):
        engine = AgentCycleEngine()
        async def no_tool(receipts):
            return PlanDecision(action="reply", confidence=1)
        self.assertEqual((await engine.run(no_tool)).action, "reply")
        async def cancelled(receipts):
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await engine.run(cancelled)


if __name__ == "__main__":
    unittest.main()
