import tempfile
import unittest
from pathlib import Path
from zhuangfy.image_meaning import ImageMeaningStore, explicit_image_meaning


class ImageMeaningTests(unittest.TestCase):
    def test_explicit_clarification_and_non_clarifications(self):
        self.assertEqual(explicit_image_meaning("其实是在表达好感，我喜欢你的意思"),
                         "好感，我喜欢你的意思")
        self.assertEqual(explicit_image_meaning("我发那张图是想告诉你我喜欢你"),
                         "告诉你我喜欢你")
        for content in ("给你看看这张图", "你觉得这个是表达好感吗？",
                        "比如说其实是在表达喜欢？", "我喜欢你"):
            self.assertEqual(explicit_image_meaning(content), "")

    def test_same_hash_only_in_own_conversation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "meanings.db"
            digest = "a" * 64
            db = ImageMeaningStore(path)
            db.save("qq:bot:private:alice", digest, "向你表达喜欢", "msg2")
            self.assertEqual(ImageMeaningStore(path).lookup(
                "qq:bot:private:alice", digest), "向你表达喜欢")
            self.assertEqual(db.lookup("qq:bot:private:bob", digest), "")
            self.assertEqual(db.lookup("qq:bot:private:alice", "b"*64), "")


if __name__ == "__main__":
    unittest.main()
