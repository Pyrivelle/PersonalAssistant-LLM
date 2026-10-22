"""Offline memory tests: no LLM, internet, QQ, or model download."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from zhuangfy.memory import MemoryVectorService, SQLiteMemoryRepository, VectorHit
from zhuangfy.memory.vector import checked_vector


class FakeEmbedding:
    model_id = "unit-test-3d-v1"
    dimension = 3

    def _embed(self, text):
        if "港口" in text or "OSG" in text or "帧率" in text:
            return [1.0, 0.0, 0.0]
        if "蛋糕" in text:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]

    embed_document = _embed
    embed_query = _embed


class FakeIndex:
    def __init__(self):
        self.items = {}
        self.fail_upsert = False
        self.fail_delete = False
        self.ignore_filter = False

    def upsert(self, memory_id, agent_id, person_id, vector):
        if self.fail_upsert:
            raise OSError("Simulated vector storage offline")
        self.items[memory_id] = (agent_id, person_id, list(vector))

    def delete(self, memory_id):
        if self.fail_delete:
            raise OSError("Simulated vector storage offline")
        self.items.pop(memory_id, None)

    def search(self, agent_id, person_id, vector, limit):
        found = []
        for memory_id, (a, p, v) in self.items.items():
            if self.ignore_filter or (a == agent_id and p == person_id):
                score = sum(x * y for x, y in zip(vector, v))
                found.append(VectorHit(memory_id, score))
        found.sort(key=lambda x: x.score, reverse=True)
        return found[:limit]

    def close(self):
        return None


class MemoryVectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = SQLiteMemoryRepository(Path(self.temp.name) / "memory.sqlite3")
        self.index = FakeIndex()
        self.embedding = FakeEmbedding()
        self.svc = MemoryVectorService(self.repo, self.index, self.embedding, agent_id="zhuangfy-instance-01")
        self.a = str(uuid4())
        self.b = str(uuid4())

    def test_store_recall_and_reopen(self):
        result = self.svc.remember(person_id=self.a, content="一起排查 OSG 港口帧率问题", source_turn_ids=("t-1",))
        self.assertTrue(result.indexed)
        found = self.svc.recall(person_id=self.a, query="港口场景优化")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].entry.memory_id, result.entry.memory_id)
        self.assertEqual(found[0].entry.source_turn_ids, ("t-1",))
        reread = SQLiteMemoryRepository(self.repo.database)
        self.assertEqual(reread.get(result.entry.memory_id, self.svc.agent_id, self.a).content,
                         "一起排查 OSG 港口帧率问题")

    def test_isolation_of_people_even_if_vector_filter_is_broken(self):
        other = self.svc.remember(person_id=self.b, content="OSG 港口私密项目")
        own = self.svc.remember(person_id=self.a, content="我在做蛋糕")
        self.index.ignore_filter = True
        result = self.svc.recall(person_id=self.a, query="港口场景优化", limit=5)
        self.assertEqual([r.entry.memory_id for r in result], [own.entry.memory_id])
        self.assertNotEqual(other.entry.memory_id, own.entry.memory_id)

    def test_agent_scope_isolation(self):
        foreign = MemoryVectorService(self.repo, self.index, self.embedding, agent_id="other-agent")
        foreign.remember(person_id=self.a, content="港口渲染私密细节")
        self.assertEqual(self.svc.recall(person_id=self.a, query="OSG"), [])

    def test_index_failure_keeps_authoritative_record_and_is_repairable(self):
        self.index.fail_upsert = True
        saved = self.svc.remember(person_id=self.a, content="港口渲染项目")
        self.assertFalse(saved.indexed)
        self.assertIsNotNone(self.repo.get(saved.entry.memory_id, self.svc.agent_id, self.a))
        self.index.fail_upsert = False
        repaired = self.svc.repair_index()
        self.assertEqual((repaired.indexed, repaired.failures), (1, 0))
        self.assertEqual(len(self.svc.recall(person_id=self.a, query="港口")), 1)

    def test_forget_scrubs_text_and_retries_failed_vector_deletion(self):
        saved = self.svc.remember(person_id=self.a, content="OSG 港口项目")
        self.assertFalse(self.svc.forget(person_id=self.b, memory_id=saved.entry.memory_id))
        self.index.fail_delete = True
        self.assertTrue(self.svc.forget(person_id=self.a, memory_id=saved.entry.memory_id))
        self.assertEqual(self.svc.recall(person_id=self.a, query="港口"), [])
        self.assertIn(saved.entry.memory_id, self.repo.deleted_ids())
        self.index.fail_delete = False
        fixed = self.svc.repair_index()
        self.assertEqual(fixed.purged, 1)
        self.assertNotIn(saved.entry.memory_id, self.repo.deleted_ids())
        self.assertNotIn(saved.entry.memory_id, self.index.items)

    def test_embedding_change_requires_reindex(self):
        result = self.svc.remember(person_id=self.a, content="OSG 港口项目")
        new_embedding = FakeEmbedding()
        new_embedding.model_id = "unit-test-3d-v2"
        next_service = MemoryVectorService(self.repo, self.index, new_embedding,
                                           agent_id=self.svc.agent_id)
        self.assertEqual(next_service.recall(person_id=self.a, query="港口"), [])
        self.assertEqual(next_service.repair_index().indexed, 1)
        self.assertEqual(len(next_service.recall(person_id=self.a, query="港口")), 1)
        self.assertEqual(self.repo.get(result.entry.memory_id, self.svc.agent_id, self.a).indexed_model,
                         "unit-test-3d-v2@3")

    def test_reject_blank_identity_wrong_ids_and_bad_vectors(self):
        with self.assertRaises(ValueError):
            self.svc.remember(person_id="", content="abc")
        with self.assertRaises(ValueError):
            self.svc.remember(person_id=self.a, content="abc", memory_id="not-a-uuid")
        with self.assertRaises(ValueError):
            self.svc.recall(person_id=self.a, query="")
        with self.assertRaises(ValueError):
            checked_vector([1.0, 0.0], 3)
        with self.assertRaises(ValueError):
            checked_vector([float("nan"), 0.0, 0.0], 3)
        with self.assertRaises(ValueError):
            checked_vector([0.0, 0.0, 0.0], 3)


if __name__ == "__main__":
    unittest.main()
