"""M5 administrator API and evidence tests; no cloud API or model download."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from zhuangfy.memory.client import MemoryChannel, MemoryClient, MemoryClientError
from zhuangfy.memory.coordinator import MemoryCore
from zhuangfy.memory.evaluation import MemoryEvalCase, evaluate_memory_retrieval
from zhuangfy.memory.http_server import build_handler
from zhuangfy.memory.vector import VectorHit


class Embed:
    model_id = "offline-m5"
    dimension = 3

    def embed_document(self, text):
        return [1, 0, 0] if ("OSG" in text or "港口" in text) else [0, 1, 0]

    def embed_query(self, text):
        return self.embed_document(text)


class Index:
    def __init__(self):
        self.points = {}

    def upsert(self, memory_id, agent_id, person_id, vector):
        self.points[memory_id] = (agent_id, person_id, vector)

    def search(self, agent_id, person_id, vector, limit):
        ids = [(mid, sum(a*b for a,b in zip(vector, v)))
               for mid, (ag,pe,v) in self.points.items()
               if (ag, pe) == (agent_id,person_id)]
        return [VectorHit(mid,score) for mid,score in sorted(ids,key=lambda x:-x[1])[:limit]]

    def delete(self, memory_id):
        self.points.pop(memory_id, None)

    def close(self):
        pass


class Model:
    """Produces one grounded episodic memory or semantic memory from real sources."""
    def extract(self, *, system_prompt, payload):
        sources = payload["sources"]
        user = next(x for x in sources if x["role"] == "user")
        delivered = next((x for x in sources if x["role"] == "assistant"), None)
        if delivered:
            ids = [user["entry_id"], delivered["entry_id"]]
            kind = "episodic"
            content = "我曾与这个人讨论 OSG 港口渲染"
        else:
            ids = [user["entry_id"]]
            kind = "semantic"
            content = "用户曾讨论 OSG 港口渲染"
        op = {
            "action": "add", "kind": kind,
            "content": content,
            "source_entry_ids": ids,
            "evidence": [{"entry_id": user["entry_id"], "quote": user["text"][:15]}],
            "event_state": "ongoing",
        }
        return json.dumps({"operations": [op]}, ensure_ascii=False)


CLIENT_TOKEN = "c"*48
ADMIN_TOKEN = "a"*48


class AdminManagementTests(unittest.TestCase):
    def test_admin_people_include_verified_channel_labels(self):
        people = self.admin.list_people()
        alice = next(p for p in people if p["person_id"] == self.alice)
        self.assertTrue(any(identity["external_user_id"] == self.aid.external_user_id
                            for identity in alice["identities"]))

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.core = MemoryCore(Path(tmp.name), embedding=Embed(), index=Index(), curator_model=Model())
        self.server = ThreadingHTTPServer(("127.0.0.1",0),
            build_handler(self.core, client_token=CLIENT_TOKEN, admin_token=ADMIN_TOKEN))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.user = MemoryClient(url=url, token=CLIENT_TOKEN)
        self.admin = MemoryClient(url=url, token=ADMIN_TOKEN)
        self.alice, self.aid = self.setup_person("甲", "a")
        self.bob, self.bid = self.setup_person("乙", "b")
        self.counter = 0

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.core.close()

    def setup_person(self, name, account):
        person = self.admin.admin_create_person(name)["person_id"]
        identity = MemoryChannel("qq","bot-01",account)
        self.admin.admin_link(person,identity,"verified-by-test-admin")
        for p in ("timeline","curation"):
            self.admin.admin_consent(person,p,True)
        return person,identity

    def remember(self, person_id, identity, content="我在学习 OSG 港口渲染", delivered=True):
        previous = {m["memory_id"] for m in self.admin.list_memories(person_id)}
        self.counter += 1
        ref = self.user.receive(identity,chat_id=f"private:{identity.external_user_id}",
                                message_id=f"msg-{self.counter}", content=content)
        if delivered:
            self.user.complete_reply(identity,conversation_id=ref["conversation_id"],
                                     entry_id=ref["entry_id"],reply_id=f"reply-{self.counter}",
                                     content="我会和你继续讨论技术方案。")
        self.core.curated.schedule(
            conversation_id=ref["conversation_id"], person_id=person_id)
        result = self.admin.reflect_one()
        self.assertEqual(result["status"],"done", result)
        new_ids = {m["memory_id"] for m in self.admin.list_memories(person_id)} - previous
        self.assertEqual(len(new_ids), 1)
        return new_ids.pop()

    def test_audited_manual_correction_and_live_projection(self):
        mid = self.remember(self.alice,self.aid)
        before = self.admin.get_evidence(self.alice,mid)
        self.assertEqual(len(before["sources"]),1)
        self.assertFalse(before["memory"]["manual_override"])
        change = self.admin.correct_memory(self.alice,mid,"已经解决了 Qt 绘图接口问题","用户亲自核实新事实")
        self.assertTrue(change["changed"])
        after = self.admin.get_evidence(self.alice,mid)
        self.assertTrue(after["memory"]["manual_override"])
        self.assertEqual(len(after["corrections"]),1)
        self.assertEqual(after["memory"]["content"],"已经解决了 Qt 绘图接口问题")
        self.assertNotIn("我曾与",after["memory"]["content"])
        self.assertEqual(self.core.projector.sync().indexed,0)
        with self.assertRaises(MemoryClientError) as e:
            self.user.correct_memory(self.alice,mid,"随便修改","无权限")
        self.assertEqual(e.exception.status,401)
        for item in self.user.recall(self.aid,"Qt 绘图接口")["memories"]:
            self.assertNotIn("我曾与这个人讨论 OSG 港口渲染",item["content"])

    def test_erasure_clears_fts_index_and_no_other_user_leak(self):
        a = self.remember(self.alice,self.aid)
        b = self.remember(self.bob,self.bid)
        erased = self.admin.forget_memory(self.alice,a)
        self.assertEqual(erased["erased_versions"],1)
        self.assertEqual(self.admin.list_memories(self.alice),[])
        self.assertTrue(any(m["memory_id"]==b for m in self.admin.list_memories(self.bob)))
        self.assertFalse(any(m["memory_id"]==a for m in self.user.recall(self.aid,"港口")["memories"]))
        self.admin.forget_person(self.bob)
        self.assertFalse(any(p["person_id"]==self.bob for p in self.admin.list_people()))
        with self.assertRaises(MemoryClientError) as e:
            self.user.recall(self.bid,"港口")
        self.assertEqual(e.exception.status,403)

    def test_episode_relations_require_two_owned_active_episodes(self):
        first = self.remember(self.alice,self.aid)
        second = self.remember(self.alice,self.aid,content="我还在学习 Qt 窗口绘图")
        foreign = self.remember(self.bob,self.bid)
        self.assertTrue(self.admin.link_episodes(self.alice,first,second,"follow_up")["linked"])
        links = self.admin.get_evidence(self.alice,first)["event_links"]
        self.assertEqual(links[0]["relation"],"follow_up")
        with self.assertRaises(MemoryClientError) as e:
            self.admin.link_episodes(self.alice,first,foreign,"related_to")
        self.assertEqual(e.exception.status,403)

    def test_measurable_retrieval_and_privacy_metrics(self):
        alice = self.remember(self.alice,self.aid)
        bob = self.remember(self.bob,self.bid)
        cases = [
            MemoryEvalCase(self.alice,"OSG 港口渲染",frozenset({alice}),frozenset({bob})),
            MemoryEvalCase(self.bob,"OSG 港口渲染",frozenset({bob}),frozenset({alice})),
        ]
        metrics = evaluate_memory_retrieval(self.core.retriever,agent_id=self.core.agent_id,
                                            cases=cases,k=5)
        self.assertEqual(metrics.cases,2)
        self.assertEqual(metrics.hit_at_k,1.0)
        self.assertEqual(metrics.leakage_count,0)
        self.assertEqual(metrics.stale_count,0)


if __name__ == "__main__":
    unittest.main()
