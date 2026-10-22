"""Local Qdrant adapter integration; skipped when optional package absent."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from zhuangfy.memory import QdrantLocalIndex

try:
    import qdrant_client  # noqa: F401
except ImportError:
    QDRANT_INSTALLED = False
else:
    QDRANT_INSTALLED = True


@unittest.skipUnless(QDRANT_INSTALLED, "qdrant-client optional dependency not installed")
class QdrantLocalIndexTests(unittest.TestCase):
    def test_persist_search_filter_delete_and_model_namespacing(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = str(uuid4()), str(uuid4())
            index = QdrantLocalIndex(Path(directory), model_id="fake-3d-v1", dimension=3)
            self.assertNotIn("shimano", index.collection)
            id_a, id_b = str(uuid4()), str(uuid4())
            index.upsert(id_a, "agent-1", a, [1, 0, 0])
            index.upsert(id_b, "agent-1", b, [1, 0, 0])
            self.assertEqual([x.memory_id for x in index.search("agent-1", a, [1, 0, 0], 5)], [id_a])
            self.assertEqual(index.search("agent-2", a, [1, 0, 0], 5), [])
            index.close()
            index = QdrantLocalIndex(Path(directory), model_id="fake-3d-v1", dimension=3)
            self.assertEqual([x.memory_id for x in index.search("agent-1", a, [1, 0, 0], 5)], [id_a])
            index.delete(id_a)
            self.assertEqual(index.search("agent-1", a, [1, 0, 0], 5), [])
            index.close()
            different = QdrantLocalIndex(Path(directory), model_id="fake-3d-v2", dimension=3)
            self.assertEqual(different.search("agent-1", b, [1, 0, 0], 5), [])
            different.close()


if __name__ == "__main__":
    unittest.main()
