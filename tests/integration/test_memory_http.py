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
        job = self.core.curated.job_status(reply["reflection_job_id"])
        self.assertEqual(job[0], "pending")

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
        self.assertEqual(self.client.recall(aid, "旧事"), {"memories": []})


if __name__ == "__main__":
    unittest.main()
