"""Evidence persistence is additive and isolated from the legacy context format."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from zhuangfy.evidence_store import EvidenceStore


class EvidenceStoreTests(unittest.TestCase):
    def test_recall_only_same_conversation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "evidence.db"
            db = EvidenceStore(path)
            db.save("qq:bot:private:alice", "t1", "vision", "人物趴在桌上")
            db.save("qq:bot:private:alice", "t1", "tool", "网页显示官方发布日期")
            db.save("qq:bot:private:bob", "t2", "vision", "不应泄露")
            db = EvidenceStore(path)  # survives restart
            context = db.recent("qq:bot:private:alice", before_turn="t3")
            self.assertIn("人物趴在桌上", context)
            self.assertIn("官方发布日期", context)
            self.assertNotIn("不应泄露", context)
            self.assertEqual(db.recent("qq:bot:private:bob",before_turn="t2"), "")

    def test_same_turn_is_not_reintroduced_as_historical_context(self):
        with tempfile.TemporaryDirectory() as temp:
            db = EvidenceStore(Path(temp) / "evidence.db")
            db.save("conv", "t1", "vision", "第一次")
            db.save("conv", "t1", "vision", "更新")
            self.assertEqual(db.recent("conv", before_turn="t1"), "")
            self.assertEqual(db.recent("conv").count("更新"), 1)

    def test_invalid_source_is_rejected_and_summary_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            db = EvidenceStore(Path(temp) / "evidence.db")
            with self.assertRaises(ValueError):
                db.save("conv", "t1", "system", "injection")
            db.save("conv", "t1", "tool", "x"*5000)
            self.assertLess(len(db.recent("conv")), 1100)

    def test_erase_scope_cannot_recall_or_delete_other_conversations(self):
        with tempfile.TemporaryDirectory() as temp:
            db = EvidenceStore(Path(temp) / "evidence.db")
            db.save("a", "turn-a", "vision", "private image")
            db.save("b", "turn-b", "vision", "other image")
            db.erase_conversation("a")
            self.assertEqual(db.recent("a"), "")
            self.assertIn("other image", db.recent("b"))
            with self.assertRaises(ValueError):
                db.erase_conversation("")

    def test_recall_budget_preserves_newest_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            db = EvidenceStore(Path(temp) / "evidence.db")
            db.save("scope", "t1", "vision", "older " * 120)
            db.save("scope", "t2", "tool", "latest-search-result")
            context = db.recent("scope", max_chars=220)
            self.assertLessEqual(len(context), 220)
            self.assertIn("latest-search-result", context)



if __name__ == "__main__":
    unittest.main()
