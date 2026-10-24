"""Offline tests for durable, message-bound vision evidence."""
import base64
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
