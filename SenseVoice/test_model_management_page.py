"""Offscreen check that model allocation uses saved model identifiers."""
import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from model_management_page import ModelDialog, ModelManagementPage
from zhuangfy.llm.model_probe import ModelProbeResult
from zhuangfy.llm.model_registry import ModelRegistry, ProviderConnection, RegisteredModel


class ModelManagementPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_model_id_is_shown_and_assignment_refreshes_after_save(self):
        connection = ProviderConnection("provider_1", "本地服务", "custom",
                                        "http://127.0.0.1:11434/v1")
        model = RegisteredModel("local_chat", "本地聊天模型", connection.id, "qwen-local")
        registry = ModelRegistry((connection,), (model,), (model.id,), (model.id,))
        with patch("model_management_page.load_registry", return_value=registry), \
             patch("model_management_page.persist_registry") as persist:
            page = ModelManagementPage()
            try:
                self.assertEqual(page.table.item(0, 2).text(), "local_chat")
                self.assertEqual(page.table.columnCount(), 5)
                self.assertTrue(page.table.cellWidget(0, 4).findChildren(QPushButton)[0].isEnabled())
                self.assertEqual(page.providers.item(1).text(), "本地服务")
                self.assertFalse(page.stage_temperature.isEnabled())
                page.tabs.setCurrentIndex(1)
                page.stages.setCurrentRow(0)
                self.assertEqual(page.assignment_boxes[0].currentData(), "local_chat")
                page.save_function_assignment()
                persist.assert_called_once_with(registry)
                self.assertEqual(page.registry.reply, ("local_chat",))
            finally:
                page.close()

    def test_test_button_runs_builtin_probe_and_displays_result(self):
        connection = ProviderConnection("provider_1", "本地服务", "custom",
                                        "http://127.0.0.1:11434/v1")
        model = RegisteredModel("local_chat", "本地聊天模型", connection.id, "qwen-local")
        registry = ModelRegistry((connection,), (model,), (model.id,), (model.id,))
        with patch("model_management_page.load_registry", return_value=registry), \
             patch("model_management_page.probe_model",
                   return_value=ModelProbeResult("连接正常", 0.2)) as probe:
            page = ModelManagementPage()
            try:
                button = page.table.cellWidget(0, 4).findChildren(QPushButton)[0]
                button.click()
                deadline = time.monotonic() + 2
                while page._probe_worker is not None and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
                self.assertIsNone(page._probe_worker)
                probe.assert_called_once_with(connection, model)
                self.assertIn("连接正常", page.feedback.text())
            finally:
                page.close()

    def test_thinking_is_selectable_for_supported_provider_only(self):
        providers = (ProviderConnection("deepseek", "DeepSeek", "deepseek",
                                        "https://api.deepseek.com"),
                     ProviderConnection("local", "本地", "custom",
                                        "http://127.0.0.1:11434/v1"))
        models = (RegisteredModel("ds", "DeepSeek", "deepseek", "deepseek-flash"),)
        registry = ModelRegistry(providers, models, ("ds",), ("ds",))
        dialog = ModelDialog(registry, models[0])
        try:
            self.assertTrue(dialog.thinking.isEnabled())
            dialog.thinking.setCurrentIndex(dialog.thinking.findData("disabled"))
            self.assertEqual(dialog.values().thinking, "disabled")
            dialog.provider.setCurrentIndex(dialog.provider.findData("local"))
            self.assertFalse(dialog.thinking.isEnabled())
            self.assertEqual(dialog.values().thinking, "auto")
            self.assertFalse(dialog.web_search.isEnabled())
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
