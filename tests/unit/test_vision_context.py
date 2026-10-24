"""Offline tests for durable, message-bound vision evidence."""
import base64
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from zhuangfy.vision_context import VisionDescriptionStore, image_digest


class VisionContextTests(unittest.TestCase):
    def test_hash_uses_image_bytes_and_validates_input(self):
        data = "data:image/png;base64," + base64.b64encode(b"example image bytes").decode()
        self.assertEqual(image_digest(data), image_digest(data))
        with self.assertRaises(ValueError):
            image_digest("https://example.org/picture.png")
        with self.assertRaises(ValueError):
            image_digest("data:image/png;base64,not valid !!!")

    def test_cached_description_and_message_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = VisionDescriptionStore(Path(tmp) / "vision.sqlite3")
            digest = "f" * 64
            db.associate("qq:msg:1", digest)
            initial = db.refresh("qq:msg:1", "vision-v1")
            self.assertEqual(initial.status, "pending")
            self.assertEqual(initial.description, "")
            db.store("qq:msg:2", digest, "vision-v1", "玫瑰旁边有动漫角色")
            self.assertEqual(db.lookup(digest, "vision-v1"), "玫瑰旁边有动漫角色")
            self.assertEqual(db.lookup(digest, "vision-v2"), "")
            refreshed = db.refresh("qq:msg:1", "vision-v1")
            self.assertEqual(refreshed.status, "completed")
            self.assertIn("玫瑰", refreshed.description)
            self.assertEqual(db.refresh("qq:msg:2", "vision-v1").status, "completed")


    def test_model_versions_and_chat_scopes_are_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = VisionDescriptionStore(Path(tmp) / "cache.sqlite3")
            digest = "a" * 64
            db.store("qq:1:private:2:evt:0", digest, "model-A", "A描述")
            db.store("qq:1:private:3:evt:0", digest, "model-B", "B描述")
            self.assertEqual(db.lookup(digest, "model-A"), "A描述")
            self.assertEqual(db.lookup(digest, "model-B"), "B描述")
            self.assertEqual(len(db.recent("qq:1:private:2:", "model-A")), 1)
            self.assertEqual(db.recent("qq:1:private:3:", "model-A"), [])
            self.assertEqual(db.recent("qq:1:private:2:", "model-B"), [])

    def test_failed_then_retried_image_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = VisionDescriptionStore(Path(tmp) / "cache.sqlite3")
            digest = "b" * 64
            db.mark_failed("chat:m1", digest, "signature")
            self.assertEqual(db.refresh("chat:m1", "signature").status, "failed")
            db.store("chat:m1", digest, "signature", "可见物体")
            self.assertEqual(db.refresh("chat:m1", "signature").status, "completed")

    def test_legacy_schema_migrates_without_dropping_latest_description(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.sqlite3"
            with closing(sqlite3.connect(path)) as con, con:
                con.execute("""CREATE TABLE descriptions(
                    image_hash TEXT PRIMARY KEY, description TEXT NOT NULL,
                    model_signature TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
                con.execute("INSERT INTO descriptions(image_hash,description,model_signature) VALUES (?,?,?)",
                            ("c"*64, "旧描述", "old"))
                con.execute("""CREATE TABLE message_images(
                    message_key TEXT PRIMARY KEY, image_hash TEXT NOT NULL, status TEXT NOT NULL)""")
            db = VisionDescriptionStore(path)
            self.assertEqual(db.lookup("c"*64, "old"), "旧描述")
            db.store("m2", "c"*64, "new", "新描述")
            self.assertEqual(db.lookup("c"*64, "new"), "新描述")
            self.assertEqual(db.lookup("c"*64, "old"), "旧描述")


if __name__ == "__main__":
    unittest.main()
