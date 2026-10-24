"""Storage migration keeps identities and secrets intact when paths change."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from zhuangfy.paths import Paths
from zhuangfy.storage import estimated_bytes, migrate, validate_target


class StorageMigrationTests(unittest.TestCase):
    def test_migration_copies_both_data_roots_and_switches_only_after_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            legacy = project / "SenseVoice"
            (legacy / "models").mkdir(parents=True)
            (project / "data" / "memory_core").mkdir(parents=True)
            (legacy / ".env.local").write_bytes(b"PRIVATE_KEY=secret\n")
            (legacy / "qq_settings.json").write_text('{"allowed_private_ids":[42]}')
            (legacy / "models" / "weights.onnx").write_bytes(b"model-bytes")
            database = project / "data" / "memory_core" / "memory.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                with connection:
                    connection.execute("CREATE TABLE people (person_id TEXT PRIMARY KEY)")
                    connection.execute("INSERT INTO people VALUES ('stable-person-id')")
            target = base / "chosen"
            target.mkdir()
            (legacy / "program.py").write_bytes(b"program-code-not-migrated")
            self.assertEqual(estimated_bytes(project, target),
                             len(b"PRIVATE_KEY=secret\n")
                             + len(b'{"allowed_private_ids":[42]}')
                             + len(b"model-bytes") + database.stat().st_size)

            migrate(project, target)

            self.assertEqual(Paths(project, {}).data_root, target / "legacy")
            self.assertEqual(Paths(project, {}).project_data_root, target / "data")
            self.assertEqual((target / "legacy" / ".env.local").read_bytes(), b"PRIVATE_KEY=secret\n")
            self.assertEqual((target / "legacy" / "models" / "weights.onnx").read_bytes(), b"model-bytes")
            with closing(sqlite3.connect(target / "data" / "memory_core" / "memory.sqlite3")) as connection:
                self.assertEqual(connection.execute("SELECT person_id FROM people").fetchone()[0],
                                 "stable-person-id")
            self.assertEqual(json.loads((project / "storage_location.json").read_text())["path"], str(target))
            self.assertTrue((project / "data" / "memory_core" / "memory.sqlite3").exists())
            self.assertFalse((target / "legacy" / "program.py").exists())

    def test_nonempty_or_nested_target_is_rejected_without_switching(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            (project / "SenseVoice").mkdir(parents=True)
            (project / "data").mkdir()
            with self.assertRaises(ValueError):
                validate_target(project, project / "data" / "nested")
            destination = Path(temporary) / "chosen"
            destination.mkdir()
            (destination / "existing.txt").write_text("do not overwrite")
            with self.assertRaises(ValueError):
                migrate(project, destination)
            self.assertFalse((project / "storage_location.json").exists())
            self.assertEqual((destination / "existing.txt").read_text(), "do not overwrite")


if __name__ == "__main__":
    unittest.main()
