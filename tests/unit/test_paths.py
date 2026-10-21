"""Path policy remains predictable before any user data is moved."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from zhuangfy.paths import Paths


class PathsTests(unittest.TestCase):
    def test_existing_runtime_data_stays_in_legacy_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "SenseVoice").mkdir()
            paths = Paths(project, {})
            self.assertEqual(paths.config_file("qq_settings.json"), project / "SenseVoice" / "qq_settings.json")
            self.assertEqual(paths.memory_root, project / "SenseVoice" / "memory_data")
            self.assertEqual(paths.models_root, project / "SenseVoice" / "models")
            self.assertEqual(paths.gpt_sovits_root, project.parent / "GPT-SoVITS")

    def test_new_checkout_uses_data_directory_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertEqual(Paths(project, {}).data_root, project / "data")
            custom = project / "private-data"
            self.assertEqual(Paths(project, {"ZHUANGFY_DATA_DIR": str(custom)}).data_root, custom)

    def test_resource_paths_prefer_new_layout_and_reject_config_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "resources" / "personas").mkdir(parents=True)
            (project / "resources" / "images").mkdir(parents=True)
            paths = Paths(project, {})
            self.assertEqual(paths.persona_root, project / "resources" / "personas")
            self.assertEqual(paths.images_root, project / "resources" / "images")
            with self.assertRaises(ValueError):
                paths.config_file("../outside.json")

    def test_snowluma_versioned_deployment_and_explicit_engine_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            project = parent / "ZhuangFY"
            project.mkdir()
            deployment = parent / "SnowLuma" / "v2.0.3"
            deployment.mkdir(parents=True)
            (deployment / "index.mjs").write_text("", encoding="utf-8")
            self.assertEqual(Paths(project, {}).snowluma_root, deployment)
            custom = parent / "engines" / "GPT-SoVITS"
            paths = Paths(project, {"ZHUANGFY_GPT_SOVITS_DIR": str(custom)})
            self.assertEqual(paths.gpt_sovits_root, custom)


if __name__ == "__main__":
    unittest.main()
