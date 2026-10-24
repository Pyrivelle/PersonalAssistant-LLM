"""Contract tests for the delivered-turn evidence commit path."""
import tempfile
import unittest
from pathlib import Path

from zhuangfy.turn_evidence import commit_delivered_evidence
from zhuangfy.evidence_store import EvidenceStore
from zhuangfy.image_meaning import ImageMeaningStore


class DeliveredEvidenceTests(unittest.TestCase):
    def test_stored_evidence_and_meaning_survive_reopen_in_scope_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            digest = "a" * 64
            commit_delivered_evidence(
                root, conversation_id="qq:1:private:alice", sender_id="alice",
                turn_id="t1", image_hash=digest, image_message_id="m1",
                visual_summary="一张玫瑰表情包",
            )
            images = ImageMeaningStore(root / "image_meanings.sqlite3")
            self.assertEqual(images.latest_unambiguous(
                "qq:1:private:alice", "alice"), digest)
            commit_delivered_evidence(
                root, conversation_id="qq:1:private:alice", sender_id="alice",
                turn_id="t2", meaning_hash=digest,
                meaning_text="向你表达喜欢", meaning_source_message_id="m2",
                tool_summary="检索返回资料",
            )
            self.assertEqual(ImageMeaningStore(root / "image_meanings.sqlite3").lookup(
                "qq:1:private:alice", digest), "向你表达喜欢")
            self.assertEqual(images.lookup("qq:1:private:bob", digest), "")
            context = EvidenceStore(root / "context.sqlite3").recent("qq:1:private:alice")
            self.assertIn("玫瑰", context)
            self.assertIn("检索返回资料", context)
            self.assertEqual(EvidenceStore(root / "context.sqlite3").recent(
                "qq:1:private:bob"), "")

    def test_missing_provenance_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                commit_delivered_evidence(
                    Path(temp), conversation_id="qq:1:private:alice",
                    sender_id="alice", turn_id="t1", image_hash="b"*64)
            with self.assertRaises(ValueError):
                commit_delivered_evidence(
                    Path(temp), conversation_id="qq:1:private:alice",
                    sender_id="alice", turn_id="t2", meaning_hash="a"*64)

    def test_invalid_image_provenance_cannot_half_commit_tool_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                commit_delivered_evidence(
                    root, conversation_id="conv", sender_id="alice", turn_id="t1",
                    tool_summary="should-not-persist",
                    image_hash="not-a-sha256", image_message_id="m1",
                )
            self.assertFalse((root / "context.sqlite3").exists())

    def test_erase_all_derivative_stores_after_delivery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            digest = "c" * 64
            scope = "qq:1:private:alice"
            commit_delivered_evidence(
                root, conversation_id=scope, sender_id="alice", turn_id="t1",
                image_hash=digest, image_message_id="m1",
                visual_summary="图片", meaning_hash=digest,
                meaning_text="表达好感", meaning_source_message_id="m2")
            evidence = EvidenceStore(root / "context.sqlite3")
            meanings = ImageMeaningStore(root / "image_meanings.sqlite3")
            evidence.erase_conversation(scope)
            meanings.erase_conversation(scope)
            self.assertEqual(evidence.recent(scope), "")
            self.assertEqual(meanings.lookup(scope, digest), "")
            self.assertEqual(meanings.latest_unambiguous(scope, "alice"), "")


if __name__ == "__main__":
    unittest.main()
