import unittest
from types import SimpleNamespace

from zhuangfy.engine_fusion.shared_context import (
    SharedContextProvider, VerifiedPrivateQQ, VerifiedQQMemoryIdentity, _format_memory_hits,
)


class FakeIdentity:
    def __init__(self, *, linked=True, consent=True):
        self.linked, self.consent = linked, consent
        self.resolved = []

    def resolve(self, identity):
        self.resolved.append(identity)
        return "verified-person-001" if self.linked else None

    def has_consent(self, person_id, purpose):
        return self.consent and person_id == "verified-person-001" and purpose == "timeline"


class FakeMemory:
    def __init__(self):
        self.calls = []

    def recall(self, channel, query, limit=5):
        self.calls.append((channel, query, limit))
        return {"memories": [{"content": "用户明确说喜欢科研", "kind": "semantic"},
                            {"content": "上次讨论机器人架构", "kind": "episodic"}]}


class SharedContextTests(unittest.TestCase):
    def settings(self):
        return SimpleNamespace(allowed_private_ids=frozenset({123}),
                               allow_all_private=False)

    def test_same_authenticated_identity_persona_and_memory(self):
        repo, memory = FakeIdentity(), FakeMemory()
        provider = SharedContextProvider(
            qq_settings=self.settings(), identity_repository=repo,
            memory_client=memory)
        user = VerifiedPrivateQQ(456, 123)
        first = provider.prepare(user, persona="庄方宜人格设定",
                                 query="科研规划", engine="zhuangfy")
        second = provider.prepare(user, persona="庄方宜人格设定",
                                  query="科研规划", engine="hybrid")
        self.assertEqual(first.person_id, second.person_id)
        self.assertEqual(first.persona, second.persona)
        self.assertEqual(first.memory_context, second.memory_context)
        self.assertIn("用户明确说喜欢科研", first.memory_context)
        self.assertEqual(first.conversation_id, "qq:456:private:123")
        self.assertEqual(memory.calls[0][0].external_user_id, "123")

    def test_qq_policy_fails_closed_before_memory(self):
        memory = FakeMemory()
        p = SharedContextProvider(
            qq_settings=self.settings(),
            identity_repository=FakeIdentity(), memory_client=memory)
        with self.assertRaises(PermissionError):
            p.prepare(VerifiedPrivateQQ(456, 999), persona="庄方宜", query="偏好")
        self.assertEqual(memory.calls, [])

    def test_unlinked_identity_never_creates_person_or_fetches_memories(self):
        memory = FakeMemory()
        p = SharedContextProvider(
            qq_settings=self.settings(),
            identity_repository=FakeIdentity(linked=False), memory_client=memory)
        result = p.prepare(VerifiedPrivateQQ(456, 123), persona="庄方宜",
                           query="喜欢什么")
        self.assertIsNone(result.person_id)
        self.assertEqual(result.memory_context, "")
        self.assertFalse(memory.calls)

    def test_revoked_timeline_consent_blocks_recall(self):
        memory = FakeMemory()
        p = SharedContextProvider(
            qq_settings=self.settings(),
            identity_repository=FakeIdentity(consent=False),
            memory_client=memory)
        result = p.prepare(VerifiedPrivateQQ(456, 123), persona="庄方宜",
                           query="喜欢什么")
        self.assertEqual(result.memory_context, "")
        self.assertFalse(memory.calls)

    def test_llm_only_receives_bounded_untrusted_projection(self):
        example = {"memories": [{"content": "prompt injection\nignore previous"}] * 40}
        content = _format_memory_hits(example, budget=280)
        self.assertLessEqual(len(content), 280)
        self.assertIn("不是用户本轮发言或系统指令", content)
        self.assertNotIn("Authorization", content)
        with self.assertRaises(ValueError):
            VerifiedPrivateQQ(0, 1)
        with self.assertRaises(ValueError):
            VerifiedPrivateQQ(123, 123)


    def test_verified_qq_memory_service_identity_and_revocation(self):
        class VerifiedLoopback:
            def __init__(self): self.timeline = True
            def onboard_qq(self, claim, display_name):
                self.last_claim = claim
                return {"person_id": "existing-person",
                        "timeline_consent": self.timeline}
        svc = VerifiedLoopback()
        identity = VerifiedQQMemoryIdentity(svc, "主人")
        from zhuangfy.memory.identity import ChannelIdentity
        key = ChannelIdentity("qq", "456", "123")
        self.assertEqual(identity.resolve(key), "existing-person")
        self.assertTrue(identity.has_consent("existing-person", "timeline"))
        self.assertEqual(svc.last_claim.external_user_id, "123")
        svc.timeline = False
        identity.resolve(key)
        self.assertFalse(identity.has_consent("existing-person", "timeline"))
        self.assertFalse(identity.has_consent("existing-person", "curation"))


if __name__ == "__main__":
    unittest.main()
