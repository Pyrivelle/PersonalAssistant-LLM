"""R1-B: deterministic identity, evidence, controls, no model/network calls."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zhuangfy.memory.database import MemoryDatabase
from zhuangfy.memory.identity import ChannelIdentity, IdentityProof, IdentityRepository
from zhuangfy.memory.timeline import TimelineRepository
from zhuangfy.relationship import RelationshipCore

AGENT = "zhuangfy-main"
PROOF = IdentityProof("trusted_admin", "confirmed-in-test")


class RelationshipCoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = MemoryDatabase(Path(tmp.name) / "memory.sqlite3")
        self.identities = IdentityRepository(db)
        self.timeline = TimelineRepository(db, self.identities)
        self.core = RelationshipCore(db, self.identities)
        self.alice = self.identities.create_person("同名")
        self.bob = self.identities.create_person("同名")
        self.a = ChannelIdentity("qq", "bot-1", "u1")
        self.b = ChannelIdentity("qq", "bot-1", "u2")
        self.identities.link_verified(self.alice.person_id, self.a, PROOF)
        self.identities.link_verified(self.bob.person_id, self.b, PROOF)
        self.identities.set_consent(self.alice.person_id, "timeline", True)
        self.chat = self.timeline.open_private(
            agent_id=AGENT, identity=self.a, external_conversation_id="qq-private-u1")

    def _received(self, mid: str = "msg-1"):
        return self.timeline.receive(
            conversation_id=self.chat.conversation_id, identity=self.a,
            source_message_id=mid, content="我们继续聊聊之前的插件项目")

    def test_opt_in_initial_seed_and_l3_requires_permission(self):
        self.assertIsNone(self.core.snapshot(agent_id=AGENT, identity=self.a))
        with self.assertRaises(ValueError):
            self.core.enroll(agent_id=AGENT, identity=self.a, initial_tier="L3")
        with self.assertRaises(PermissionError):
            self.core.enroll(agent_id=AGENT, identity=ChannelIdentity("qq", "bot-1", "unknown"))
        p = self.core.enroll(agent_id=AGENT, identity=self.a, initial_tier="L2")
        self.assertEqual((p.tier, p.version), ("L2", 1))
        self.assertFalse(p.allow_affection)
        self.assertIn("不预设暧昧", p.prompt_hint())
        with self.assertRaises(ValueError):
            self.core.enroll(agent_id=AGENT, identity=self.a)
        self.assertEqual(self.core.enroll(agent_id="another-agent", identity=self.a,
                                           initial_tier="L3", allow_affection=True).tier, "L3")

    def test_manual_correction_freeze_ceiling_and_version_lock(self):
        p = self.core.enroll(agent_id=AGENT, identity=self.a, initial_tier="L1")
        p = self.core.correct(agent_id=AGENT, identity=self.a, expected_version=p.version,
                              closeness=99, familiarity=90, trust=90,
                              tier_ceiling="L1", allow_affection=True,
                              tone="direct", avoid_nicknames=True)
        self.assertEqual(p.tier, "L1")
        self.assertEqual((p.closeness, p.tone, p.version), (99, "direct", 2))
        with self.assertRaises(ValueError):
            self.core.correct(agent_id=AGENT, identity=self.a, expected_version=1,
                              tier_ceiling="L3")
        with self.assertRaises(ValueError):
            self.core.correct(agent_id=AGENT, identity=self.a, expected_version=2,
                              familiarity=101)
        p = self.core.correct(agent_id=AGENT, identity=self.a, expected_version=2,
                              tier_ceiling="L3", frozen=True)
        self.assertEqual(p.tier, "L3")
        self.assertTrue(p.frozen)
        events = self.core.events(agent_id=AGENT, identity=self.a)
        self.assertEqual(sum(e.kind == "manual_correction" for e in events), 2)
        self.assertTrue(any('"before"' in e.detail and '"after"' in e.detail
                            for e in events if e.kind == "manual_correction"))

    def test_only_real_timeline_evidence_can_drive_autonomous_growth(self):
        p = self.core.enroll(agent_id=AGENT, identity=self.a)
        with self.assertRaises(ValueError):
            self.core.observe(agent_id=AGENT, identity=self.a, event_kind="shared_topic",
                              timeline_entry_id="imaginary", expected_version=p.version)
        with self.assertRaises(ValueError):
            self.core.observe(agent_id=AGENT, identity=self.a, event_kind="tool_success",
                              timeline_entry_id="imaginary", expected_version=p.version)
        message = self._received()
        changed = self.core.observe(agent_id=AGENT, identity=self.a, event_kind="shared_topic",
                                    timeline_entry_id=message.entry_id, expected_version=p.version)
        self.assertEqual(changed.familiarity, p.familiarity + 2)
        same = self.core.observe(agent_id=AGENT, identity=self.a, event_kind="shared_topic",
                                 timeline_entry_id=message.entry_id, expected_version=changed.version)
        self.assertEqual(same.version, changed.version)

    def test_growth_limits_repeat_sources_and_time(self):
        self.core.enroll(agent_id=AGENT, identity=self.a)
        a = self._received("a")
        b = self._received("b")
        with patch("zhuangfy.relationship.core.time.time", return_value=1_900_000_000):
            first = self.core.observe(agent_id=AGENT, identity=self.a, event_kind="shared_topic",
                                      timeline_entry_id=a.entry_id, expected_version=1)
            ignored = self.core.observe(agent_id=AGENT, identity=self.a, event_kind="shared_topic",
                                        timeline_entry_id=b.entry_id, expected_version=2)
        self.assertEqual(ignored.version, first.version)
        with patch("zhuangfy.relationship.core.time.time", return_value=1_900_100_000):
            second = self.core.observe(agent_id=AGENT, identity=self.a, event_kind="shared_topic",
                                       timeline_entry_id=b.entry_id, expected_version=2)
        self.assertEqual(second.familiarity, first.familiarity + 2)

    def test_cross_user_and_other_agent_sources_rejected(self):
        self.core.enroll(agent_id=AGENT, identity=self.a)
        self.core.enroll(agent_id=AGENT, identity=self.b)
        self.identities.set_consent(self.bob.person_id, "timeline", True)
        message = self._received()
        with self.assertRaises(ValueError):
            self.core.observe(agent_id=AGENT, identity=self.b, event_kind="shared_topic",
                              timeline_entry_id=message.entry_id, expected_version=1)
        with self.assertRaises(ValueError):
            self.core.observe(agent_id="other-agent", identity=self.a, event_kind="shared_topic",
                              timeline_entry_id=message.entry_id, expected_version=1)
        self.assertIsNone(self.core.snapshot(agent_id="unknown", identity=self.a))
        self.assertNotEqual(self.core.snapshot(agent_id=AGENT, identity=self.b).person_id,
                            self.core.snapshot(agent_id=AGENT, identity=self.a).person_id)

    def test_generated_reply_does_not_count_as_delivered_trust_evidence(self):
        self.core.enroll(agent_id=AGENT, identity=self.a)
        msg = self._received()
        draft = self.timeline.generate_reply(
            conversation_id=self.chat.conversation_id, in_reply_to=msg.entry_id,
            source_message_id="out-1", content="我们可以一起排查")
        with self.assertRaises(ValueError):
            self.core.observe(agent_id=AGENT, identity=self.a,
                              event_kind="cooperative_exchange", timeline_entry_id=draft.entry_id,
                              expected_version=1)
        delivered = self.timeline.set_reply_state(
            conversation_id=self.chat.conversation_id, entry_id=draft.entry_id,
            new_state="delivered")
        with self.assertRaises(ValueError):
            self.core.observe(agent_id=AGENT, identity=self.a,
                              event_kind="cooperative_exchange", timeline_entry_id=delivered.entry_id,
                              expected_version=1)
        user_followup = self.timeline.receive(
            conversation_id=self.chat.conversation_id, identity=self.a,
            source_message_id="reply-followup", content="谢谢，我们继续合作")
        changed = self.core.observe(agent_id=AGENT, identity=self.a,
                                    event_kind="cooperative_exchange",
                                    timeline_entry_id=user_followup.entry_id, expected_version=1)
        self.assertEqual(changed.trust, 12)
        with self.assertRaises(ValueError):
            self.core.observe(agent_id=AGENT, identity=self.a,
                              event_kind="shared_topic", timeline_entry_id=draft.entry_id,
                              expected_version=changed.version)

    def test_affection_needs_opt_in_and_can_be_revoked(self):
        p = self.core.enroll(agent_id=AGENT, identity=self.a)
        msg = self._received()
        unchanged = self.core.observe(agent_id=AGENT, identity=self.a,
                                      event_kind="affection_signal",
                                      timeline_entry_id=msg.entry_id, expected_version=1)
        self.assertEqual(unchanged.version, 1)
        p = self.core.correct(agent_id=AGENT, identity=self.a, expected_version=1,
                              allow_affection=True)
        after = self.core.observe(agent_id=AGENT, identity=self.a,
                                  event_kind="affection_signal",
                                  timeline_entry_id=msg.entry_id, expected_version=p.version)
        self.assertEqual(after.closeness, p.closeness + 1)
        later = self.core.correct(agent_id=AGENT, identity=self.a,
                                  expected_version=after.version, allow_affection=False)
        self.assertFalse(later.allow_affection)
        self.assertNotEqual(later.tier, "L3")

    def test_frozen_disabled_and_consent_revocation_block_growth(self):
        p = self.core.enroll(agent_id=AGENT, identity=self.a)
        msg = self._received()
        p = self.core.correct(agent_id=AGENT, identity=self.a,
                              expected_version=p.version, frozen=True)
        ignored = self.core.observe(agent_id=AGENT, identity=self.a, event_kind="shared_topic",
                                    timeline_entry_id=msg.entry_id, expected_version=p.version)
        self.assertEqual(ignored.version, p.version)
        p = self.core.correct(agent_id=AGENT, identity=self.a,
                              expected_version=p.version, frozen=False)
        self.identities.set_consent(self.alice.person_id, "timeline", False)
        with self.assertRaises(PermissionError):
            self.core.observe(agent_id=AGENT, identity=self.a, event_kind="shared_topic",
                              timeline_entry_id=msg.entry_id, expected_version=p.version)
        p = self.core.correct(agent_id=AGENT, identity=self.a, expected_version=p.version,
                              enabled=False)
        self.assertIsNone(self.core.snapshot(agent_id=AGENT, identity=self.a))
        self.assertEqual(self.core.events(agent_id=AGENT, identity=self.a), ())

    def test_user_can_lock_one_dimension_while_others_still_evolve(self):
        p = self.core.enroll(agent_id=AGENT, identity=self.a)
        p = self.core.correct(agent_id=AGENT, identity=self.a,
                              expected_version=p.version, lock_familiarity=True)
        self.assertTrue(p.lock_familiarity)
        self.assertFalse(p.lock_trust)
        first = self._received("lock-msg")
        blocked = self.core.observe(agent_id=AGENT, identity=self.a,
                                    event_kind="shared_topic", timeline_entry_id=first.entry_id,
                                    expected_version=p.version)
        self.assertEqual(blocked.version, p.version)
        p = self.core.correct(agent_id=AGENT, identity=self.a,
                              expected_version=p.version, lock_familiarity=False)
        after = self.core.observe(agent_id=AGENT, identity=self.a,
                                  event_kind="shared_topic", timeline_entry_id=first.entry_id,
                                  expected_version=p.version)
        self.assertEqual(after.familiarity, 12)

    def test_erasure_and_opt_in_again(self):
        self.core.enroll(agent_id=AGENT, identity=self.a)
        self.assertTrue(self.core.erase(agent_id=AGENT, identity=self.a))
        self.assertEqual(self.core.events(agent_id=AGENT, identity=self.a), ())
        self.assertIsNone(self.core.snapshot(agent_id=AGENT, identity=self.a))
        new = self.core.enroll(agent_id=AGENT, identity=self.a)
        self.assertEqual(new.version, 1)
        self.assertEqual(len(self.core.events(agent_id=AGENT, identity=self.a)), 1)


if __name__ == "__main__":
    unittest.main()
