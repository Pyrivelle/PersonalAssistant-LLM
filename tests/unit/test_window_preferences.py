import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from zhuangfy.ui.window_preferences import (
    WindowPreferences, fit_to_screen, load_preferences, save_preferences,
)


class WindowPreferencesTests(unittest.TestCase):
    def test_default_and_persistence(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "dashboard_settings.json"
            self.assertEqual(load_preferences(path), WindowPreferences(1600, 900))
            save_preferences(WindowPreferences(1920, 1080), path)
            self.assertEqual(load_preferences(path), WindowPreferences(1920, 1080))

    def test_invalid_or_unreadable_file_falls_back(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "dashboard_settings.json"
            path.write_text(json.dumps({"width": True, "height": 900}), encoding="utf-8")
            self.assertEqual(load_preferences(path), WindowPreferences())
            with self.assertRaises(ValueError):
                save_preferences(WindowPreferences(800, 400), path)

    def test_smaller_screen_clamps_display_without_mutating_saved_size(self):
        preference = WindowPreferences(1920, 1080)
        self.assertEqual(fit_to_screen(preference, 1600, 850), (1600, 850))
        self.assertEqual(preference, WindowPreferences(1920, 1080))


if __name__ == "__main__":
    unittest.main()
