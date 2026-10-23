"""R1-C/E: independent candidate inference, evidence checks and reversible changes."""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path

from zhuangfy.memory.database import MemoryDatabase
from zhuangfy.memory.identity import IdentityRepository, ChannelIdentity, IdentityProof
from zhuangfy.memory.timeline import TimelineRepository
from zhuangfy.relationship import RelationshipCore, RelationshipEvolution, RelationshipCandidate

class RelationshipEvolutionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = MemoryDatabase(Path(tmp.name) / "mem.sqlite3")
        identities = IdentityRepository(db)
        self.identity = ChannelIdentity("qq", "main", "me")
        self.other = ChannelIdentity("qq", "main", "other")
        me = identities.create_person("Alice")
        other = identities.create_person("Bob")
        for p, who in ((me,self.identity),(other,self.other)):
            identities.link_verified(p.person_id, who, IdentityProof("trusted_admin", "test"))
            identities.set_consent(p.person_id, "timeline", True)
        self.timeline = TimelineRepository(db, identities)
        self.core = RelationshipCore(db, identities)
        self.evolve = RelationshipEvolution(self.core)
        self.profile = self.core.enroll(agent_id="a", identity=self.identity)
        self.other_profile = self.core.enroll(agent_id="a", identity=self.other)
        self.conv = self.timeline.open_private(agent_id="a", identity=self.identity,
                                               external_conversation_id="dm:me")

    def msg(self, text, mid):
        return self.timeline.receive(conversation_id=self.conv.conversation_id,
                                     identity=self.identity, source_message_id=mid, content=text)

    def candidate(self, kind, quote, id, conf=.95):
        return RelationshipCandidate(kind, conf, quote, id)

    def test_model_can_propose_but_without_matching_original_quote_it_cannot_apply(self):
        entry = self.msg("这次我们继续讨论港口渲染插件", "1")
        p = self.evolve.propose(agent_id="a", identity=self.identity,
             source_entry_id=entry.entry_id,
             classifier=lambda prompt, source: json.dumps({
                 "event_kind": "shared_topic", "confidence": .95,
                 "evidence_quote": "我们在北京旅行过" }))
        self.assertEqual(p.event_kind, "shared_topic")
        with self.assertRaises(ValueError):
            self.evolve.apply(agent_id="a", identity=self.identity, candidate=p,
                              expected_version=self.profile.version)
        self.assertEqual(self.core.snapshot(agent_id="a", identity=self.identity).version, 1)

    def test_grounded_suggestion_causes_small_growth_and_manual_lock_overrides(self):
        entry = self.msg("继续讨论港口渲染插件应该怎么封装", "1")
        p = self.evolve.propose(agent_id="a", identity=self.identity,
              source_entry_id=entry.entry_id,
              classifier=lambda prompt, source: json.dumps({
                  "event_kind": "shared_topic", "confidence": .95,
                  "evidence_quote": "港口渲染插件" }))
        grown = self.evolve.apply(agent_id="a", identity=self.identity, candidate=p,
                                  expected_version=1)
        self.assertEqual(grown.familiarity, 12)
        locked = self.core.correct(agent_id="a", identity=self.identity,
                   expected_version=grown.version, familiarity=50, lock_familiarity=True)
        second = self.msg("我还想讨论港口渲染插件", "2")
        suggestion = self.candidate("shared_topic", "港口渲染插件", second.entry_id)
        still = self.evolve.apply(agent_id="a", identity=self.identity, candidate=suggestion,
                                  expected_version=locked.version)
        self.assertEqual(still.familiarity, 50)

    def test_affection_no_grant_from_a_user_statement_or_model_claim(self):
        entry = self.msg("以后你可以对我亲近一点", "1")
        proposal = self.candidate("affection_signal", "你可以对我亲近一点", entry.entry_id)
        unchanged = self.evolve.apply(agent_id="a", identity=self.identity,
                                      candidate=proposal, expected_version=1)
        self.assertEqual(unchanged.version, 1)
        accepted = self.core.correct(agent_id="a", identity=self.identity,
                       expected_version=1, allow_affection=True)
        grown = self.evolve.apply(agent_id="a", identity=self.identity,
                                  candidate=proposal, expected_version=accepted.version)
        self.assertEqual(grown.closeness, 1)

    def test_confidence_floor_missing_evidence_cross_identity_and_unknown_kind(self):
        entry = self.msg("今天看起来港口性能不错", "1")
        p = self.candidate("shared_topic", "港口性能不错", entry.entry_id, .7)
        self.assertEqual(self.evolve.apply(agent_id="a", identity=self.identity,
                         candidate=p, expected_version=1).version, 1)
        with self.assertRaises(ValueError):
            self.evolve.apply(agent_id="a", identity=self.other,
                              candidate=self.candidate("shared_topic", "港口性能不错", entry.entry_id),
                              expected_version=1)
        with self.assertRaises(ValueError):
            RelationshipCandidate.parse('{"event_kind":"root","confidence":0.99,"evidence_quote":"港口"}',
                                        entry.entry_id)

    def test_unsent_bot_text_cannot_become_user_event(self):
        entry = self.msg("我在研究插件", "1")
        draft = self.timeline.generate_reply(conversation_id=self.conv.conversation_id,
              in_reply_to=entry.entry_id, source_message_id="2", content="我们已经很熟悉了")
        with self.assertRaises(ValueError):
            self.evolve.propose(agent_id="a", identity=self.identity,
                                source_entry_id=draft.entry_id,
                                classifier=lambda prompt, source: "{}")

if __name__ == "__main__":
    unittest.main()
