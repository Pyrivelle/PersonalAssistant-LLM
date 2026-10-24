"""The global image-description cache must not leak or over-delete scoped links."""
import tempfile
import unittest
from pathlib import Path

from zhuangfy.vision_context import VisionDescriptionStore


class VisionScopeErasureTests(unittest.TestCase):
    def test_erase_one_conversation_preserves_shared_other_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            store = VisionDescriptionStore(Path(folder) / "vision.db")
            shared = "a" * 64
            unique = "b" * 64
            store.store("qq:bot:private:alice:m1:0", shared, "sig", "shared pic")
            store.store("qq:bot:private:bob:m2:0", shared, "sig", "shared pic")
            store.store("qq:bot:private:alice:m3:0", unique, "sig", "private pic")
            store.erase_conversation("qq:bot:private:alice:")
            self.assertIsNone(store.refresh("qq:bot:private:alice:m1:0", "sig"))
            self.assertIsNone(store.refresh("qq:bot:private:alice:m3:0", "sig"))
            self.assertEqual(store.lookup(unique, "sig"), "")
            self.assertEqual(store.lookup(shared, "sig"), "shared pic")
            self.assertIsNotNone(store.refresh("qq:bot:private:bob:m2:0", "sig"))
            with self.assertRaises(ValueError):
                store.erase_conversation("qq:bot:private:alice")


if __name__ == "__main__":
    unittest.main()
