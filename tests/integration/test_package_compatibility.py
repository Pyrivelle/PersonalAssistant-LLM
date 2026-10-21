"""Old and new imports refer to the same stateful module instances."""

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "SenseVoice"), str(ROOT / "src")]


class PackageCompatibilityTests(unittest.TestCase):
    def test_legacy_activity_stream_is_canonical_stream(self):
        old = importlib.import_module("activity_log")
        new = importlib.import_module("zhuangfy.observability.activity_log")
        self.assertIs(old, new)

    def test_legacy_persona_registry_uses_packaged_resources(self):
        old = importlib.import_module("persona_packs")
        new = importlib.import_module("zhuangfy.persona.packs")
        self.assertIs(old, new)
        self.assertIn("庄方宜", old.ZHUANG_FANGYI_V1.prompt())
        self.assertEqual(old.PACK_ROOT, ROOT / "resources" / "personas")


if __name__ == "__main__":
    unittest.main()
