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

    def test_qq_auto_onboard_is_client_only_and_requires_separate_consent(self):
        claim = MemoryChannel("qq", "10001", "20002")
        created = self.client.onboard_qq(claim, "梨愿")
        self.assertTrue(created["created"])
        self.assertEqual(created["display_name"], "梨愿")
        self.assertFalse(created["timeline_consent"])
        self.assertFalse(created["curation_consent"])
        again = self.client.onboard_qq(claim, "新的 QQ 昵称")
        self.assertFalse(again["created"])
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
