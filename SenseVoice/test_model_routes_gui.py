"""Qt offscreen smoke tests for the user-facing API route editor."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import model_routes_gui
from model_routes import ModelRoutes
from turn_planner import PlannerSettings


class RouteGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_add_reorder_remove_and_read_policy(self):
        with patch.object(model_routes_gui, "load_routes", return_value=ModelRoutes()), \
             patch.object(model_routes_gui.PlannerSettings, "load", return_value=PlannerSettings()):
            dialog = model_routes_gui.ModelRoutesDialog()
        panel = dialog.editors["reply"]
        self.assertEqual(len(panel.rows), 0)
        panel.add_candidate()
        panel.add_candidate()
        self.assertEqual([row.route_id for row in panel.rows], ["reply_1", "reply_2"])
        panel.rows[0].model.setCurrentText("primary-model")
        panel.rows[1].model.setCurrentText("backup-model")
        panel.move_candidate(panel.rows[1], -1)
        self.assertEqual([item.model for item in panel.policy().candidates],
                         ["backup-model", "primary-model"])
        panel.remove_candidate(panel.rows[-1])
        self.assertEqual(len(panel.policy().candidates), 1)
        dialog.reject()

    def test_provider_model_choices_and_fetched_list(self):
        with patch.object(model_routes_gui, "load_routes", return_value=ModelRoutes()), \
             patch.object(model_routes_gui.PlannerSettings, "load", return_value=PlannerSettings()):
            dialog = model_routes_gui.ModelRoutesDialog()
        panel = dialog.editors["reply"]
        panel.add_candidate()
        row = panel.rows[0]
        row.provider.setCurrentIndex(row.provider.findData("deepseek"))
        self.assertIn("deepseek-flash", [row.model.itemText(i) for i in range(row.model.count())])
        self.assertEqual(row.model.currentText(), "deepseek-flash")
        row._models_loaded("deepseek", row.base_url.text(), ["deepseek-chat", "deepseek-reasoner"])
        self.assertIn("deepseek-reasoner", [row.model.itemText(i) for i in range(row.model.count())])
        row.model.setCurrentText("deepseek-reasoner")
        self.assertEqual(panel.policy().candidates[0].model, "deepseek-reasoner")
        dialog.reject()

    def test_settings_save_preserves_old_cognition_options_and_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "cognition_settings.json"
            file.write_text(json.dumps({"enabled": False, "memory_private_ids": [42]}), encoding="utf-8")
            with patch.object(model_routes_gui, "ROOT", root), \
                 patch.object(model_routes_gui, "load_routes", return_value=ModelRoutes()), \
                 patch.object(model_routes_gui.PlannerSettings, "load", return_value=PlannerSettings()), \
                 patch.object(model_routes_gui, "save_routes") as store, \
                 patch.object(model_routes_gui, "save_route_key") as save_key, \
                 patch.object(model_routes_gui.QMessageBox, "information"):
                dialog = model_routes_gui.ModelRoutesDialog()
                dialog.planner_enabled.setChecked(True)
                dialog.editors["reply"].add_candidate()
                dialog.editors["reply"].rows[0].key_edit.setText("new-secret")
                dialog.save()
            self.assertEqual(json.loads(file.read_text(encoding="utf-8")),
                             {"enabled": True, "memory_private_ids": [42]})
            self.assertTrue(store.called)
            save_key.assert_called_once_with("reply_1", "new-secret",
                                             "siliconflow", "https://api.siliconflow.cn/v1")


if __name__ == "__main__":
    unittest.main()
