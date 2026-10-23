"""Focus arbitration tests: one foreground Planner, explicit membership and privacy."""
import unittest

from zhuangfy.agent_v2.focus import FocusCoordinator, FocusGroup, FocusLease, FocusSignal


def signal(session, mid, at, priority=0, directed=False, privacy="group"):
    return FocusSignal(session, mid, "message", at, priority, directed, privacy)


class FocusCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.focus = FocusCoordinator()
        self.focus.register_group(FocusGroup("test-circle", frozenset({
            "qq:group:9", "qq:private:42", "weixin:group:5"
        }), max_consecutive=2))

    def test_requires_explicit_membership_and_admission(self):
        self.assertEqual(self.focus.scope_for("qq:group:9"), "test-circle")
        self.assertIsNone(self.focus.scope_for("qq:private:777"))
        self.assertFalse(self.focus.offer(signal("qq:group:9", "1", 0)))
        self.assertFalse(self.focus.offer(signal("qq:private:777", "1", 0),
                                          authorized=True))
        self.assertTrue(self.focus.offer(signal("qq:group:9", "1", 0),
                                         authorized=True))
        self.assertFalse(self.focus.offer(signal("qq:group:9", "1", 0),
                                          authorized=True))
        self.assertTrue(self.focus.offer(signal("qq:private:42", "1", 0, privacy="private"),
                                         authorized=True))
        self.assertEqual(self.focus.snapshot("test-circle")["pending_count"], 2)

    def test_directed_message_preempts_passive_and_one_active(self):
        self.focus.offer(signal("qq:group:9", "passive", 0, priority=50), authorized=True)
        self.focus.offer(signal("qq:private:42", "urgent", 1, priority=5,
                                directed=True, privacy="private"), authorized=True)
        lease = self.focus.claim("test-circle", now=1)
        self.assertEqual(lease.signal.event_id, "urgent")
        self.assertIsNone(self.focus.claim("test-circle", now=2))
        self.focus.complete(lease)
        nxt = self.focus.claim("test-circle", now=2)
        self.assertEqual(nxt.signal.event_id, "passive")
        self.focus.complete(nxt)

    def test_fairness_between_chat_streams(self):
        for i in range(5):
            self.focus.offer(signal("qq:group:9", str(i), i), authorized=True)
        self.focus.offer(signal("weixin:group:5", "other", 1), authorized=True)
        chosen = []
        for now in range(6, 12):
            lease = self.focus.claim("test-circle", now=now)
            chosen.append(lease.signal.session_id)
            self.focus.complete(lease)
        self.assertIn("weixin:group:5", chosen[:3])
        self.assertEqual(len(chosen), 6)

    def test_separate_focus_groups_never_steal_context(self):
        other = FocusGroup("other", frozenset({"weixin:private:3"}))
        self.focus.register_group(other)
        with self.assertRaises(ValueError):
            self.focus.register_group(FocusGroup("collision", frozenset({"qq:group:9"})))
        self.assertTrue(self.focus.offer(signal("weixin:private:3", "x", 2,
                                               privacy="private"), authorized=True))
        self.assertIsNone(self.focus.claim("test-circle", now=2))
        self.assertEqual(self.focus.claim("other", now=2).signal.session_id, "weixin:private:3")
        # Snapshot exposes metadata only, not any private conversation text.
        self.assertNotIn("content", self.focus.snapshot("other"))

    def test_backpressure_cancel_and_stale_lease(self):
        focus = FocusCoordinator()
        focus.register_group(FocusGroup("limited", frozenset({"a", "b"}),
                                        max_pending_per_session=2, max_total_pending=3))
        for i in range(2):
            self.assertTrue(focus.offer(signal("a", str(i), i), authorized=True))
        self.assertFalse(focus.offer(signal("a", "2", 2), authorized=True))
        self.assertTrue(focus.offer(signal("b", "b", 2), authorized=True))
        self.assertFalse(focus.offer(signal("b", "c", 2), authorized=True))
        self.assertEqual(focus.cancel("limited", session_id="a"), 2)
        lease = focus.claim("limited", now=3)
        focus.complete(lease)
        with self.assertRaises(ValueError):
            focus.complete(lease)

    def test_invalid_identity_priority_or_scope_rejected(self):
        with self.assertRaises(ValueError):
            signal("", "id", 0)
        with self.assertRaises(ValueError):
            signal("a", "id", 0, priority=101)
        with self.assertRaises(ValueError):
            FocusGroup("invalid", frozenset())


if __name__ == "__main__":
    unittest.main()
