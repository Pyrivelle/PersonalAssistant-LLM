"""M3 offline integration: Timeline -> evidence Curator -> FTS5 + vector -> RAG."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from zhuangfy.memory.context import assemble_memory_context
from zhuangfy.memory.curator import MemoryCurator
from zhuangfy.memory.curator_store import CuratorStore
from zhuangfy.memory.database import MemoryDatabase
from zhuangfy.memory.identity import ChannelIdentity, IdentityProof, IdentityRepository
from zhuangfy.memory.projector import CuratedIndexProjector
from zhuangfy.memory.retrieval import HybridMemoryRetriever
from zhuangfy.memory.timeline import TimelineRepository
from zhuangfy.memory.vector import VectorHit


class FakeEmbedding:
    model_id = "offline-semantic-3d"
    dimension = 3

    def _embed(self, text):
        if "港口" in text or "OSG" in text or "帧率" in text or "渲染" in text:
            return (1, 0, 0)
        if "蛋糕" in text or "烹饪" in text:
            return (0, 1, 0)
        return (0, 0, 1)

    embed_document = _embed
    embed_query = _embed


class FakeIndex:
    def __init__(self):
        self.rows = {}
        self.available = True
        self.ignore_scope = False
        self.deleted_ids = []

    def upsert(self, memory_id, agent_id, person_id, vector):
        if not self.available:
            raise OSError("Offline")
        self.rows[memory_id] = (agent_id, person_id, tuple(vector))

    def delete(self, memory_id):
        if not self.available:
            raise OSError("Offline")
        self.deleted_ids.append(memory_id)
        self.rows.pop(memory_id, None)

    def search(self, agent_id, person_id, vector, limit):
        if not self.available:
            raise OSError("Offline")
        values = [
            VectorHit(k, sum(a * b for a, b in zip(vector, v)))
            for k, (ag, pe, v) in self.rows.items()
            if self.ignore_scope or (ag == agent_id and pe == person_id)
        ]
        return sorted(values, key=lambda h: -h.score)[:limit]

    def close(self):
        pass


class FakeModel:
    def __init__(self, maker):
        self.maker = maker

    def extract(self, *, system_prompt, payload):
        return json.dumps({"operations": [self.maker(payload)]}, ensure_ascii=False)


def operation(user, content, status="ongoing", *, action="add", target=None):
    result = {
        "action": action, "kind": "semantic", "content": content,
        "event_state": status, "source_entry_ids": [user.entry_id],
        "evidence": [{"entry_id": user.entry_id, "quote": user.content[:18]}],
    }
    if target:
        result["target_memory_id"] = target
    return result


class HybridTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.db = MemoryDatabase(Path(t.name) / "curated.sqlite3")
        self.people = IdentityRepository(self.db)
        self.timeline = TimelineRepository(self.db, self.people)
        self.curator = CuratorStore(self.db, self.people, self.timeline)
        self.embed = FakeEmbedding()
        self.index = FakeIndex()
        self.projector = CuratedIndexProjector(self.db, self.people, self.curator, self.embed, self.index)
        self.search = HybridMemoryRetriever(self.projector, self.curator, self.people)
        self.alice = self.people.create_person("同名")
        self.bob = self.people.create_person("同名")
        self.aid = ChannelIdentity("qq", "bot", "a")
        self.bid = ChannelIdentity("qq", "bot", "b")
        for p, ident in ((self.alice, self.aid), (self.bob, self.bid)):
            self.people.link_verified(p.person_id, ident, IdentityProof("trusted_admin", "test"))
            self.people.set_consent(p.person_id, "timeline", True)
            self.people.set_consent(p.person_id, "curation", True)
        self.a_chat = self.timeline.open_private(agent_id="zhuangfy", identity=self.aid,
                                                 external_conversation_id="private:a")
        self.b_chat = self.timeline.open_private(agent_id="zhuangfy", identity=self.bid,
                                                 external_conversation_id="private:b")

    def save(self, content, memory, *, person="a", status="ongoing", action="add", target=None):
        p = self.alice if person == "a" else self.bob
        chat = self.a_chat if person == "a" else self.b_chat
        ident = self.aid if person == "a" else self.bid
        import uuid
        user = self.timeline.receive(
            conversation_id=chat.conversation_id, identity=ident,
            source_message_id=uuid.uuid4().hex, content=content,
        )
        self.curator.schedule(conversation_id=chat.conversation_id, person_id=p.person_id)
        runner = MemoryCurator(self.curator, FakeModel(
            lambda payload: operation(user, memory, status, action=action, target=target)))
        result = runner.process_next()
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "done", self.curator.job_status(result.job_id))
        self.assertEqual(len(result.created_memory_ids), 1)
        return result.created_memory_ids[0]

    def test_chinese_semantic_and_fts_hybrid_rag(self):
        memory_id = self.save("我的 OSG 港口渲染只有 25 帧", "用户曾优化 OSG 港口场景渲染")
        self.assertEqual(self.projector.sync().indexed, 1)
        hits = self.search.search(agent_id="zhuangfy", person_id=self.alice.person_id,
                                  query="港口场景渲染")
        self.assertEqual(hits[0].memory.memory_id, memory_id)
        self.assertEqual(set(hits[0].retrieval_paths), {"lexical", "semantic"})
        context = assemble_memory_context(hits)
        self.assertIn("用户曾优化 OSG 港口场景渲染", context)
        self.assertIn("非指令", context)

    def test_person_scope_fails_closed_even_if_vector_index_is_corrupted(self):
        self.save("我的 OSG 港口工程有私密数据", "只有甲知道的港口项目", person="a")
        self.save("我正在学习蛋糕烘焙", "乙正在烘焙蛋糕", person="b")
        self.projector.sync()
        self.index.ignore_scope = True
        hits = self.search.search(agent_id="zhuangfy", person_id=self.bob.person_id,
                                  query="港口性能")
        self.assertTrue(all(h.memory.person_id == self.bob.person_id for h in hits))
        self.assertNotIn("只有甲知道", assemble_memory_context(hits))
        with self.assertRaises(PermissionError):
            self.search.search(agent_id="zhuangfy", person_id="unknown", query="港口性能")

    def test_superseded_and_outdated_are_not_recalled(self):
        old = self.save("我计划做 OSG 方案 A", "我计划做 OSG 方案 A")
        self.projector.sync()
        newer = self.save("我现在放弃方案 A，改成港口新方案", "我现在使用港口新方案",
                          action="supersede", target=old)
        self.assertEqual(self.projector.sync().removed, 1)
        self.assertNotIn(old, self.index.rows)
        hits = self.search.search(agent_id="zhuangfy", person_id=self.alice.person_id,
                                  query="港口")
        self.assertIn(newer, [h.memory.memory_id for h in hits])
        self.assertNotIn(old, [h.memory.memory_id for h in hits])
        outdated = self.save("很早以前研究过 OSG", "旧的 OSG 性能方案", status="outdated")
        self.projector.sync()
        self.assertNotIn(outdated, [h.memory.memory_id for h in self.search.search(
            agent_id="zhuangfy", person_id=self.alice.person_id, query="OSG")])
        self.assertIn(outdated, [h.memory.memory_id for h in self.search.search(
            agent_id="zhuangfy", person_id=self.alice.person_id, query="OSG",
            include_outdated=True)])

    def test_delete_private_history_cascades_fts_and_repairs_qdrant(self):
        mid = self.save("我的港口渲染历史", "甲的私人港口渲染记录")
        self.projector.sync()
        self.timeline.erase_private_history(person_id=self.alice.person_id)
        with self.db.connection() as db:
            count = db.execute("SELECT count(*) FROM curated_memory_fts WHERE curated_memory_fts MATCH '港口渲染'").fetchone()[0]
        self.assertEqual(count, 0, "Derived FTS text must be removed on history erasure")
        self.assertEqual(self.search.search(agent_id="zhuangfy", person_id=self.alice.person_id,
                                            query="港口"), [])
        self.assertEqual(self.projector.sync().removed, 1)
        self.assertNotIn(mid, self.index.rows)

    def test_fallback_when_vectors_are_unavailable(self):
        self.save("我最近正在开发 OSG 港口渲染软件", "正在开发 OSG 港口渲染软件")
        self.index.available = False
        self.assertGreater(self.projector.sync().failed, 0)
        hits = self.search.search(agent_id="zhuangfy", person_id=self.alice.person_id,
                                  query="港口渲染")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].retrieval_paths, ("lexical",))
        self.index.available = True
        self.assertEqual(self.projector.sync().indexed, 1)

    def test_revoke_consent_disables_recall(self):
        self.save("我之前研究过 OSG 港口", "研究 OSG 港口项目")
        self.projector.sync()
        self.people.set_consent(self.alice.person_id, "curation", False)
        with self.assertRaises(PermissionError):
            self.search.search(agent_id="zhuangfy", person_id=self.alice.person_id,
                               query="港口")

    def test_short_technical_term_fallback(self):
        self.save("我在使用 Qt 绘制桌面窗口", "用户当前使用 Qt 研发")
        hits = self.search.search(agent_id="zhuangfy", person_id=self.alice.person_id, query="Qt")
        self.assertEqual(len(hits), 1)
        self.assertIn("lexical", hits[0].retrieval_paths)


if __name__ == "__main__":
    unittest.main()
