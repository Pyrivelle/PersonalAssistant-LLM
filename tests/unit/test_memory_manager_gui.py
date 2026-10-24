"""M5 Qt smoke test: GUI loads without network or database access."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    from zhuangfy.ui.memory_manager import CuratedMemoryDialog
    from zhuangfy.memory.client import MemoryClient
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class MemoryManagerGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_account_roster_and_curator_approval_controls(self):
        with patch.object(MemoryClient, "from_environment", return_value=None):
            win = CuratedMemoryDialog()
            win.client = object()  # trusted admin test stub; no network
            self.assertEqual(win.tabs.count(), 3)
            person_id = "person-001"
            win.people = [{
                "person_id": person_id, "display_name": "梨愿",
                "identities": [{"platform": "qq", "channel_account_id": "100",
                                "external_user_id": "200"}],
                "timeline_enabled": True, "timeline_revoked": False,
                "curation_enabled": False, "message_count": 6,
                "memory_count": 0, "jobs": {"pending": 0, "running": 0,
                                            "done": 0, "failed": 0},
            }]
            win.person.blockSignals(True)
            win.person.addItem("梨愿", person_id)
            win.person.blockSignals(False)
            win._render_people()
            self.assertEqual(win.account_table.rowCount(), 1)
            self.assertIn("qq:100:200", win.account_table.item(0, 1).text())
            self.assertIn("6", win.person_summary.text())
            self.assertTrue(win.approve_curator_button.isEnabled())
            self.assertFalse(win.revoke_curator_button.isEnabled())
            with patch.object(win, "_confirm", return_value=True), \
                 patch.object(win, "_start") as request:
                win.approve_curator()
                request.assert_called_once_with("admin_consent", person_id,
                                                "curation", True)
            win.people[0]["curation_enabled"] = True
            win._update_person_status()
            self.assertFalse(win.approve_curator_button.isEnabled())
            self.assertTrue(win.revoke_curator_button.isEnabled())
            with patch.object(win, "_confirm", return_value=True), \
                 patch.object(win, "_start") as request:
                win.revoke_curator()
                request.assert_called_once_with("admin_consent", person_id,
                                                "curation", False)
            with patch("zhuangfy.ui.memory_manager.QInputDialog.getText",
                       return_value=("新名称", True)), \
                 patch.object(win, "_start") as request:
                win.rename_person()
                request.assert_called_once_with("admin_rename_person", person_id,
                                                "新名称")
            win.client = None
            win.close()

    def test_new_memory_manager_requires_explicit_admin_ui_opt_in(self):
        with patch.object(MemoryClient, "from_environment", return_value=None):
            win = CuratedMemoryDialog()
            self.assertIn("未启用", win.notice.text())
            self.assertEqual(win.person.count(), 0)
            self.assertTrue(win.windowTitle().startswith("ZhuangFY"))
            win.close()


if __name__ == "__main__":
    unittest.main()
