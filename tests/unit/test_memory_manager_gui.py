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

    def test_new_memory_manager_requires_explicit_admin_ui_opt_in(self):
        with patch.object(MemoryClient, "from_environment", return_value=None):
            win = CuratedMemoryDialog()
            self.assertIn("未启用", win.notice.text())
            self.assertEqual(win.person.count(), 0)
            self.assertTrue(win.windowTitle().startswith("ZhuangFY"))
            win.close()


if __name__ == "__main__":
    unittest.main()
