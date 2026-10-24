"""Offscreen smoke test for editable dashboard size preferences."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import dashboard
from zhuangfy.ui.window_preferences import WindowPreferences


class DashboardWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_preset_applies_immediately_and_custom_resize_persists(self):
        with patch.object(dashboard, "load_window_preferences",
                          return_value=WindowPreferences()):
            window = dashboard.Dashboard()
        try:
            self.assertEqual(window.window_preferences, WindowPreferences(1600, 900))
            self.assertEqual(window.window_size_preset.itemText(0), "自定义大小")
            self.assertEqual(window.window_size_preset.itemText(1), "1920 × 1080")
            index = window._window_preset_index((1366, 768))
            self.assertGreaterEqual(index, 0)
            with patch.object(dashboard, "save_window_preferences") as save:
                window.window_size_preset.setCurrentIndex(index)
            save.assert_called_once_with(WindowPreferences(1366, 768))
            self.assertEqual(window.window_preferences, WindowPreferences(1366, 768))
            window.show()
            self.app.processEvents()
            window.enable_window_resize_tracking()
            window.resize(1500, 800)
            self.app.processEvents()
            self.assertEqual(window.window_size_preset.currentText(), "自定义大小")
            with patch.object(dashboard, "save_window_preferences") as save:
                window.save_custom_window_size()
            save.assert_called_once_with(WindowPreferences(1500, 800))
            self.assertEqual(window.window_preferences, WindowPreferences(1500, 800))
        finally:
            window._really_quit = True
            window.close()


if __name__ == "__main__":
    unittest.main()
