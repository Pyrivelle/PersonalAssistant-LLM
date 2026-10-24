"""V3 bound Memory Skill: no accounts, network, or external model calls."""
from __future__ import annotations
import unittest
from unittest.mock import Mock

from zhuangfy.agent_v2.discovery import SkillDiscovery
from zhuangfy.agent_v2.memory_skill import create_turn_registry
from zhuangfy.agent_v2.skill_access import SkillAccessPolicy, SkillGrant, SkillPrincipal
from zhuangfy.agent_v2.tools import build_builtin_registry


class SkillGrantTests(unittest.TestCase):
    def test_exact_identity_capability_and_resource_default_deny(self):
        a = SkillPrincipal.verified_qq_private(bot_id=100, user_id=200)
        b = SkillPrincipal.verified_qq_private(bot_id=100, user_id=201)
        different_bot = SkillPrincipal.verified_qq_private(bot_id=101, user_id=200)
        self.assertNotEqual(a.key, b.key)
        self.assertNotEqual(a.key, different_bot.key)
        policy = SkillAccessPolicy((SkillGrant(
            principal_key=a.key, capability="task_memory.search",
            resource_id="own_timeline", expires_at=900),))
        self.assertTrue(policy.allows(a, "task_memory.search",
                                      resource_id="own_timeline", now=899))
        self.assertFalse(policy.allows(a, "task_memory.search",
                                       resource_id="own_timeline", now=900))
        self.assertFalse(policy.allows(b, "task_memory.search",
                                       resource_id="own_timeline", now=1))
        self.assertFalse(policy.allows(different_bot, "task_memory.search",
                                       resource_id="own_timeline", now=1))
        self.assertFalse(policy.allows(a, "mijia.device_control",
                                       resource_id="lamp-001", now=1))
        self.assertFalse(policy.allows(a, "task_memory.search",
                                       resource_id="someone_else_timeline", now=1))
        self.assertFalse(policy.allows(a, "task_memory.search",
                                       resource_id="own_timeline",
                                       mutating=True, now=1))
        self.assertFalse(SkillAccessPolicy().allows(
            a, "mijia.device_control", resource_id="lamp-001", now=1))
        # Even an explicit placeholder allow_mutation grant cannot authorize
        # real-world actions until a separate confirmation protocol exists.
        fake_device_grant = SkillAccessPolicy((SkillGrant(
            principal_key=a.key, capability="mijia.device_control",
            resource_id="lamp-001", allow_mutation=True),))
        self.assertFalse(fake_device_grant.allows(
            a, "mijia.device_control", resource_id="lamp-001",
            mutating=True, now=1))

    def test_noncanonical_or_untrusted_identity_rejected(self):
        for bot_id, user_id in ((0, 3), (3, 0), (-1, 2), ("100", 200), (True, 3)):
            with self.assertRaises(ValueError):
                SkillPrincipal.verified_qq_private(bot_id=bot_id, user_id=user_id)


class BoundMemorySkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_per_turn_principal_secrecy_and_no_cross_account_reuse(self):
        client = Mock()
        def memory_reply(channel, query, limit):
            return {"tasks": [{"task_id": "private-task",
                      "origin": {"excerpt": "这个账号的 OSG 项目"},
                      "reported_progress": {"state": "active", "status_basis": "user_report"},
                      "history": []}]}
        client.task_recall.side_effect = memory_reply
        base = build_builtin_registry()
        a = SkillPrincipal.verified_qq_private(bot_id=9, user_id=42)
        b = SkillPrincipal.verified_qq_private(bot_id=9, user_id=43)
        reg_a = create_turn_registry(base, memory_client=client, principal=a,
                                     memory_capture_verified=True)
        reg_b = create_turn_registry(base, memory_client=client, principal=b,
                                     memory_capture_verified=True)
        self.assertNotIn("task_memory.search", base.public_names(scope="private"))
        self.assertNotIn("task_memory.search",
                         SkillDiscovery(reg_a).select("今天心情不错", scope="private").names)
        shortlist = SkillDiscovery(reg_a).select(
            "方宜，还记得之前 OSG 项目吗？", scope="private")
        self.assertIn("task_memory.search", shortlist.deferred_names)
        self.assertNotIn("task_memory.search",
                         SkillDiscovery(reg_a).select("还记得之前", scope="group").names)
        result = await reg_a.invoke("task_memory.search", {"query": "OSG 项目"},
                                    scope="private")
        self.assertTrue(result.success, result.summary)
        self.assertIn("用户报告", result.summary)
        self.assertEqual(client.task_recall.call_args.args[0].external_user_id, "42")
        other_result = await reg_b.invoke("task_memory.search", {"query": "OSG 项目"},
                                          scope="private")
        self.assertTrue(other_result.success)
        self.assertEqual(client.task_recall.call_args.args[0].external_user_id, "43")
        self.assertFalse((await reg_a.invoke(
            "task_memory.search", {"query": "OSG 项目", "user_id": "43"},
            scope="private")).success)
        self.assertFalse((await reg_a.invoke(
            "task_memory.search", {"query": "OSG 项目", "person_id": "other"},
            scope="private")).success)
        self.assertFalse((await reg_a.invoke(
            "task_memory.search", {"query": "OSG 项目"},
            scope="group")).success)

    async def test_no_access_without_trusted_capture_and_fail_closed(self):
        client = Mock()
        principal = SkillPrincipal.verified_qq_private(bot_id=10, user_id=11)
        base = build_builtin_registry()
        disabled = create_turn_registry(
            base, memory_client=client, principal=principal,
            memory_capture_verified=False)
        self.assertIs(disabled, base)
        self.assertNotIn("task_memory.search", base.public_names(scope="private"))
        enabled = create_turn_registry(
            base, memory_client=client, principal=principal,
            memory_capture_verified=True)
        client.task_recall.return_value = {"tasks": []}
        result = await enabled.invoke("task_memory.search", {"query": "OSG"},
                                      scope="private")
        self.assertTrue(result.success)
        self.assertIn("未找到", result.summary)
        client.task_recall.side_effect = PermissionError("Timeline revoked")
        denied = await enabled.invoke("task_memory.search", {"query": "OSG"},
                                      scope="private")
        self.assertFalse(denied.success)
        self.assertNotIn("Timeline revoked", denied.summary)
