"""Runtime console events and restart wiring without launching services."""

import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QProcess, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from dashboard import Dashboard, PAGE_CHAT, PAGE_STREAM, PAGE_MONITOR, PAGE_MODEL, PAGE_PLATFORM, PAGE_PLUGINS, _tts_process_environment, _tts_python
from model_management_page import ModelManagementPage


class DashboardRuntimeTests(unittest.TestCase):
    def test_tts_child_does_not_inherit_desktop_python_environment(self):
        with patch.dict(os.environ, {"PYTHONHOME": "wrong-python", "PYTHONPATH": "wrong-path",
                                     "VIRTUAL_ENV": "wrong-venv"}):
            environment = _tts_process_environment()
        for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE",
                     "__PYVENV_LAUNCHER__", "VIRTUAL_ENV"):
            self.assertFalse(environment.contains(name))

    def test_tts_uses_real_python_inside_its_own_venv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "base"
            home.mkdir()
            (home / "python.exe").write_bytes(b"base executable")
            venv = root / ".venv"
            (venv / "Scripts").mkdir(parents=True)
            (venv / "pyvenv.cfg").write_text(f"home = {home}\n", encoding="utf-8")
            with patch("dashboard.ENGINE_ROOT", root):
                interpreter, base_home = _tts_python()
            self.assertEqual(interpreter.read_bytes(), b"base executable")
            self.assertEqual(interpreter.parent.name.lower(), "scripts")
            environment = _tts_process_environment(base_home)
            self.assertEqual(environment.value("PATH").split(os.pathsep)[0], str(base_home))

    @classmethod
    def setUpClass(cls):
        cls.local_data = tempfile.TemporaryDirectory()
        cls.old_data_dir = os.environ.get("ZHUANGFY_DATA_DIR")
        os.environ["ZHUANGFY_DATA_DIR"] = cls.local_data.name
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        if cls.old_data_dir is None:
            os.environ.pop("ZHUANGFY_DATA_DIR", None)
        else:
            os.environ["ZHUANGFY_DATA_DIR"] = cls.old_data_dir
        cls.local_data.cleanup()

    def test_dashboard_navigation_and_embedded_chat(self):
        window = Dashboard()
        try:
            self.assertEqual([button.text() for button in window.nav], [
                "首页", "聊天", "聊天流", "记忆中心", "角色设置", "模型管理",
                "平台连接", "插件管理", "运行监控", "系统设置"])
            window.switch_page(PAGE_CHAT)
            self.assertIs(window.pages.currentWidget(), window.chat_page)
            self.assertIsNotNone(window.chat_page.chat_id)
            window.switch_page(PAGE_STREAM)
            self.assertIs(window.pages.currentWidget(), window.chat_stream)
        finally:
            window.quit_app()

    def test_model_management_page_is_embedded(self):
        window = Dashboard()
        try:
            window.switch_page(PAGE_MODEL)
            self.assertIsInstance(window.pages.currentWidget(), ModelManagementPage)
            self.assertEqual(window.pages.currentWidget().tabs.tabText(0), "模型设置")
            self.assertEqual(window.pages.currentWidget().tabs.tabText(1), "功能分配")
        finally:
            window.quit_app()

    def test_tts_ready_is_logged_once_per_online_transition(self):
        window = Dashboard()
        try:
            window._tts_ready_logged = False
            with patch.object(window.tts_server, "state", return_value=QProcess.ProcessState.Running), \
                    patch.object(window, "_log") as log:
                window._update_tts_readiness(False)
                window._update_tts_readiness(True)
                window._update_tts_readiness(True)
                self.assertEqual(log.call_count, 1)
                self.assertIn("已启动", log.call_args.args[0])
                window._update_tts_readiness(False)
                window._update_tts_readiness(True)
                self.assertEqual(log.call_count, 2)
        finally:
            window.quit_app()

    def test_plugin_cards_have_read_only_switches_and_status_lights(self):
        window = Dashboard()
        try:
            window.show()
            window.switch_page(PAGE_PLUGINS)
            self.app.processEvents()
            window.resize(940, 780)
            self.app.processEvents()
            self.assertEqual(window.pages.currentWidget().horizontalScrollBar().maximum(), 0)
            self.assertEqual(len(window.plugin_states), 5)
            self.assertIs(window.plugin_grid.itemAtPosition(0, 2).widget(),
                          window.plugin_cards["sensevoice"])
            self.assertIs(window.plugin_grid.itemAtPosition(1, 0).widget(),
                          window.plugin_cards["gpt_sovits"])
            for name, toggle in window.plugin_switches.items():
                self.assertFalse(toggle.isEnabled(), name)
                self.assertIsNotNone(window.plugin_indicators[name])
            QTest.mouseClick(window.plugin_switches["snowluma"], Qt.MouseButton.LeftButton)
            self.assertEqual(window.pages.currentIndex(), PAGE_PLUGINS)
            QTest.mouseClick(window.plugin_links["snowluma"], Qt.MouseButton.LeftButton)
            self.assertEqual(window.pages.currentIndex(), PAGE_PLATFORM)
        finally:
            window.quit_app()

    def test_managed_child_event_reaches_current_session(self):
        window = Dashboard()
        try:
            self.assertGreater(window._event_receiver.localPort(), 0)
            packet = json.dumps({"token": window._event_token, "event": {
                "time": "2026-10-20T22:59:04.588+08:00", "pid": 42,
                "source": "QQ", "phase": "思考内容", "detail": '{"tone":"关切"}',
            }}, ensure_ascii=False).encode("utf-8")
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(packet, ("127.0.0.1", window._event_receiver.localPort()))
            window._receive_child_events()
            window.refresh_logs(force=True)
            self.assertIn('"tone":"关切"', window.log_view.toPlainText())
        finally:
            window.quit_app()

    def test_connection_restart_preserves_window_and_restarts_managed_services(self):
        window = Dashboard()
        try:
            window.switch_page(PAGE_MONITOR)
            with patch.object(window.bridge, "state", return_value=QProcess.ProcessState.Running), \
                    patch.object(window.tts_server, "state", return_value=QProcess.ProcessState.Running), \
                    patch.object(window, "_stop") as stop, \
                    patch.object(window, "_start_bridge") as start_bridge, \
                    patch.object(window, "toggle_tts_service") as start_speech, \
                    patch.object(window, "quit_app") as quit_app:
                window.restart_connections()
                self.assertEqual(stop.call_count, 2)
                start_bridge.assert_called_once()
                start_speech.assert_called_once()
                quit_app.assert_not_called()
            self.assertEqual(window.pages.currentIndex(), PAGE_MONITOR)
        finally:
            window.quit_app()
