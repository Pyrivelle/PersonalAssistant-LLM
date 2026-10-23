import json
from pathlib import Path
import tempfile
import unittest

from zhuangfy.agent_v2.proactive_delivery import ProactiveConfig, ProactiveStore
from zhuangfy.agent_v2.initiative import InitiativeGrant, InitiativeInbox


class ProactiveDeliveryTests(unittest.TestCase):
    def test_opt_in_config_and_channel_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.assertFalse(ProactiveConfig.load(path).enabled)
            path.write_text(json.dumps({"enabled": True, "channel": "qq",
                "recipient_id": 42, "daily_checkin": True}), encoding="utf-8")
            self.assertEqual(ProactiveConfig.load(path).policy().owner_scope, "qq:private:42")
            path.write_text(json.dumps({"enabled": True, "channel": "wechat",
                "recipient_id": 42}), encoding="utf-8")
            with self.assertRaises(ValueError):
                ProactiveConfig.load(path)

    def test_gate_reservation_and_no_duplicate_after_uncertain_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProactiveStore(Path(directory) / "proactive.db")
            policy = ProactiveConfig(enabled=True, recipient_id=42).policy()
            start = 1_785_000_000.0
            store.initialize(42, start)
            # Pick a local daytime instant without making tests timezone-dependent.
            from datetime import datetime
            daytime = next(start+i*3600 for i in range(24)
                           if datetime.fromtimestamp(start+i*3600).hour == 14)
            ready = daytime + 9*3600
            ready = next(ready+i*3600 for i in range(24)
                         if datetime.fromtimestamp(ready+i*3600).hour == 14)
            self.assertFalse(store.reserve("checkin:one", 42, "你好", start, policy))
            self.assertTrue(store.reserve("checkin:one", 42, "你好", ready, policy))
            self.assertTrue(store.already_reserved("checkin:one"))
            self.assertFalse(store.reserve("checkin:one", 42, "你好", ready+1, policy))
            self.assertFalse(store.reserve("checkin:two", 42, "又来问候", ready+30, policy))
            store.mark_sent("checkin:one")
            store.human_message(42, ready+86401)
            self.assertFalse(store.reserve("checkin:two", 42, "你好", ready+86402, policy))

    def test_only_current_nondismissed_reviews_are_deliverable(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = InitiativeInbox(Path(directory) / "background.db")
            grant = InitiativeGrant("qq:private:42", True)
            self.assertTrue(inbox.propose_from_goal(goal_id="g1", source_event_id="e1",
                owner_scope="qq:private:42", evidence="已完成", topic="继续聊", now=100,
                grant=grant))
            self.assertTrue(inbox.record_review(goal_id="g1", owner_scope="qq:private:42",
                action="share", confidence=.9, proposed_text="任务完成了。", reviewed_at=110,
                reason="可告知"))
            self.assertEqual(len(inbox.actionable_reviews("qq:private:42", 90)), 1)
            self.assertEqual(inbox.actionable_reviews("qq:private:42", 101), ())
            inbox.dismiss("g1", "qq:private:42")
            self.assertEqual(inbox.actionable_reviews("qq:private:42", 90), ())


if __name__ == "__main__":
    unittest.main()
