"""Local installation identity stays stable and never implies a QQ link."""
import tempfile
import unittest
from pathlib import Path

from zhuangfy.agent_v2.desktop_identity import installation_id
from zhuangfy.agent_v2.skill_access import SkillPrincipal


class DesktopIdentityTests(unittest.TestCase):
    def test_persisted_installation_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "identity.json"
            first = installation_id(path)
            self.assertEqual(installation_id(path), first)
            self.assertEqual(SkillPrincipal.local_desktop(
                installation_id=first).key,
                f"desktop:local:private:{first}")
            self.assertEqual(len(first), 32)
