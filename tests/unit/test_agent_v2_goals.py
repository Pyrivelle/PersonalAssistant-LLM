"""GoalStore B0: offline SQLite persistence, permission, retry and audit tests."""
from pathlib import Path
import tempfile
import unittest

from zhuangfy.agent_v2.goals import GoalStore


class GoalStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "goals.sqlite3"
        self.store = GoalStore(self.path)

    def add(self, name="goal-1", **override):
        kwargs = dict(goal_id=name, owner_scope="qq:private:42",
                      source_event_id="user-approved:1", due_at=100,
                      expires_at=500, consent=True, max_attempts=2)
        kwargs.update(override)
        return self.store.create(**kwargs)

    def test_opt_in_owner_scope_and_restart_survival(self):
        self.assertTrue(self.add())
        self.assertFalse(self.add())  # idempotent create
        self.add("disabled", consent=False)
        self.add("other", owner_scope="qq:group:9")
        self.assertEqual(self.store.claim_due(now=100, owner_scopes=frozenset({"untrusted"})), ())
        reopened = GoalStore(self.path)
        events = reopened.claim_due(now=100, owner_scopes=frozenset({"qq:private:42"}))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].goal_id, "goal-1")
        self.assertEqual(events[0].attempt, 1)
        self.assertEqual(reopened.inspect("goal-1")["status"], "running")
        self.assertFalse(reopened.finish(events[0], now=501, success=True))
        self.assertTrue(reopened.finish(events[0], now=105, success=True, summary="完成只读检查"))
        self.assertEqual(GoalStore(self.path).inspect("goal-1")["status"], "done")
        self.assertEqual(len(self.store.audit("goal-1")), 1)
        self.assertFalse(reopened.finish(events[0], now=106, success=True))
        self.assertEqual(reopened.claim_due(now=110, owner_scopes=frozenset({"qq:private:42"})), ())

    def test_stale_lease_retries_bounded_and_rejects_old_receipt(self):
        self.add()
        first = self.store.claim_due(now=100, owner_scopes=frozenset({"qq:private:42"}),
                                     lease_seconds=5)[0]
        self.assertEqual(self.store.claim_due(now=102, owner_scopes=frozenset({"qq:private:42"})), ())
        second = GoalStore(self.path).claim_due(
            now=106, owner_scopes=frozenset({"qq:private:42"}), lease_seconds=5)[0]
        self.assertEqual(second.attempt, 2)
        self.assertNotEqual(first.event_id, second.event_id)
        self.assertFalse(self.store.finish(first, now=107, success=True))
        self.assertFalse(self.store.finish(second, now=112, success=True))
        self.assertEqual(self.store.claim_due(now=113, owner_scopes=frozenset({"qq:private:42"})), ())
        self.assertEqual(self.store.inspect("goal-1")["status"], "failed")

    def test_failure_reschedules_then_stops_at_budget(self):
        self.add(max_attempts=2)
        first = self.store.claim_due(now=100, owner_scopes=frozenset({"qq:private:42"}))[0]
        self.assertTrue(self.store.finish(first, now=102, success=False,
                                          summary="暂时失败", retry_delay=8))
        self.assertEqual(self.store.inspect("goal-1")["due_at"], 110)
        self.assertEqual(self.store.claim_due(now=109, owner_scopes=frozenset({"qq:private:42"})), ())
        second = self.store.claim_due(now=110, owner_scopes=frozenset({"qq:private:42"}))[0]
        self.assertTrue(self.store.finish(second, now=111, success=False, retry_delay=5))
        self.assertEqual(self.store.inspect("goal-1")["status"], "failed")
        self.assertEqual(len(self.store.audit("goal-1")), 2)

    def test_revoke_blocks_pending_and_running_results(self):
        self.add()
        event = self.store.claim_due(now=100, owner_scopes=frozenset({"qq:private:42"}))[0]
        self.assertTrue(self.store.revoke("goal-1"))
        self.assertFalse(self.store.finish(event, now=101, success=True))
        self.assertEqual(self.store.inspect("goal-1")["status"], "revoked")
        self.assertEqual([x["action"] for x in self.store.audit("goal-1")], ["revoked"])
        self.assertFalse(self.store.revoke("goal-1"))
        self.assertEqual(self.store.claim_due(now=150, owner_scopes=frozenset({"qq:private:42"})), ())

    def test_expiry_and_validation(self):
        self.add("expired", due_at=20, expires_at=60)
        self.assertEqual(self.store.claim_due(now=100, owner_scopes=frozenset({"qq:private:42"})), ())
        self.assertEqual(self.store.inspect("expired")["status"], "expired")
        with self.assertRaises(ValueError):
            self.add("bad", due_at=float("nan"))
        with self.assertRaises(ValueError):
            self.add("bad2", due_at=50, expires_at=40)
        with self.assertRaises(ValueError):
            self.add("bad3", consent="yes")
        with self.assertRaises(ValueError):
            self.store.claim_due(now=100, owner_scopes=frozenset({"bad"}), lease_seconds=0)

    def test_many_other_scopes_cannot_starve_authorized_tasks(self):
        for idx in range(40):
            self.add(f"unauth-{idx}", owner_scope=f"untrusted:{idx}")
        self.add("allowed")
        results = self.store.claim_due(now=100, owner_scopes=frozenset({"qq:private:42"}))
        self.assertEqual([v.goal_id for v in results], ["allowed"])


if __name__ == "__main__":
    unittest.main()
