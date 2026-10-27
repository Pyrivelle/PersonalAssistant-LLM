"""M5 Qt smoke test: GUI loads without network or database access."""

import os
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    from zhuangfy.ui.memory_manager import AdminTask, CuratedMemoryDialog, tier_correction
    from zhuangfy.memory.client import MemoryChannel
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

    def test_relation_tier_is_shown_and_saved_for_selected_verified_identity(self):
        with patch.object(MemoryClient, "from_environment", return_value=None):
            win = CuratedMemoryDialog()
            win.client = object()
            person_id = "person-001"
            win.people = [{"person_id": person_id, "display_name": "测试人物",
                           "identities": [{"platform": "qq", "channel_account_id": "100",
                                           "external_user_id": "200"}]}]
            win.person.blockSignals(True)
            win.person.addItem("测试人物", person_id)
            win.person.blockSignals(False)
            win.relationship_state = {"enrolled": True, "enabled": True, "profile": {
                "tier": "L2", "version": 4,
                "auto_growth": True, "tier_ceiling": "L3"}}
            win._render_relationship()
            self.assertIn("L2 · v4", win.relationship_status.text())
            self.assertTrue(win.save_relationship_button.isEnabled())
            win.relationship_tier.setCurrentText("L4")
            with patch.object(win, "_confirm", return_value=True), \
                 patch.object(win, "_start") as request:
                win.save_relationship_tier()
                args = request.call_args.args
            self.assertEqual(args[0], "relationship_correct")
            self.assertEqual(args[1].external_user_id, "200")
            self.assertEqual(args[2], 4)
            self.assertEqual(args[3], tier_correction("L4"))
            self.assertEqual(args[3]["tier_ceiling"], "L4")
            self.assertNotIn("auto_growth", args[3])
            win.relationship_state["enabled"] = False
            win._render_relationship()
            self.assertIn("已停用", win.relationship_status.text())
            win.client = None
            win.close()

    def test_new_person_enrollment_requires_selected_verified_identity(self):
        with patch.object(MemoryClient, "from_environment", return_value=None):
            win = CuratedMemoryDialog()
            win.client = object()
            win.people = [{"person_id": "person-001", "display_name": "测试人物",
                           "identities": [{"platform": "qq", "channel_account_id": "100",
                                           "external_user_id": "200"}]}]
            win.person.blockSignals(True)
            win.person.addItem("测试人物", "person-001")
            win.person.blockSignals(False)
            win.relationship_state = {"enrolled": False}
            win.relationship_tier.setCurrentText("L0")
            with patch.object(win, "_confirm", return_value=True), \
                 patch.object(win, "_start") as request:
                win.save_relationship_tier()
                request.assert_called_once()
                self.assertEqual(request.call_args.args[0], "relationship_enroll")
                self.assertEqual(request.call_args.args[2:], ("L0", False))
            win.client = None
            win.close()

    def test_admin_worker_passes_keyword_only_relationship_fields(self):
        client = Mock()
        identity = MemoryChannel("qq", "100", "200")
        task = AdminTask(client, "relationship_correct",
                         (identity, 7, tier_correction("L3")), None)
        task.run()
        client.relationship_correct.assert_called_once_with(
            identity, expected_version=7, changes=tier_correction("L3"))

    def test_account_page_loads_real_tier_through_background_queue(self):
        client = Mock()
        client.list_people.return_value = [{
            "person_id": "person-001", "display_name": "测试人物",
            "identities": [{"platform": "qq", "channel_account_id": "100",
                            "external_user_id": "200"}],
            "timeline_enabled": True, "jobs": {},
        }]
        client.list_memories.return_value = []
        client.list_jobs.return_value = []
        client.relationship_get.return_value = {"enrolled": True, "enabled": True, "profile": {
            "tier": "L4", "version": 3,
            "auto_growth": False, "tier_ceiling": "L4"}}
        with patch.object(MemoryClient, "from_environment", return_value=client):
            win = CuratedMemoryDialog()
        deadline = time.monotonic() + 3
        while win.task is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertIsNone(win.task)
        self.assertIn("L4 · v3", win.relationship_status.text())
        client.relationship_get.assert_called()
        win.close()


if __name__ == "__main__":
    unittest.main()
