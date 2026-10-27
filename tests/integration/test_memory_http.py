"""M4 local HTTP integration tests with explicit identity and consent."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from zhuangfy.memory.client import MemoryChannel, MemoryClient, MemoryClientError
from zhuangfy.memory.coordinator import MemoryCore
from zhuangfy.memory.http_server import build_handler, serve
from zhuangfy.memory.vector import VectorHit

CLIENT_TOKEN = "client-key-" + "a" * 40
ADMIN_TOKEN = "admin-key-" + "b" * 40


class Embed:
    model_id = "offline-test-http-3"
    dimension = 3

    def embed_document(self, text):
        return [1, 0, 0]

    def embed_query(self, text):
        return [1, 0, 0]


class Index:
    def __init__(self):
        self.points = {}

    def upsert(self, memory_id, agent_id, person_id, vector):
        self.points[memory_id] = (agent_id, person_id)

    def search(self, agent_id, person_id, vector, limit):
        return [VectorHit(k, 1.0) for k, v in self.points.items()
                if v == (agent_id, person_id)][:limit]

    def delete(self, memory_id):
        self.points.pop(memory_id, None)

    def close(self):
        pass


class HttpMemoryTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.core = MemoryCore(Path(t.name), embedding=Embed(), index=Index())
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            build_handler(self.core, client_token=CLIENT_TOKEN, admin_token=ADMIN_TOKEN),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.client = MemoryClient(url=self.url, token=CLIENT_TOKEN)
        self.admin = MemoryClient(url=self.url, token=ADMIN_TOKEN)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.core.close()

    def test_tokens_and_loopback_safeguards(self):
        with self.assertRaises(ValueError):
            MemoryClient(url="http://example.com:8769", token=CLIENT_TOKEN)
        with self.assertRaises(ValueError):
            serve(self.core, host="0.0.0.0", client_token=CLIENT_TOKEN, admin_token=ADMIN_TOKEN)
        with self.assertRaises(ValueError):
            build_handler(self.core, client_token=CLIENT_TOKEN, admin_token=CLIENT_TOKEN)
        with self.assertRaises(MemoryClientError) as e:
            self.client.admin_create_person("try-escalation")
        self.assertEqual(e.exception.status, 401)
        with self.assertRaises(MemoryClientError) as e:
            self.admin.recall(MemoryChannel("desktop", "local", "abc"), "OSG")
        self.assertEqual(e.exception.status, 401)

    def _person(self, nickname, account):
        p = self.admin.admin_create_person(nickname)
        identity = MemoryChannel("qq", "bot-11", account)
        self.admin.admin_link(p["person_id"], identity, "manual-verified-test")
        for purpose in ("timeline", "curation"):
            self.admin.admin_consent(p["person_id"], purpose, True)
        return p["person_id"], identity

    def test_user_authored_profile_is_versioned_and_private_to_verified_person(self):
        person_id, qq = self._person("Pyri", "owner")
        desktop = MemoryChannel("desktop", "local", "same-person")
        self.admin.admin_link(person_id, desktop, "same-person-approved")
        _, other = self._person("other", "tester")
        saved = self.admin.self_profile_set(
            person_id, expected_version=0,
            full_text="我的技术署名是 Pyrivelle。我正在准备研究生考试。",
            prompt_summary="用户自述：署名 Pyrivelle，简称 Pyri；准备研究生考试。")
        self.assertEqual(saved["version"], 1)
        self.assertEqual(self.admin.self_profile_get(person_id)["full_text"], saved["full_text"])
        self.assertEqual(self.client.self_profile_snapshot(qq)["summary"], saved["prompt_summary"])
        self.assertEqual(self.client.self_profile_snapshot(desktop)["summary"], saved["prompt_summary"])
        self.assertFalse(self.client.self_profile_snapshot(other)["enabled"])
        with self.assertRaises(MemoryClientError):
            self.client.self_profile_get(person_id)
        with self.assertRaises(MemoryClientError):
            self.admin.self_profile_set(person_id, expected_version=0,
                                        full_text="stale", prompt_summary="stale")
        self.client.erase_self(qq)
        self.assertFalse(self.client.self_profile_snapshot(desktop)["enabled"])

    def test_admin_can_rename_person_without_changing_identity(self):
        claim = MemoryChannel("qq", "10001", "20002")
        self.client.onboard_qq(claim, "旧昵称")
        person_id = self.admin.list_people()[0]["person_id"]
        with self.assertRaises(MemoryClientError) as denied:
            self.client.admin_rename_person(person_id, "新名称")
        self.assertEqual(denied.exception.status, 401)
        renamed = self.admin.admin_rename_person(person_id, "  新名称  ")
        self.assertEqual(renamed, {"person_id": person_id, "display_name": "新名称"})
        self.assertEqual(self.client.onboard_qq(claim, "又改 QQ 昵称")["display_name"], "新名称")
        self.assertEqual(self.admin.list_people()[0]["person_id"], person_id)
        with self.assertRaises(MemoryClientError) as invalid:
            self.admin.admin_rename_person(person_id, " \n ")
        self.assertEqual(invalid.exception.status, 400)
        self.assertEqual(self.admin.list_people()[0]["display_name"], "新名称")

    def test_qq_auto_onboard_is_client_only_and_requires_separate_consent(self):
        claim = MemoryChannel("qq", "10001", "20002")
        created = self.client.onboard_qq(claim, "梨愿")
        self.assertTrue(created["created"])
        self.assertTrue(created["person_id"])
        self.assertEqual(created["display_name"], "梨愿")
        self.assertFalse(created["timeline_consent"])
        self.assertFalse(created["curation_consent"])
        again = self.client.onboard_qq(claim, "新的 QQ 昵称")
        self.assertFalse(again["created"])
        self.assertEqual(again["person_id"], created["person_id"])
        self.assertEqual(again["display_name"], "梨愿")
        same_name = self.client.onboard_qq(MemoryChannel("qq", "10001", "20003"), "梨愿")
        self.assertTrue(same_name["created"])
        same_user_different_bot = self.client.onboard_qq(MemoryChannel("qq", "10002", "20002"), "梨愿")
        self.assertTrue(same_user_different_bot["created"])
        people = self.admin.request("/v1/admin/list-people", {})["people"]
        self.assertEqual(len({p["person_id"] for p in people}), 3)
        with self.assertRaises(MemoryClientError) as blocked:
            self.client.receive(claim, chat_id="private:20002", message_id="m1", content="你好")
        self.assertEqual(blocked.exception.status, 403)
        target = next(p for p in people if any(
            item["channel_account_id"] == "10001" and item["external_user_id"] == "20002"
            for item in p["identities"]))
        self.admin.admin_consent(target["person_id"], "timeline", True)
        accepted = self.client.receive(
            claim, chat_id="private:20002", message_id="m1", content="你好")
        self.assertTrue(accepted["entry_id"])
        with self.assertRaises(MemoryClientError) as blocked:
            self.client.onboard_qq(MemoryChannel("desktop", "local", "abc"), "伪造")
        self.assertEqual(blocked.exception.status, 400)
        with self.assertRaises(MemoryClientError) as blocked:
            self.admin.onboard_qq(MemoryChannel("qq", "10001", "20004"), "冒充")
        self.assertEqual(blocked.exception.status, 401)

    def test_auto_capture_is_local_and_never_automatically_curates(self):
        self.core.auto_qq_timeline = True
        claim = MemoryChannel("qq", "10001", "20005")
        profile = self.client.onboard_qq(claim, "新用户")
        self.assertTrue(profile["created"])
        self.assertTrue(profile["timeline_consent"])
        self.assertFalse(profile["curation_consent"])
        captured = self.client.receive(
            claim, chat_id="private:20005", message_id="m-1", content="第一句")
        self.assertTrue(captured["entry_id"])
        people = self.admin.request("/v1/admin/list-people", {})["people"]
        person_id = next(p["person_id"] for p in people if p["display_name"] == "新用户")
        self.admin.admin_consent(person_id, "timeline", False)
        again = self.client.onboard_qq(claim, "新名字")
        self.assertFalse(again["timeline_consent"])
        with self.assertRaises(MemoryClientError) as e:
            self.client.receive(claim, chat_id="private:20005",
                                message_id="m-2", content="删除后再次发消息")
        self.assertEqual(e.exception.status, 403)

    def test_admin_roster_displays_per_person_capture_and_curator_policy(self):
        self.core.auto_qq_timeline = True
        channel = MemoryChannel("qq", "200", "300")
        self.client.onboard_qq(channel, "测试用户")
        roster = self.admin.list_people()
        self.assertEqual(len(roster), 1)
        person = roster[0]
        self.assertEqual(person["display_name"], "测试用户")
        self.assertTrue(person["timeline_enabled"])
        self.assertFalse(person["timeline_revoked"])
        self.assertFalse(person["curation_enabled"])
        self.assertEqual(person["message_count"], 0)
        self.assertEqual(person["memory_count"], 0)
        self.assertEqual(person["jobs"]["pending"], 0)
        captured = self.client.receive(channel, chat_id="private:300",
                                       message_id="m1", content="你好")
        roster = self.admin.list_people()[0]
        self.assertEqual(roster["message_count"], 1)
        self.assertEqual(roster["source_message_count"], 1)
        self.assertEqual(roster["unprocessed_count"], 1)
        self.assertEqual(roster["unscheduled_count"], 1)
        self.assertFalse(roster["curation_enabled"])
        self.admin.admin_consent(person["person_id"], "curation", True)
        job_id = self.core.curated.schedule(
            conversation_id=captured["conversation_id"], person_id=person["person_id"])
        queued = self.admin.list_people()[0]
        self.assertEqual(queued["unprocessed_count"], 1)
        self.assertEqual(queued["unscheduled_count"], 0)
        job = self.admin.list_jobs(person["person_id"])[0]
        self.assertEqual(job["job_id"], job_id)
        self.assertEqual(job["source_count"], 1)
        self.assertEqual(job["platform"], "qq")
        self.assertEqual(job["external_conversation_id"], "private:300")
        self.assertIsNotNone(job["source_first_at"])
        with self.core.database.write() as db:
            db.execute("UPDATE reflection_jobs SET status='done',finished_at=created_at "
                       "WHERE job_id=?", (job_id,))
        self.assertEqual(self.admin.list_people()[0]["unprocessed_count"], 0)
        self.assertTrue(self.admin.list_people()[0]["curation_enabled"])
        self.admin.admin_consent(person["person_id"], "curation", False)
        self.admin.admin_consent(person["person_id"], "timeline", False)
        self.client.onboard_qq(channel, "仍然是测试用户")
        latest = self.admin.list_people()[0]
        self.assertFalse(latest["timeline_enabled"])
        self.assertTrue(latest["timeline_revoked"])
        self.assertFalse(latest["curation_enabled"])

    def test_group_member_profiles_are_group_scoped_and_curator_opt_in(self):
        private = MemoryChannel("qq", "10001", "20001")
        group = MemoryChannel("qq_group", "10001:9", "20001")
        other_group = MemoryChannel("qq_group", "10001:10", "20001")
        private_person = self.client.onboard_qq(private, "阿蓝")
        profile = self.client.onboard_qq_group_member(group, "阿蓝")
        self.assertTrue(profile["created"])
        self.assertTrue(profile["timeline_consent"])
        self.assertFalse(profile["curation_consent"])
        self.assertNotEqual(profile["person_id"], private_person["person_id"])
        self.assertNotEqual(
            self.client.onboard_qq_group_member(other_group, "阿蓝")["person_id"],
            profile["person_id"])
        self.assertEqual(self.client.onboard_qq_group_member(group, "新昵称")["person_id"],
                         profile["person_id"])
        captured = self.client.receive(group, chat_id="group:9:member:20001",
                                       message_id="g1", content="我喜欢研究星图")
        self.assertTrue(captured["entry_id"])
        person = next(p for p in self.admin.list_people()
                      if p["person_id"] == profile["person_id"])
        self.assertEqual(person["message_count"], 1)
        self.assertFalse(person["curation_enabled"])
        self.assertEqual(person["jobs"]["pending"], 0)
        self.admin.admin_consent(profile["person_id"], "curation", True)
        with self.core.database.write() as db:
            db.execute("UPDATE timeline_entries SET recorded_at=recorded_at-90000 "
                       "WHERE entry_id=?", (captured["entry_id"],))
        self.assertGreaterEqual(self.core.schedule_due_reflections(), 1)
        jobs = self.admin.list_jobs(profile["person_id"])
        self.assertEqual(jobs[0]["source_count"], 1)
        self.assertEqual(jobs[0]["platform"], "qq_group")
        self.assertEqual(jobs[0]["external_conversation_id"],
                         "group:9:member:20001")
        with self.assertRaises(MemoryClientError):
            self.client.onboard_qq_group_member(
                MemoryChannel("qq_group", "10001:bad", "20001"), "伪造")

    def test_task_origin_bridge_is_scoped_idempotent_and_erasure_safe(self):
        from zhuangfy.agent_v2.goals import GoalStore
        from zhuangfy.agent_v2.task_memory_bridge import link_existing_qq_goal

        self.core.auto_qq_timeline = True
        owner = MemoryChannel("qq", "10001", "20001")
        other = MemoryChannel("qq", "10001", "20002")
        self.client.onboard_qq(owner, "同名")
        self.client.onboard_qq(other, "同名")
        msg = self.client.receive(
            owner, chat_id="private:20001", message_id="qq-1",
            content="明天继续优化港口场景")
        other_msg = self.client.receive(
            other, chat_id="private:20002", message_id="qq-2",
            content="明天继续优化港口场景")
        goals = GoalStore(self.core.root / "goals.sqlite3")
        self.assertTrue(goals.create(
            goal_id="goal-001", owner_scope="qq:10001:private:20001",
            source_event_id=msg["entry_id"], due_at=10, expires_at=200,
            consent=True))
        created = link_existing_qq_goal(
            goals=goals, admin=self.admin, goal_id="goal-001",
            bot_id="10001", user_id="20001")
        self.assertTrue(created["created"])
        again = link_existing_qq_goal(
            goals=goals, admin=self.admin, goal_id="goal-001",
            bot_id="10001", user_id="20001")
        self.assertFalse(again["created"])
        result = self.client.task_origin(owner, "goal-001")
        self.assertEqual(result["origin_entry_id"], msg["entry_id"])
        self.assertEqual(result["evidence"][0]["source_message_id"], "qq-1")
        # Task IDs cannot be hijacked by an overlapping nickname.
        with self.assertRaises(MemoryClientError) as e:
            self.client.task_origin(other, "goal-001")
        self.assertEqual(e.exception.status, 403)
        with self.assertRaises(PermissionError):
            link_existing_qq_goal(goals=goals, admin=self.admin,
                                  goal_id="goal-001", bot_id="10001",
                                  user_id="20002")
        with self.assertRaises(MemoryClientError) as e:
            self.admin.admin_bind_goal_origin(
                owner, task_id="goal-001", owner_scope="qq:10001:private:20001",
                source_entry_id=other_msg["entry_id"])
        self.assertEqual(e.exception.status, 403)
        with self.assertRaises(MemoryClientError) as e:
            self.admin.admin_bind_goal_origin(
                owner, task_id="goal-002", owner_scope="qq:10001:private:20001",
                source_entry_id=other_msg["entry_id"])
        self.assertEqual(e.exception.status, 403)
        with self.assertRaises(MemoryClientError) as e:
            self.admin.admin_bind_goal_origin(
                owner, task_id="goal-002", owner_scope="qq:private:20001",
                source_entry_id=msg["entry_id"])
        self.assertEqual(e.exception.status, 403)
        with self.assertRaises(MemoryClientError) as e:
            self.admin.admin_bind_goal_origin(
                owner, task_id="goal-002", owner_scope="qq:10001:private:20001",
                source_entry_id="made-up-entry")
        self.assertEqual(e.exception.status, 403)
        # A non-admin channel token cannot register invented tasks.
        with self.assertRaises(MemoryClientError) as e:
            self.client.admin_bind_goal_origin(
                owner, task_id="forged", owner_scope="qq:10001:private:20001",
                source_entry_id=msg["entry_id"])
        self.assertEqual(e.exception.status, 401)
        # A revoked Timeline is not readable as task context.
        p = next(p for p in self.admin.list_people() if
                 p["display_name"] == "同名" and any(
                    i["external_user_id"] == "20001" for i in p["identities"]))
        self.admin.admin_consent(p["person_id"], "timeline", False)
        with self.assertRaises(MemoryClientError) as e:
            self.client.task_origin(owner, "goal-001")
        self.assertEqual(e.exception.status, 403)
        self.admin.admin_consent(p["person_id"], "timeline", True)
        self.assertEqual(self.client.task_origin(owner, "goal-001")["task_id"], "goal-001")
        self.client.erase_self(owner)
        with self.assertRaises(MemoryClientError) as e:
            self.client.task_origin(owner, "goal-001")
        self.assertEqual(e.exception.status, 403)
        with self.core.database.connection() as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM task_memory_tasks").fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT count(*) FROM task_memory_links").fetchone()[0], 0)

    def test_task_continuity_finds_previous_shared_topics_without_curator(self):
        self.core.auto_qq_timeline = True
        a = MemoryChannel("qq", "10001", "20001")
        b = MemoryChannel("qq", "10001", "20002")
        other_bot = MemoryChannel("qq", "10002", "20001")
        for identity in (a, b, other_bot):
            self.client.onboard_qq(identity, "相同昵称")
        source = self.client.receive(
            a, chat_id="private:20001", message_id="origin-1",
            content="我们研究OSG港口场景的遮挡剔除和性能优化")
        other_task = self.client.receive(
            a, chat_id="private:20001", message_id="origin-2",
            content="我们复习英语语法分析长难句")
        continuation = self.client.receive(
            a, chat_id="private:20001", message_id="next-1",
            content="OSG遮挡剔除继续研究LOD简化细节")
        current_turn = self.client.receive(
            a, chat_id="private:20001", message_id="next-2",
            content="OSG遮挡剔除这个事情今天继续聊")
        self.client.receive(
            b, chat_id="private:20002", message_id="b-1",
            content="OSG港口场景的遮挡剔除和性能优化")
        self.client.receive(
            other_bot, chat_id="private:20001", message_id="c-1",
            content="OSG港口场景的遮挡剔除和性能优化")
        self.admin.admin_bind_goal_origin(
            a, task_id="goal-osg", owner_scope="qq:10001:private:20001",
            source_entry_id=source["entry_id"])
        self.admin.admin_bind_goal_origin(
            a, task_id="goal-english", owner_scope="qq:10001:private:20001",
            source_entry_id=other_task["entry_id"])
        result = self.client.task_recall(
            a, "接着聊OSG遮挡剔除的做法", limit=2,
            exclude_entry_id=current_turn["entry_id"])
        self.assertEqual([x["task_id"] for x in result["tasks"]], ["goal-osg"])
        item = result["tasks"][0]
        self.assertEqual(item["origin"]["relation"], "verified_origin")
        self.assertEqual(item["task_status"], "unverified")
        self.assertEqual(item["origin"]["entry_id"], source["entry_id"])
        self.assertTrue(any(h["entry_id"] == continuation["entry_id"]
                            for h in item["history"]))
        self.assertTrue(all(h["relation"] == "possible_related"
                            for h in item["history"]))
        self.assertFalse(any(h["entry_id"] == current_turn["entry_id"]
                             for h in item["history"]))
        self.assertEqual(item["curated_candidates"], [])
        person = next(p for p in self.admin.list_people() if
                      any(v["channel_account_id"] == "10001"
                          and v["external_user_id"] == "20001"
                          for v in p["identities"]))
        desktop = MemoryChannel("desktop", "local", "approved-desktop")
        self.admin.admin_link(person["person_id"], desktop, "verified-same-user")
        self.assertEqual(self.client.resolve_identity(desktop), person["person_id"])
        desktop_recall = self.client.task_recall(desktop, "继续 OSG 遮挡剔除")
        self.assertEqual(desktop_recall["tasks"][0]["task_id"], "goal-osg")
        self.assertEqual(self.client.task_recall(
            a, "晚上吃饭", limit=2)["tasks"], [])
        self.assertEqual(self.client.task_recall(
            b, "OSG遮挡剔除", limit=2)["tasks"], [])
        self.assertEqual(self.client.task_recall(
            other_bot, "OSG遮挡剔除", limit=2)["tasks"], [])
        # Recreate service-layer object to verify persistent sources are
        # discoverable without in-process continuity state.
        from zhuangfy.memory.task_continuity import TaskContinuity
        resumed = TaskContinuity(
            self.core.database, self.core.identities, self.core.timeline,
            self.core.task_links, self.core.retriever)
        from zhuangfy.memory.identity import ChannelIdentity
        self.assertEqual(resumed.recall(
            agent_id=self.core.agent_id,
            identity=ChannelIdentity("qq", "10001", "20001"),
            query="OSG遮挡剔除")["tasks"][0]["task_id"], "goal-osg")
        person = next(p for p in self.admin.list_people() if
                      any(v["channel_account_id"] == "10001"
                          and v["external_user_id"] == "20001"
                          for v in p["identities"]))
        self.admin.admin_consent(person["person_id"], "timeline", False)
        with self.assertRaises(MemoryClientError):
            self.client.resolve_identity(desktop)
        with self.assertRaises(MemoryClientError) as e:
            self.client.task_recall(a, "OSG遮挡剔除")
        self.assertEqual(e.exception.status, 403)
        self.admin.admin_consent(person["person_id"], "timeline", True)
        self.assertEqual(
            self.client.task_recall(a, "OSG遮挡剔除")["tasks"][0]["task_id"], "goal-osg")
        self.client.erase_self(a)
        self.assertEqual(self.client.task_recall(b, "OSG遮挡剔除")["tasks"], [])
        with self.assertRaises(MemoryClientError) as e:
            self.client.task_recall(a, "OSG遮挡剔除")
        self.assertEqual(e.exception.status, 403)

    def test_task_continuity_curated_candidates_require_separate_consent(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        self.core.auto_qq_timeline = True
        identity = MemoryChannel("qq", "10001", "20007")
        self.client.onboard_qq(identity, "交流者")
        origin = self.client.receive(
            identity, chat_id="private:20007", message_id="origin-cur",
            content="之前研究过OSG港口的遮挡剔除优化")
        self.admin.admin_bind_goal_origin(
            identity, task_id="goal-cur", owner_scope="qq:10001:private:20007",
            source_entry_id=origin["entry_id"])
        person = self.admin.list_people()[0]
        fabricated_hit = SimpleNamespace(memory=SimpleNamespace(
            memory_id="some-curated-id", content="用户计划检查OSG遮挡剔除是否完成",
            event_state="completed", source_entry_ids=(origin["entry_id"],)))
        with patch.object(self.core.retriever, "search",
                          return_value=[fabricated_hit]) as retrieve:
            unapproved = self.client.task_recall(identity, "OSG遮挡剔除")
            retrieve.assert_not_called()
        self.assertEqual(unapproved["tasks"][0]["curated_candidates"], [])
        self.admin.admin_consent(person["person_id"], "curation", True)
        with patch.object(self.core.retriever, "search",
                          return_value=[fabricated_hit]) as retrieve:
            approved = self.client.task_recall(identity, "OSG遮挡剔除")
            retrieve.assert_called_once()
        item = approved["tasks"][0]
        self.assertEqual(item["task_status"], "unverified")
        self.assertEqual(item["curated_candidates"][0]["relation"],
                         "semantic_candidate")
        self.assertEqual(item["curated_candidates"][0]["event_state"], "completed")

    def test_task_continuity_does_not_auto_create_or_mark_complete(self):
        self.core.auto_qq_timeline = True
        claim = MemoryChannel("qq", "10001", "20006")
        self.client.onboard_qq(claim, "相同昵称")
        self.client.receive(claim, chat_id="private:20006", message_id="m1",
                            content="我计划未来完成港口模型")
        self.assertEqual(self.client.task_recall(
            claim, "港口模型未来的安排")["tasks"], [])
        # Invalid requests cannot scan other scopes or return bulk history.
        for text in ("", "a" * 501):
            with self.assertRaises(MemoryClientError) as err:
                self.client.task_recall(claim, text)
            self.assertEqual(err.exception.status, 400)
        with self.assertRaises(MemoryClientError) as err:
            self.client.task_recall(claim, "港口模型", limit=100)
        self.assertEqual(err.exception.status, 400)

    def test_task_progress_reports_reducer_and_append_only_correction(self):
        self.core.auto_qq_timeline = True
        owner = MemoryChannel("qq", "10001", "20101")
        other = MemoryChannel("qq", "10001", "20102")
        self.client.onboard_qq(owner, "研究者")
        self.client.onboard_qq(other, "研究者")
        origin = self.client.receive(owner, chat_id="private:20101",
                                     message_id="start-task",
                                     content="我们要研究OSG港口场景的软件遮挡剔除")
        self.admin.admin_bind_goal_origin(
            owner, task_id="goal-render", owner_scope="qq:10001:private:20101",
            source_entry_id=origin["entry_id"])
        self.assertEqual(self.client.task_state(owner, "goal-render")["state"], "proposed")
        one = self.client.receive(owner, chat_id="private:20101", message_id="m-progress-1",
                                  content="我已经开始研究OSG港口场景的软件遮挡剔除")
        self.assertEqual(self.client.task_state(owner, "goal-render")["state"], "active")
        # A replay of an existing source cannot double-apply task progress.
        again = self.client.receive(owner, chat_id="private:20101",
                                    message_id="m-progress-1",
                                    content="我已经开始研究OSG港口场景的软件遮挡剔除")
        self.assertEqual(one, again)
        self.assertEqual(len(self.client.task_state(owner, "goal-render")["events"]), 1)
        self.client.receive(owner, chat_id="private:20101", message_id="m-blocked",
                            content="OSG港口场景的软件遮挡剔除遇到瓶颈")
        self.assertEqual(self.client.task_state(owner, "goal-render")["state"], "blocked")
        done = self.client.receive(owner, chat_id="private:20101",
                                   message_id="m-done",
                                   content="OSG港口场景的软件遮挡剔除已经完成了")
        snapshot = self.client.task_state(owner, "goal-render")
        self.assertEqual(snapshot["state"], "reported_completed")
        self.assertEqual(snapshot["status_basis"], "user_report")
        self.assertEqual(len(snapshot["events"]), 3)
        completed = snapshot["events"][-1]
        self.assertEqual(completed["kind"], "reported_completed")
        self.assertEqual(completed["source"], "user_report")
        self.assertEqual(completed["source_entry_id"], done["entry_id"])
        # A hedged claim is only a candidate and cannot change the reducer.
        self.client.receive(owner, chat_id="private:20101", message_id="m-hedged",
                            content="OSG港口场景的软件遮挡剔除可能完成了")
        snapshot = self.client.task_state(owner, "goal-render")
        self.assertEqual(snapshot["state"], "reported_completed")
        self.assertEqual(snapshot["events"][-1]["review_state"], "pending")
        # The client is NOT an administrator and cannot approve uncertainty.
        with self.assertRaises(MemoryClientError) as denied:
            self.client.admin_task_event_review(
                owner, event_id=snapshot["events"][-1]["event_id"], action="accept")
        self.assertEqual(denied.exception.status, 401)
        # A true follow-up correction is source-backed, append-only and scoped.
        revised = self.client.receive(
            owner, chat_id="private:20101", message_id="m-correction",
            content="之前说OSG港口场景的软件遮挡剔除已经完成，其实没完成")
        correction = self.admin.admin_task_event_correct(
            owner, task_id="goal-render", source_entry_id=revised["entry_id"],
            supersedes_event_id=completed["event_id"], reported_state="active")
        self.assertTrue(correction["created"])
        repeat = self.admin.admin_task_event_correct(
            owner, task_id="goal-render", source_entry_id=revised["entry_id"],
            supersedes_event_id=completed["event_id"], reported_state="active")
        self.assertFalse(repeat["created"])
        updated = self.client.task_state(owner, "goal-render")
        self.assertEqual(updated["state"], "active")
        self.assertEqual(updated["events"][-1]["kind"], "correction")
        self.assertEqual(updated["events"][-1]["supersedes_event_id"],
                         completed["event_id"])
        with self.assertRaises(MemoryClientError) as denied:
            self.admin.admin_task_event_correct(
                other, task_id="goal-render", source_entry_id=revised["entry_id"],
                supersedes_event_id=completed["event_id"], reported_state="blocked")
        self.assertEqual(denied.exception.status, 403)
        with self.assertRaises(MemoryClientError) as denied:
            self.client.task_state(other, "goal-render")
        self.assertEqual(denied.exception.status, 403)
        # The phase-2 continuity read shows reported progress, never factual
        # completion and never a GoalStore execution receipt.
        recall = self.client.task_recall(owner, "OSG港口场景遮挡剔除")
        self.assertEqual(recall["tasks"][0]["task_status"], "unverified")
        self.assertEqual(recall["tasks"][0]["reported_progress"]["state"], "active")
        self.assertEqual(recall["tasks"][0]["reported_progress"]["status_basis"],
                         "user_report")
        # Revocation blocks reading; erasure cleans up all task events.
        person = next(p for p in self.admin.list_people() if any(
            i["external_user_id"] == "20101" for i in p["identities"]))
        self.admin.admin_consent(person["person_id"], "timeline", False)
        with self.assertRaises(MemoryClientError) as denied:
            self.client.task_state(owner, "goal-render")
        self.assertEqual(denied.exception.status, 403)
        self.admin.admin_consent(person["person_id"], "timeline", True)
        self.client.erase_self(owner)
        with self.core.database.connection() as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM task_progress_events").fetchone()[0], 0)

    def test_task_progress_rejects_vague_or_cross_task_inferences(self):
        self.core.auto_qq_timeline = True
        identity = MemoryChannel("qq", "10001", "20202")
        self.client.onboard_qq(identity, "对话者")
        first = self.client.receive(
            identity, chat_id="private:20202", message_id="origin-a",
            content="准备研究OSG港口场景软件遮挡剔除")
        second = self.client.receive(
            identity, chat_id="private:20202", message_id="origin-b",
            content="准备研究OSG场景软件遮挡剔除")
        self.admin.admin_bind_goal_origin(
            identity, task_id="goal-a", owner_scope="qq:10001:private:20202",
            source_entry_id=first["entry_id"])
        self.admin.admin_bind_goal_origin(
            identity, task_id="goal-b", owner_scope="qq:10001:private:20202",
            source_entry_id=second["entry_id"])
        # An unspecified "that thing" is not evidence of which task changed.
        self.client.receive(identity, chat_id="private:20202",
                            message_id="vague", content="那个问题终于解决了")
        self.assertEqual(self.client.task_state(identity, "goal-a")["events"], [])
        self.assertEqual(self.client.task_state(identity, "goal-b")["events"], [])
        self.client.receive(identity, chat_id="private:20202",
                            message_id="ambiguous",
                            content="OSG场景软件遮挡剔除已经开始做了")
        one = self.client.task_state(identity, "goal-a")
        two = self.client.task_state(identity, "goal-b")
        self.assertEqual(one["state"], "proposed")
        self.assertEqual(two["state"], "proposed")
        self.assertEqual(one["events"][-1]["review_state"], "pending")
        self.assertEqual(two["events"][-1]["review_state"], "pending")
        chosen = two["events"][-1]["event_id"]
        reviewed = self.admin.admin_task_event_review(
            identity, event_id=chosen, action="accept")
        self.assertTrue(reviewed["changed"])
        self.assertEqual(self.client.task_state(identity, "goal-b")["state"], "active")
        self.assertEqual(self.client.task_state(identity, "goal-a")["state"], "proposed")
        rejected = self.admin.admin_task_event_review(
            identity, event_id=one["events"][-1]["event_id"], action="reject")
        self.assertEqual(rejected["review_state"], "rejected")
        with self.assertRaises(MemoryClientError) as error:
            self.admin.admin_task_event_review(
                identity, event_id=chosen, action="reject")
        self.assertEqual(error.exception.status, 400)
        # A message written after a goal does not automatically create a goal,
        # and ungrounded planned intentions are not progress completions.
        self.client.receive(identity, chat_id="private:20202", message_id="future",
                            content="OSG软件遮挡剔除计划明天已经完成")
        self.assertEqual(self.client.task_state(identity, "goal-b")["state"], "active")
    def test_task_origin_must_be_real_received_user_entry(self):
        self.core.auto_qq_timeline = True
        owner = MemoryChannel("qq", "10001", "20001")
        self.client.onboard_qq(owner, "测试")
        msg = self.client.receive(
            owner, chat_id="private:20001", message_id="qq-3", content="开始任务")
        reply = self.client.complete_reply(
            owner, conversation_id=msg["conversation_id"],
            entry_id=msg["entry_id"], reply_id="bot-3", content="明白")
        with self.assertRaises(MemoryClientError) as e:
            self.admin.admin_bind_goal_origin(
                owner, task_id="goal-003", owner_scope="qq:10001:private:20001",
                source_entry_id=reply["entry_id"])
        self.assertEqual(e.exception.status, 403)
        self.assertTrue(self.admin.admin_bind_goal_origin(
            owner, task_id="goal-003", owner_scope="qq:10001:private:20001",
            source_entry_id=msg["entry_id"])["created"])
        # Persistence through a new repository instance (e.g. process restart).
        from zhuangfy.memory.task_links import TaskMemoryLinks
        restored = TaskMemoryLinks(self.core.database, self.core.identities, self.core.timeline)
        from zhuangfy.memory.identity import ChannelIdentity
        self.assertEqual(
            restored.get_task(
                agent_id=self.core.agent_id,
                identity=ChannelIdentity("qq", "10001", "20001"),
                task_id="goal-003")["origin_entry_id"], msg["entry_id"])

    def test_verified_channel_identity_and_historical_turn(self):
        a, aid = self._person("甲", "alice")
        b, bid = self._person("同名", "bob")
        self.assertNotEqual(a, b)
        user = self.client.receive(aid, chat_id="private:alice", message_id="qq123",
                                   content="今天讨论 OSG 渲染")
        self.assertEqual(self.client.receive(aid, chat_id="private:alice", message_id="qq123",
                                            content="今天讨论 OSG 渲染"), user)
        with self.assertRaises(MemoryClientError) as e:
            self.client.receive(aid, chat_id="private:alice", message_id="qq123",
                                content="伪造重复消息")
        self.assertEqual(e.exception.status, 400)
        with self.assertRaises(MemoryClientError) as e:
            self.client.complete_reply(bid, conversation_id=user["conversation_id"],
                entry_id=user["entry_id"], reply_id="reply1", content="窃取用户私聊")
        self.assertEqual(e.exception.status, 403)
        with self.assertRaises(MemoryClientError) as e:
            self.client.receive(MemoryChannel("qq", "bot-11", "unknown"),
                chat_id="private:unknown", message_id="event", content="你好")
        self.assertEqual(e.exception.status, 403)
        reply = self.client.complete_reply(aid, conversation_id=user["conversation_id"],
                                           entry_id=user["entry_id"], reply_id="reply1",
                                           content="我们可以先做剔除实验。")
        self.assertEqual(reply["state"], "delivered")
        self.assertEqual(self.client.complete_reply(
            aid, conversation_id=user["conversation_id"], entry_id=user["entry_id"],
            reply_id="reply1", content="我们可以先做剔除实验。")["entry_id"], reply["entry_id"])
        self.assertIsNone(reply["reflection_job_id"])

    def test_delivery_is_bound_to_original_channel_even_if_person_is_shared(self):
        alice, qq = self._person("甲", "alice")
        desktop = MemoryChannel("desktop", "local", "alice-primary-desktop")
        self.admin.admin_link(alice, desktop, "verified-two-channel-test")
        reference = self.client.receive(
            qq, chat_id="private:alice", message_id="qq-message", content="讨论港口帧率")
        with self.assertRaises(MemoryClientError) as failure:
            self.client.complete_reply(
                desktop, conversation_id=reference["conversation_id"],
                entry_id=reference["entry_id"], reply_id="desktop-not-qq",
                content="这个确认不属于 QQ 频道")
        self.assertEqual(failure.exception.status, 403)
        self.assertEqual(self.client.complete_reply(
            qq, conversation_id=reference["conversation_id"],
            entry_id=reference["entry_id"], reply_id="qq-actual-ack",
            content="我们可以讨论剔除方案")["state"], "delivered")

    def test_relationship_http_opt_in_correction_event_and_atomic_erasure(self):
        alice, qq = self._person("甲", "alice")
        bob, other = self._person("乙", "bob")
        self.assertFalse(self.client.relationship_snapshot(qq)["enabled"])
        self.assertFalse(self.admin.relationship_get(qq)["enrolled"])
        with self.assertRaises(MemoryClientError) as err:
            self.client.relationship_enroll(qq, initial_tier="L3", allow_affection=True)
        self.assertEqual(err.exception.status, 401)  # Admin token only.
        prof = self.admin.relationship_enroll(qq, initial_tier="L1")
        self.assertEqual(prof["tier"], "L1")
        self.assertEqual(self.admin.relationship_get(qq)["profile"]["tier"], "L1")
        snap = self.client.relationship_snapshot(qq)
        self.assertTrue(snap["enabled"])
        self.assertIn("可信宿主建议", snap["hint"])
        self.assertFalse(self.client.relationship_snapshot(other)["enabled"])
        with self.assertRaises(MemoryClientError) as err:
            self.client.relationship_correct(qq, expected_version=1, changes={"frozen": True})
        self.assertEqual(err.exception.status, 401)
        revised = self.admin.relationship_correct(qq, expected_version=1,
                 changes={"familiarity": 73, "trust": 64, "tier_ceiling": "L1",
                          "avoid_nicknames": True})
        self.assertEqual(revised["version"], 2)
        self.assertEqual(self.client.relationship_snapshot(qq)["tier"], "L1")
        user = self.client.receive(qq, chat_id="private:alice",
                                   message_id="qq-relationship-1",
                                   content="我们继续讨论港口渲染插件")
        with self.assertRaises(MemoryClientError) as err:
            self.client.relationship_apply(other, source_entry_id=user["entry_id"],
                   event_kind="shared_topic", confidence=.99,
                   evidence_quote="港口渲染插件", expected_version=1)
        self.assertEqual(err.exception.status, 400)
        grown = self.client.relationship_apply(qq, source_entry_id=user["entry_id"],
                  event_kind="shared_topic", confidence=.95,
                  evidence_quote="港口渲染插件", expected_version=2)
        self.assertEqual(grown["version"], 3)
        self.assertEqual(self.client.relationship_apply(qq, source_entry_id=user["entry_id"],
                         event_kind="shared_topic", confidence=.95,
                         evidence_quote="港口渲染插件", expected_version=3)["version"], 3)
        self.assertGreaterEqual(len(self.admin.relationship_events(qq)["events"]), 2)
        self.assertTrue(self.client.erase_self(qq)["erased"])
        with self.core.database.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM relationship_profiles WHERE person_id=?",
                                        (alice,)).fetchone()[0], 0)
        with self.assertRaises(MemoryClientError) as err:
            self.client.relationship_snapshot(qq)
        self.assertEqual(err.exception.status, 403)

    def test_erasure_revokes_both_consents_and_does_not_silently_recapture(self):
        alice, qq = self._person("甲", "alice")
        desktop = MemoryChannel("desktop", "local", "alice-desktop")
        self.admin.admin_link(alice, desktop, "verified-two-channel-test")
        ref = self.client.receive(
            qq, chat_id="private:alice", message_id="qq-1", content="应当遗忘的对话")
        self.client.receive(
            desktop, chat_id="desktop:voice", message_id="voice-1",
            content="同一人物通过其他渠道说的话")
        erased = self.client.erase_self(qq)
        self.assertTrue(erased["erased"])
        self.assertTrue(erased["retention_disabled"])
        with self.core.database.connection() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM private_conversations WHERE person_id=?",
                (alice,)).fetchone()[0], 0)
        for channel, chat in ((qq, "private:alice"), (desktop, "desktop:voice")):
            with self.assertRaises(MemoryClientError) as e:
                self.client.receive(channel, chat_id=chat, message_id="new-message",
                                    content="后续消息不应自动重新保存")
            self.assertEqual(e.exception.status, 403)
            with self.assertRaises(MemoryClientError) as e:
                self.client.recall(channel, "之前的内容")
            self.assertEqual(e.exception.status, 403)
        for purpose in ("timeline", "curation"):
            self.assertFalse(self.core.identities.has_consent(alice, purpose))
        # Only a NEW explicit admin authorization may restore logging.
        self.admin.admin_consent(alice, "timeline", True)
        self.admin.admin_consent(alice, "curation", True)
        self.assertEqual(self.client.receive(
            qq, chat_id="private:alice", message_id="new-allowed",
            content="经重新授权的新事件")["conversation_id"] is not None, True)

    def test_consent_is_enforced_and_self_erasure_is_scoped(self):
        a, aid = self._person("甲", "alice")
        b, bid = self._person("乙", "bob")
        alice_ref = self.client.receive(aid, chat_id="private:alice", message_id="a1",
                                        content="甲的私人历史")
        self.client.receive(bid, chat_id="private:bob", message_id="b1",
                            content="乙的私人历史")
        self.admin.admin_consent(a, "timeline", False)
        with self.assertRaises(MemoryClientError) as e:
            self.client.receive(aid, chat_id="private:alice", message_id="a2",
                                content="未授权的新消息")
        self.assertEqual(e.exception.status, 403)
        self.admin.admin_consent(a, "timeline", True)
        self.assertTrue(self.client.erase_self(aid)["erased"])
        self.assertFalse(self.core.timeline.erase_private_history(person_id=a))
        with self.core.database.connection() as db:
            remaining = db.execute("SELECT COUNT(*) FROM private_conversations WHERE person_id=?",
                                   (b,)).fetchone()[0]
        self.assertEqual(remaining, 1)
        with self.assertRaises(MemoryClientError) as e:
            self.client.recall(aid, "旧事")
        self.assertEqual(e.exception.status, 403)
        self.assertFalse(self.core.identities.has_consent(a, "timeline"))
        self.assertFalse(self.core.identities.has_consent(a, "curation"))


if __name__ == "__main__":
    unittest.main()
