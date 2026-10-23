"""B1 background goals/inquiry tests: SQLite + real builtin tools, no LLM/network."""
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from zhuangfy.agent_v2.goals import GoalStore
from zhuangfy.agent_v2.autonomy import BackgroundGoalRunner, GoalGrant
from zhuangfy.agent_v2.initiative import InitiativeGrant, InitiativeInbox
from zhuangfy.agent_v2.tools import build_builtin_registry
from zhuangfy.agent_v2.runtime import AuthorizedToolRegistry, ToolBinding
from zhuangfy.agent_v2.autonomy_cli import load_policy


class BackgroundTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "state.db"
        self.store = GoalStore(self.db)
        self.clock = [100.0]
        self.grants = (GoalGrant("qq:private:42", frozenset({"calculator.evaluate"})),)
        self.registry = build_builtin_registry()

    def add(self, goal_id="math", **kwargs):
        settings = dict(goal_id=goal_id, owner_scope="qq:private:42",
                        source_event_id="user-consent-123", due_at=100,
                        expires_at=1000, consent=True, tool_name="calculator.evaluate",
                        tool_arguments={"expression": "7*(4+1)"})
        settings.update(kwargs)
        return self.store.create(**settings)

    def runner(self, **kwargs):
        return BackgroundGoalRunner(self.store, self.registry, self.grants,
                                    enabled=True, clock=lambda: self.clock[0],
                                    **kwargs)

    async def test_real_tool_executed_with_persisted_receipt_and_restart(self):
        self.add()
        run = self.runner()
        results = await run.run_once()
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)
        self.assertTrue(results[0].committed)
        self.assertIn("35", results[0].receipt)
        self.assertEqual(GoalStore(self.db).inspect("math")["status"], "done")
        self.assertEqual(len(self.store.audit("math")), 1)
        self.assertEqual(await self.runner().run_once(), ())

    async def test_denied_by_default_and_owner_tool_grants(self):
        self.add()
        self.assertEqual(await BackgroundGoalRunner(
            self.store, self.registry, self.grants, enabled=False).run_once(), ())
        self.assertEqual(self.store.inspect("math")["attempts"], 0)
        no_permission = BackgroundGoalRunner(
            self.store, self.registry,
            (GoalGrant("qq:private:777", frozenset({"calculator.evaluate"})),),
            enabled=True, clock=lambda: self.clock[0])
        self.assertEqual(await no_permission.run_once(), ())
        self.assertEqual(self.store.inspect("math")["attempts"], 0)
        denied_tool = BackgroundGoalRunner(
            self.store, self.registry,
            (GoalGrant("qq:private:42", frozenset({"clock.utc_now"})),),
            enabled=True, clock=lambda: self.clock[0])
        results = await denied_tool.run_once()
        self.assertFalse(results[0].success)
        self.assertEqual(self.store.inspect("math")["status"], "failed")
        self.assertIn("未授权", results[0].receipt)

    async def test_old_b0_goal_without_action_is_never_executed(self):
        self.store.create(goal_id="legacy", owner_scope="qq:private:42",
                          source_event_id="old-consent", due_at=100,
                          expires_at=1000, consent=True)
        self.assertEqual(await self.runner().run_once(), ())
        self.assertEqual(self.store.inspect("legacy")["attempts"], 0)

    async def test_mutating_handler_is_denied_even_with_goal_grant(self):
        calls = []
        async def unsafe(args):
            calls.append(1)
            return "written"
        reg = AuthorizedToolRegistry()
        reg.register(ToolBinding("test.write", unsafe, mutating=True))
        self.add("unsafe", tool_name="test.write", tool_arguments={})
        runner = BackgroundGoalRunner(
            self.store, reg,
            (GoalGrant("qq:private:42", frozenset({"test.write"})),),
            enabled=True, clock=lambda: self.clock[0])
        result = await runner.run_once()
        self.assertFalse(result[0].success)
        self.assertEqual(calls, [])
        self.assertEqual(self.store.inspect("unsafe")["status"], "failed")

    async def test_revoked_while_tool_runs_cannot_commit(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def slow(args):
            started.set()
            await release.wait()
            return "ok"
        reg = AuthorizedToolRegistry()
        reg.register(ToolBinding("test.read", slow))
        self.add("pending", tool_name="test.read", tool_arguments={})
        runner = BackgroundGoalRunner(
            self.store, reg,
            (GoalGrant("qq:private:42", frozenset({"test.read"})),),
            enabled=True, clock=lambda: self.clock[0])
        task = asyncio.create_task(runner.run_once())
        await asyncio.wait_for(started.wait(), 2)
        self.assertTrue(self.store.revoke("pending"))
        release.set()
        out = await asyncio.wait_for(task, 2)
        self.assertFalse(out[0].committed)
        self.assertEqual(self.store.inspect("pending")["status"], "revoked")

    async def test_cancelled_service_does_not_keep_running(self):
        self.add()
        stop = asyncio.Event()
        stop.set()
        await self.runner().serve(stop, poll_seconds=5)
        self.assertEqual(self.store.inspect("math")["attempts"], 0)
        with self.assertRaises(ValueError):
            await self.runner().serve(asyncio.Event(), poll_seconds=0)

    async def test_inquiry_draft_requires_separate_optin(self):
        self.add()
        inbox = InitiativeInbox(self.db)
        grants = (InitiativeGrant("qq:private:42", enabled=True),)
        result = await self.runner(inbox=inbox, inquiry_grants=grants).run_once()
        self.assertTrue(result[0].inquiry_drafted)
        drafts = InitiativeInbox(self.db).list_drafts("qq:private:42")
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["reason"], "completed_authorized_goal")
        self.assertIn("35", drafts[0]["evidence_excerpt"])
        self.assertTrue(inbox.dismiss("math", "qq:private:42"))
        self.assertFalse(inbox.dismiss("math", "qq:private:42"))
        self.assertEqual(inbox.list_drafts("qq:private:42"), ())

    async def test_no_proactive_draft_for_group_or_without_grant(self):
        self.add("no-draft")
        inbox = InitiativeInbox(self.db)
        out = await self.runner(inbox=inbox).run_once()
        self.assertFalse(out[0].inquiry_drafted)
        self.assertEqual(inbox.list_drafts("qq:private:42"), ())
        self.assertFalse(inbox.propose_from_goal(
            goal_id="g", source_event_id="user-ok", owner_scope="qq:group:9",
            evidence="done", now=100, grant=InitiativeGrant("qq:group:9", True)))


class InitiativeTests(unittest.TestCase):
    def test_draft_deduplication_cooldown_daily_budget_and_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            inbox = InitiativeInbox(Path(d) / "goals.db")
            grant = InitiativeGrant("qq:private:42", enabled=True, daily_limit=1)
            opts = dict(owner_scope="qq:private:42", evidence="observed: success", grant=grant)
            self.assertFalse(inbox.propose_from_goal(
                goal_id="x", source_event_id="e", now=100, **{
                    **opts, "grant": InitiativeGrant("qq:private:42", False)}))
            self.assertTrue(inbox.propose_from_goal(
                goal_id="x", source_event_id="e", now=100, **opts))
            self.assertFalse(inbox.propose_from_goal(
                goal_id="x", source_event_id="e", now=101, **opts))
            self.assertFalse(inbox.propose_from_goal(
                goal_id="y", source_event_id="e", now=101, **opts))
            self.assertFalse(inbox.propose_from_goal(
                goal_id="z", source_event_id="e", now=90000,
                **{**opts, "evidence":""}))
            self.assertTrue(inbox.propose_from_goal(
                goal_id="z", source_event_id="e", now=90000, **opts))
            self.assertEqual(len(inbox.list_drafts("qq:private:42")), 2)

    def test_policy_validation(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d) / "config.json"
            cfg={"enabled":True,"owner_grants":{"qq:private:42":{
                "tools":["calculator.evaluate"],"draft_inquiries":True}}}
            path.write_text(json.dumps(cfg), encoding="utf-8")
            enabled, interval, grants, inquiry = load_policy(path)
            self.assertTrue(enabled)
            self.assertEqual(grants[0].owner_scope, "qq:private:42")
            self.assertTrue(inquiry[0].enabled)
            for invalid in (
                {"enabled":"yes","owner_grants":{}},
                {"enabled":True,"owner_grants":{"a":{"tools":[]}}},
                {"enabled":True,"owner_grants":{"a":{"tools":["x","x"]}}},
                {"enabled":True,"owner_grants":{"a":{"tools":["x"],"draft_inquiries":"yes"}}},
            ):
                path.write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_policy(path)

    def test_legacy_sqlite_schema_migrates_without_data_loss(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"old.db"
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE goals (
                    goal_id TEXT PRIMARY KEY, owner_scope TEXT NOT NULL,
                    source_event_id TEXT NOT NULL, consent INTEGER NOT NULL,
                    status TEXT NOT NULL, due_at REAL NOT NULL,
                    expires_at REAL NOT NULL, max_attempts INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_until REAL NOT NULL DEFAULT 0,
                    last_receipt TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL)""")
                db.execute("""INSERT INTO goals
                    (goal_id,owner_scope,source_event_id,consent,status,due_at,
                     expires_at,max_attempts,created_at)
                    VALUES ('old','qq:private:42','source',1,'scheduled',100,500,2,1)""")
            store=GoalStore(path)
            self.assertEqual(store.inspect("old")["tool_name"], "")
            self.assertEqual(store.inspect("old")["tool_arguments_json"], "{}")


if __name__ == "__main__":
    unittest.main()
