"""No-network checks for privileged R1 admin CLI argument validation."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class RelationshipAdminCliTests(unittest.TestCase):
    def test_bounded_assignment_parser(self):
        spec = importlib.util.spec_from_file_location("relationship_admin_cli",
            ROOT / "scripts/relationship_admin.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        self.assertEqual(script.parse_change("familiarity=42"), ("familiarity", 42))
        self.assertEqual(script.parse_change("frozen=true"), ("frozen", True))
        self.assertEqual(script.parse_change("allow_affection=false"), ("allow_affection", False))
        for text in ("admin_token=oops", "familiarity=-1", "frozen=yes",
                     "familiarity=notanumber", "tier_ceiling="):
            with self.subTest(text=text), self.assertRaises(ValueError):
                script.parse_change(text)


if __name__ == "__main__":
    unittest.main()
