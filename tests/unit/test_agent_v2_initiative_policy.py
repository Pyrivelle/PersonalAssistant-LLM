"""B2 social turn policy and B1 initiative review integration, offline only."""
import tempfile
from pathlib import Path
import unittest

from zhuangfy.agent_v2.initiative import InitiativeInbox, InitiativeGrant
from zhuangfy.agent_v2.initiative_policy import (
    PresencePolicy, PresenceSnapshot, InitiativeReviewer,
    InitiativeDecision, timing_gate, parse_initiative_decision
)


def snapshot(**updates):
    data = dict(local_hour=14, seconds_since_human_message=4000,
                seconds_since_last_outbound=40000, sent_last_24h=0)
    data.update(updates)
    return PresenceSnapshot(**data)


class SocialTimingTests(unittest.TestCase):
    def setUp(self):
        self.policy = PresencePolicy("qq:private:42", enabled=True)

    def test_quiet_hours_cooldown_user_busy_and_voice(self):
        for inputs, reason in [
            ({"local_hour":23}, "quiet_hours"),
            ({"local_hour":6}, "quiet_hours"),
            ({"voice_active":True}, "user_busy"),
            ({"user_typing":True}, "user_busy"),
            ({"active_chat_turn":True}, "user_busy"),
            ({"seconds_since_human_message":30}, "wait_for_human"),
            ({"seconds_since_last_outbound":500}, "outbound_cooldown"),
            ({"sent_last_24h":1}, "daily_budget"),
            ({"local_hour":99}, "unknown_local_time"),
            ({"seconds_since_human_message":float("nan")}, "incomplete_presence"),
        ]:
            with self.subTest(reason=reason):
                self.assertEqual(timing_gate(self.policy, snapshot(**inputs)).reason, reason)
        self.assertTrue(timing_gate(self.policy, snapshot()).eligible)
        self.assertFalse(timing_gate(PresencePolicy("qq:private:42"), snapshot()).eligible)

    def test_no_group_or_bad_policy(self):
        with self.assertRaises(ValueError):
            PresencePolicy("qq:group:9", True)
        with self.assertRaises(ValueError):
            PresencePolicy("qq:private:42", True, daily_limit=99)

    def test_model_output_is_bounded_and_low_confidence_defers(self):
        self.assertEqual(parse_initiative_decision('{"action":"ask","confidence":0.9,"draft":"之前聊的事，有进展吗？"}').action, "ask")
        self.assertEqual(parse_initiative_decision('{"action":"ask","confidence":0.45,"draft":"打扰吗"}').action, "defer")
        self.assertEqual(parse_initiative_decision('{"action":"ask","confidence":0.95}').action, "silence")
        self.assertEqual(parse_initiative_decision('not json').action, "silence")
        self.assertEqual(parse_initiative_decision('{"action":"ask","confidence":true}').action, "silence")


class InitiativeReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_gates_before_model_and_reviews_once(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = InitiativeInbox(Path(directory)/"goals.db")
            grant = InitiativeGrant("qq:private:42", True)
            self.assertTrue(inbox.propose_from_goal(
                goal_id="goal1", source_event_id="user-consent",
                owner_scope="qq:private:42", evidence="计算结果确认成功",
                topic="继续讨论共同项目", now=0, grant=grant))
            seen = []
            async def llm(topic, evidence):
                seen.append((topic,evidence))
                return InitiativeDecision("ask", .92, "那个项目后来进展如何？", "已约定跟进")
            reviewer = InitiativeReviewer(llm)
            policy = PresencePolicy("qq:private:42", enabled=True)
            hold = await reviewer.review_next(
                inbox=inbox, policy=policy, snapshot=snapshot(voice_active=True), now=100)
            self.assertEqual(hold["gate"], "user_busy")
            self.assertFalse(hold["reviewed"])
            self.assertEqual(seen, [])
            result = await reviewer.review_next(inbox=inbox, policy=policy,
                                                 snapshot=snapshot(), now=200)
            self.assertTrue(result["reviewed"])
            self.assertEqual(result["decision"], "ask")
            self.assertEqual(len(seen), 1)
            saved = InitiativeInbox(Path(directory)/"goals.db").get_review("goal1", "qq:private:42")
            self.assertEqual(saved["action"], "ask")
            self.assertIn("进展", saved["proposed_text"])
            self.assertIsNone(await reviewer.review_next(inbox=inbox, policy=policy,
                                                        snapshot=snapshot(), now=300))
            self.assertEqual(len(seen), 1)
            self.assertIsNone(inbox.get_review("goal1", "qq:private:777"))

    async def test_spoofed_draft_cannot_trigger_planner(self):
        called = []
        async def llm(topic, evidence):
            called.append(1)
            return InitiativeDecision("ask", 1, "你好")
        reviewer=InitiativeReviewer(llm)
        invalid={"goal_id":"bad","owner_scope":"qq:group:9","status":"draft",
                 "reason":"completed_authorized_goal","source_event_id":"x",
                 "topic":"X","evidence_excerpt":"Y"}
        status, decision = await reviewer.consider(
            draft=invalid, policy=PresencePolicy("qq:private:42", True), snapshot=snapshot())
        self.assertEqual(status.reason,"untrusted_or_wrong_scope")
        self.assertIsNone(decision)
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
