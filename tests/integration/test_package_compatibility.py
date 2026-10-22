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

    def test_legacy_speech_imports_use_packaged_modules(self):
        for old_name, new_name in (
            ("recognize", "zhuangfy.speech.sensevoice"),
            ("continuous", "zhuangfy.speech.vad"),
            ("interaction", "zhuangfy.speech.interaction"),
        ):
            with self.subTest(old_name=old_name):
                self.assertIs(importlib.import_module(old_name), importlib.import_module(new_name))

        self.assertEqual(importlib.import_module("recognize").ROOT, ROOT / "SenseVoice")

    def test_legacy_services_use_packaged_modules(self):
        for old_name, new_name in (
            ("persona", "zhuangfy.persona.defaults"),
            ("llm", "zhuangfy.llm.providers"),
            ("model_routes", "zhuangfy.llm.routes"),
            ("gpt_sovits", "zhuangfy.speech.gpt_sovits"),
        ):
            with self.subTest(old_name=old_name):
                self.assertIs(importlib.import_module(old_name), importlib.import_module(new_name))

        self.assertEqual(importlib.import_module("llm").ROOT, ROOT / "SenseVoice")


if __name__ == "__main__":
    unittest.main()
