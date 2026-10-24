"""M1 identity/Timeline tests run without any LLM, Qdrant or network access."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zhuangfy.memory.database import MemoryDatabase
from zhuangfy.memory.identity import ChannelIdentity, IdentityProof, IdentityRepository
from zhuangfy.memory.timeline import TimelineRepository


PROOF = IdentityProof(method="trusted_admin", reference="explicit-local-confirmation-test")


class IdentityTimelineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        database = MemoryDatabase(Path(temp.name) / "memory.sqlite3")
        self.people = IdentityRepository(database)
        self.timeline = TimelineRepository(database, self.people)
        self.alice = self.people.create_person("同名")
        self.bob = self.people.create_person("同名")
        self.alice_qq = ChannelIdentity("qq", "bot-01", "account-01")
        self.bob_qq = ChannelIdentity("qq", "bot-01", "account-02")
        self.people.link_verified(self.alice.person_id, self.alice_qq, PROOF)
        self.people.link_verified(self.bob.person_id, self.bob_qq, PROOF)

    def grant(self, person_id: str):
        self.people.set_consent(person_id, "timeline", True)
        self.people.set_consent(person_id, "curation", True)

    def open_alice(self):
        return self.timeline.open_private(
            agent_id="zhuangfy-main", identity=self.alice_qq,
            external_conversation_id="private:account-01",
        )

    def test_auto_qq_onboarding_is_idempotent_and_does_not_grant_consent(self):
        source = ChannelIdentity("qq", "10001", "20002")
        other = ChannelIdentity("qq", "10001", "20003")
        other_bot = ChannelIdentity("qq", "10002", "20002")
        first, created = self.people.ensure_qq_person(source, "梨愿")
        self.assertTrue(created)
        self.assertEqual(first.display_name, "梨愿")
        again, created = self.people.ensure_qq_person(source, "改过的昵称")
        self.assertFalse(created)
        self.assertEqual(first.person_id, again.person_id)
        self.assertEqual(again.display_name, "梨愿")
        second, _ = self.people.ensure_qq_person(other, "梨愿")
        third, _ = self.people.ensure_qq_person(other_bot, "梨愿")
        self.assertEqual(len({first.person_id, second.person_id, third.person_id}), 3)
        self.assertEqual(self.people.resolve(source), first.person_id)
        self.assertFalse(self.people.has_consent(first.person_id, "timeline"))
        self.assertFalse(self.people.has_consent(first.person_id, "curation"))
        with self.assertRaises(PermissionError):
            self.timeline.open_private(
                agent_id="zhuangfy-main", identity=source,
                external_conversation_id="private:20002")

    def test_qq_auto_timeline_requires_explicit_service_policy_and_is_not_regranted(self):
        claim = ChannelIdentity("qq", "10001", "20004")
        person, created = self.people.ensure_qq_person(
            claim, "首次来访者", initial_timeline=True)
        self.assertTrue(created)
        self.assertTrue(self.people.has_consent(person.person_id, "timeline"))
        self.assertFalse(self.people.has_consent(person.person_id, "curation"))
        self.people.set_consent(person.person_id, "timeline", False)
        again, created = self.people.ensure_qq_person(
            claim, "再次私聊", initial_timeline=True)
        self.assertFalse(created)
        self.assertEqual(again.person_id, person.person_id)
        self.assertFalse(self.people.has_consent(person.person_id, "timeline"))
        self.assertFalse(self.people.has_consent(person.person_id, "curation"))

    def test_existing_qq_identity_gets_local_timeline_once_but_not_after_revocation(self):
        linked = ChannelIdentity("qq", "200", "301")
        person = self.people.create_person("已有账号")
        self.people.link_verified(person.person_id, linked, PROOF)
        self.assertFalse(self.people.has_consent(person.person_id, "timeline"))
        reused, created = self.people.ensure_qq_person(
            linked, "新昵称", initial_timeline=True)
        self.assertFalse(created)
        self.assertEqual(reused.person_id, person.person_id)
        self.assertTrue(self.people.has_consent(person.person_id, "timeline"))
        self.assertFalse(self.people.has_consent(person.person_id, "curation"))
        self.people.set_consent(person.person_id, "timeline", False)
        self.people.ensure_qq_person(linked, "重连", initial_timeline=True)
        self.assertFalse(self.people.has_consent(person.person_id, "timeline"))

    def test_auto_qq_onboarding_rejects_non_qq_or_forged_ids(self):
        for claim in (
            ChannelIdentity("desktop", "local", "member"),
            ChannelIdentity("qq", "00", "100"),
            ChannelIdentity("qq", "100", "100"),
            ChannelIdentity("qq", "100", "-5"),
        ):
            with self.assertRaises(ValueError):
                self.people.ensure_qq_person(claim, "同名")
        existing = self.people.create_person("管理员指定姓名")
        trusted = ChannelIdentity("qq", "200", "300")
        self.people.link_verified(existing.person_id, trusted, PROOF)
        reused, created = self.people.ensure_qq_person(trusted, "不同昵称")
        self.assertFalse(created)
        self.assertEqual(reused.person_id, existing.person_id)
        self.assertEqual(reused.display_name, "管理员指定姓名")

    def test_names_are_not_person_id_and_unknown_identities_are_not_auto_merged(self):
        self.assertNotEqual(self.alice.person_id, self.bob.person_id)
        self.assertEqual(self.people.get_person(self.alice.person_id).display_name, "同名")
        self.assertEqual(self.people.resolve(self.alice_qq), self.alice.person_id)
        self.assertEqual(self.people.resolve(self.bob_qq), self.bob.person_id)
        self.assertIsNone(self.people.resolve(ChannelIdentity("qq", "bot-01", "unknown")))
        self.assertIsNone(self.people.resolve(ChannelIdentity("qq", "bot-02", "account-01")))

    def test_explicit_cross_channel_link_and_collision_prevention(self):
        desktop = ChannelIdentity("desktop", "local", "approved-primary-user")
        with self.assertRaises(ValueError):
            self.people.link_verified(self.alice.person_id, desktop, IdentityProof("nickname", "same name"))
        with self.assertRaises(ValueError):
            self.people.link_verified(self.alice.person_id, desktop, IdentityProof("trusted_admin", ""))
        self.people.link_verified(self.alice.person_id, desktop, PROOF)
        self.people.link_verified(self.alice.person_id, desktop, PROOF)  # idempotent
        self.assertEqual(self.people.resolve(desktop), self.alice.person_id)
        with self.assertRaises(ValueError):
            self.people.link_verified(self.bob.person_id, desktop, PROOF)

    def test_no_timeline_without_explicit_consent(self):
        with self.assertRaises(PermissionError):
            self.open_alice()
        self.people.set_consent(self.alice.person_id, "timeline", True)
        conversation = self.open_alice()
        first = self.timeline.receive(
            conversation_id=conversation.conversation_id, identity=self.alice_qq,
            source_message_id="qq-001", content="我正在做港口项目",
        )
        self.assertEqual(first.state, "received")
        with self.assertRaises(PermissionError):
            self.timeline.curated_source_entries(
                conversation_id=conversation.conversation_id, person_id=self.alice.person_id,
            )
        self.people.set_consent(self.alice.person_id, "curation", True)
        self.assertEqual(len(self.timeline.curated_source_entries(
            conversation_id=conversation.conversation_id, person_id=self.alice.person_id,
        )), 1)
        self.people.set_consent(self.alice.person_id, "curation", False)
        with self.assertRaises(PermissionError):
            self.timeline.curated_source_entries(
                conversation_id=conversation.conversation_id, person_id=self.alice.person_id,
            )
        self.people.set_consent(self.alice.person_id, "timeline", False)
        with self.assertRaises(PermissionError):
            self.timeline.receive(conversation_id=conversation.conversation_id,
                                  identity=self.alice_qq, source_message_id="qq-002", content="继续")
        # Consent revocation is not silent erasure, verified separately.

    def test_reply_lifecycle_only_delivered_replies_are_curatable(self):
        self.grant(self.alice.person_id)
        chat = self.open_alice()
        inbound = self.timeline.receive(
            conversation_id=chat.conversation_id, identity=self.alice_qq,
            source_message_id="qq-in", content="我曾遇到港口帧率问题",
        )
        outbound = self.timeline.generate_reply(
            conversation_id=chat.conversation_id, in_reply_to=inbound.entry_id,
            source_message_id="qq-reply", content="我记得我们讨论过 LOD。",
        )
        only_inbound = self.timeline.curated_source_entries(
            conversation_id=chat.conversation_id, person_id=self.alice.person_id,
        )
        self.assertEqual([m.entry_id for m in only_inbound], [inbound.entry_id])
        delivered = self.timeline.set_reply_state(
            conversation_id=chat.conversation_id, entry_id=outbound.entry_id, new_state="delivered")
        self.assertEqual(delivered.state, "delivered")
        self.assertEqual(self.timeline.set_reply_state(
            conversation_id=chat.conversation_id, entry_id=outbound.entry_id,
            new_state="delivered").state, "delivered")
        with self.assertRaises(ValueError):
            self.timeline.set_reply_state(
                conversation_id=chat.conversation_id, entry_id=outbound.entry_id, new_state="discarded")
        rows = self.timeline.curated_source_entries(
            conversation_id=chat.conversation_id, person_id=self.alice.person_id,
        )
        self.assertEqual([m.state for m in rows], ["received", "delivered"])
        second = self.timeline.generate_reply(
            conversation_id=chat.conversation_id, in_reply_to=inbound.entry_id,
            source_message_id="qq-unsent", content="未发出的错误消息",
        )
        self.timeline.set_reply_state(conversation_id=chat.conversation_id, entry_id=second.entry_id,
                                      new_state="discarded")
        rows = self.timeline.curated_source_entries(
            conversation_id=chat.conversation_id, person_id=self.alice.person_id)
        self.assertEqual([x.entry_id for x in rows], [inbound.entry_id, outbound.entry_id])

    def test_repeated_message_id_is_idempotent_and_conflicting_replay_is_rejected(self):
        self.grant(self.alice.person_id)
        chat = self.open_alice()
        a = self.timeline.receive(conversation_id=chat.conversation_id, identity=self.alice_qq,
                                  source_message_id="event-1", content="你好")
        b = self.timeline.receive(conversation_id=chat.conversation_id, identity=self.alice_qq,
                                  source_message_id="event-1", content="你好")
        self.assertEqual(a.entry_id, b.entry_id)
        with self.assertRaises(ValueError):
            self.timeline.receive(conversation_id=chat.conversation_id, identity=self.alice_qq,
                                  source_message_id="event-1", content="一条被篡改的内容")

    def test_cannot_leak_another_person_private_history(self):
        self.grant(self.alice.person_id)
        self.grant(self.bob.person_id)
        chat = self.open_alice()
        self.timeline.receive(conversation_id=chat.conversation_id, identity=self.alice_qq,
                              source_message_id="s1", content="只属于 Alice 的事情")
        with self.assertRaises(PermissionError):
            self.timeline.curated_source_entries(conversation_id=chat.conversation_id,
                                                 person_id=self.bob.person_id)
        with self.assertRaises(PermissionError):
            self.timeline.receive(conversation_id=chat.conversation_id, identity=self.bob_qq,
                                  source_message_id="s2", content="我是 Bob")
        with self.assertRaises(PermissionError):
            self.timeline.open_private(agent_id="zhuangfy-main", identity=self.bob_qq,
                                       external_conversation_id="private:account-01")

    def test_references_require_true_received_message_in_same_conversation(self):
        self.grant(self.alice.person_id)
        self.grant(self.bob.person_id)
        chat = self.open_alice()
        with self.assertRaises(ValueError):
            self.timeline.generate_reply(conversation_id=chat.conversation_id,
                                         in_reply_to="invented-turn-id", source_message_id="r1",
                                         content="不真实的共同经历")

    def test_retention_erasure_explicitly_removes_history(self):
        self.grant(self.alice.person_id)
        chat = self.open_alice()
        self.timeline.receive(conversation_id=chat.conversation_id, identity=self.alice_qq,
                              source_message_id="s1", content="用户隐私内容")
        self.assertEqual(self.timeline.erase_private_history(person_id=self.alice.person_id), 1)
        with self.assertRaises(ValueError):
            self.timeline.curated_source_entries(conversation_id=chat.conversation_id,
                                                 person_id=self.alice.person_id)


if __name__ == "__main__":
    unittest.main()
