"""Local control center for the existing voice, LLM, memory and QQ modules."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer, QUrl, QUrlQuery
from PySide6.QtGui import QAction, QDesktopServices, QFont, QFontDatabase, QTextCursor
from PySide6.QtNetwork import QHostAddress, QNetworkAccessManager, QNetworkReply, QNetworkRequest, QUdpSocket
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox, QDoubleSpinBox, QStackedWidget, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)

from activity_log import (format_event, import_external_events, log_event,
                          receive_event, session_snapshot)
from branding import APP_NAME, app_icon
from zhuangfy.paths import Paths
from zhuangfy.storage import RESULT_FILE, estimated_bytes
from zhuangfy.memory.routing import legacy_memory_allowed, new_memory_selected
from llm import (
    DEFAULT_PROMPT, load_key, load_settings, save_settings,
)
from persona_packs import BUILTIN_PACKS, matching_pack
from owner_memory import current_bot_id, load_link, save_link
from gpt_sovits import (ENGINE_ROOT, SpeechSettings, available_models, save_model_config,
                        load_settings as load_speech_settings, save_settings as save_speech_settings)
from qq_bridge import QqSettings, SETTINGS_FILE as QQ_SETTINGS_FILE, load_snowluma_token
from zhuangfy.agent_v2.proactive_delivery import ProactiveConfig
from chat_page import ChatPage
from chat_stream import ChatStreamPage
from model_management_page import ModelManagementPage
from model_routes import load_route_key, load_routes
from zhuangfy.ui.console_highlighter import ConsoleHighlighter
from zhuangfy.ui.follow_scroll import FollowScroll
from zhuangfy.ui.window_preferences import (
    PRESETS as WINDOW_PRESETS, WindowPreferences, fit_to_screen,
    load_preferences as load_window_preferences,
    save_preferences as save_window_preferences,
)
from toggle_switch import ToggleSwitch


ROOT = Path(__file__).resolve().parent
DATA_ROOT = Paths.current().data_root
SNOWLUMA_WEBUI = QUrl("http://127.0.0.1:5099/")
SNOWLUMA_DIR = Paths.current().snowluma_root
PAGE_HOME, PAGE_CHAT, PAGE_STREAM, PAGE_MEMORY, PAGE_PERSONA, PAGE_MODEL, PAGE_PLATFORM, PAGE_PLUGINS, PAGE_MONITOR, PAGE_SYSTEM, PAGE_VOICE = range(11)


def _hybrid_preview_active() -> bool:
    return os.environ.get("ZHUANGFY_HYBRID_PREVIEW_ACTIVE") == "1"


def _button(label: str, callback, *, primary: bool = False) -> QPushButton:
    button = QPushButton(label)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setProperty("primary", primary)
    button.clicked.connect(callback)
    return button


def _card(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(13)
    heading = QLabel(title)
    heading.setObjectName("cardTitle")
    layout.addWidget(heading)
    if description:
        caption = QLabel(description)
        caption.setObjectName("muted")
        caption.setWordWrap(True)
        layout.addWidget(caption)
    return frame, layout


def _line(label: str, value: str) -> QLineEdit:
    edit = QLineEdit(value)
    edit.setAccessibleName(label)
    return edit


def _ids(text: str) -> list[int]:
    entries = [part.strip() for part in text.replace("，", ",").replace("\n", ",").split(",")]
    values = []
    for entry in entries:
        if not entry:
            continue
        if not entry.isdecimal() or int(entry) <= 0:
            raise ValueError("QQ 白名单只能填写正整数，用逗号分隔")
        values.append(int(entry))
    return sorted(set(values))


def _save_snowluma_token(value: str) -> None:
    value = value.strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError("OneBot Token 不能为空，也不能包含换行")
    path = DATA_ROOT / ".env.local"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [line for line in lines if line.partition("=")[0].strip() != "SNOWLUMA_ACCESS_TOKEN"]
    lines.append(f"SNOWLUMA_ACCESS_TOKEN={value}")
    temporary = path.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _port_open(url: str) -> bool:
    parsed = urlparse(url)
    try:
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.port is None:
            return False
        with socket.create_connection((parsed.hostname, parsed.port), timeout=0.12):
            return True
    except (OSError, ValueError):
        return False


def _tts_python() -> tuple[Path, Path]:
    """Use a real CPython executable in the venv, bypassing uv's Windows launcher."""
    venv = ENGINE_ROOT / ".venv"
    cfg = venv / "pyvenv.cfg"
    if not cfg.is_file():
        raise FileNotFoundError(f"GPT-SoVITS 虚拟环境配置不存在：{cfg}")
    home = next((line.partition("=")[2].strip().strip('"')
                 for line in cfg.read_text(encoding="utf-8").splitlines()
                 if line.partition("=")[0].strip().lower() == "home"), "")
    base_python = Path(home) / "python.exe" if home else None
    if base_python is None or not base_python.is_file():
        raise FileNotFoundError(f"GPT-SoVITS 虚拟环境的基础 Python 不存在：{home}")
    direct_python = venv / "Scripts" / "python-gpt-sovits.exe"
    source_stat = base_python.stat()
    if (not direct_python.is_file() or
            direct_python.stat().st_size != source_stat.st_size or
            direct_python.stat().st_mtime_ns != source_stat.st_mtime_ns):
        shutil.copy2(base_python, direct_python)
    return direct_python, base_python.parent


def _tts_process_environment(base_home: Path | None = None) -> QProcessEnvironment:
    """Keep the desktop interpreter from leaking into GPT-SoVITS's venv."""
    environment = QProcessEnvironment.systemEnvironment()
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE",
                 "__PYVENV_LAUNCHER__", "VIRTUAL_ENV"):
        environment.remove(name)
    if base_home is not None:
        environment.insert("PATH", str(base_home) + os.pathsep + environment.value("PATH"))
    return environment


def _drop_memory_admin_secrets(environment: QProcessEnvironment) -> QProcessEnvironment:
    # Never leak manager-only tokens or curator model keys to QQ, SnowLuma or TTS.
    for secret in ("ZHUANGFY_MEMORY_ADMIN_TOKEN",
                   "ZHUANGFY_MEMORY_ADMIN_UI",
                   "ZHUANGFY_MEMORY_LLM_API_KEY",
                   "ZHUANGFY_MEMORY_LLM_BASE_URL",
                   "ZHUANGFY_MEMORY_LLM_MODEL",
                   "ZHUANGFY_MEMORY_ALLOW_REMOTE",
                   "ZHUANGFY_MEMORY_REFLECT",
                   "ZHUANGFY_MEMORY_DESKTOP",
                   "ZHUANGFY_MEMORY_DESKTOP_USER_ID"):
        environment.remove(secret)
    return environment


def _drop_memory_non_bridge_secrets(environment: QProcessEnvironment) -> QProcessEnvironment:
    environment = _drop_memory_admin_secrets(environment)
    environment.remove("ZHUANGFY_MEMORY_TOKEN")
    environment.remove("ZHUANGFY_MEMORY_QQ")
    return environment


class Dashboard(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} · 控制台")
        self.setMinimumSize(940, 620)
        self.window_preferences = load_window_preferences()
        self._window_resize_ready = False
        self._applying_window_size = False
        self.apply_window_preferences(self.window_preferences)
        self.window_resize_timer = QTimer(self)
        self.window_resize_timer.setSingleShot(True)
        self.window_resize_timer.setInterval(500)
        self.window_resize_timer.timeout.connect(self.save_custom_window_size)
        self.voice_window = None
        self.chat_window = None
        self.memory_window = None
        self.curated_memory_window = None
        self._tts_network = QNetworkAccessManager(self)
        self._tts_pending_settings = None
        self._tts_pending_model = None
        self._tts_switch_step = 0
        self._auto_bridge_pending = False
        self._bridge_connected = False
        self._closing = False
        self._really_quit = False
        self._tray_hint_shown = False
        self._event_token = secrets.token_urlsafe(24)
        self._event_receiver = QUdpSocket(self)
        self._event_receiver.bind(QHostAddress.SpecialAddress.LocalHost, 0)
        self._event_receiver.readyRead.connect(self._receive_child_events)
        self.setWindowIcon(app_icon())
        self.snowluma = QProcess(self)
        self.snowluma.setProcessEnvironment(
            _drop_memory_non_bridge_secrets(QProcessEnvironment.systemEnvironment()))
        # SnowLuma forks another Node process. Its child must never inherit a
        # pipe owned by this window: after a UI restart that pipe becomes EPIPE
        # and SnowLuma can flood its own log instead of completing OneBot calls.
        self.snowluma.setStandardOutputFile(QProcess.nullDevice())
        self.snowluma.setStandardErrorFile(QProcess.nullDevice())
        self.bridge = QProcess(self)
        self.tts_server = QProcess(self)
        # Keep the local speech service usable if the console crashes. A pipe
        # owned by this window becomes invalid in an orphaned Python process.
        self.tts_server.setStandardOutputFile(QProcess.nullDevice())
        self.tts_server.setStandardErrorFile(QProcess.nullDevice())
        self._managed = {self.snowluma: "SnowLuma", self.bridge: "QQ 桥接",
                         self.tts_server: "GPT-SoVITS"}
        for process in self._managed:
            process.errorOccurred.connect(lambda _error, p=process: self._process_error(p))
            process.finished.connect(lambda _code, _status, p=process: self._process_finished(p))
            process.readyReadStandardOutput.connect(lambda p=process: self._read_output(p))
            process.readyReadStandardError.connect(lambda p=process: self._read_output(p))

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(222)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 25, 16, 20)
        side.setSpacing(4)
        brand = QLabel("庄方宜")
        brand.setObjectName("brand")
        side.addWidget(brand)
        subbrand = QLabel("ZHUANGFY  /  DESKTOP")
        subbrand.setObjectName("subbrand")
        side.addWidget(subbrand)
        side.addSpacing(25)
        self.nav = []
        for section, names in (("", ("首页", "聊天", "聊天流")),
                               ("工作空间", ("记忆中心", "角色设置", "模型管理")),
                               ("服务管理", ("平台连接", "插件管理", "运行监控")),
                               ("高级", ("系统设置",))):
            if section:
                side.addSpacing(18)
                caption = QLabel(section.upper())
                caption.setObjectName("navGroup")
                side.addWidget(caption)
                side.addSpacing(4)
            for name in names:
                button = _button(name, lambda _checked=False, i=len(self.nav): self.switch_page(i))
                button.setObjectName("nav")
                side.addWidget(button)
                self.nav.append(button)
        side.addStretch()
        status_row = QHBoxLayout()
        self.service_dot = QLabel("●")
        self.service_dot.setObjectName("serviceDot")
        self.sidebar_service = QLabel("检查服务中")
        self.sidebar_service.setObjectName("sidebarService")
        status_row.addWidget(self.service_dot)
        status_row.addWidget(self.sidebar_service)
        status_row.addStretch()
        side.addLayout(status_row)
        shell.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        shell.addWidget(self.pages, 1)
        self.pages.addWidget(self._overview())
        self.chat_page = ChatPage(self)
        self.chat_page.open_models.connect(lambda: self.switch_page(PAGE_MODEL))
        self.chat_page.open_voice.connect(self.open_voice)
        self.pages.addWidget(self.chat_page)
        self.chat_stream = ChatStreamPage(self)
        self.pages.addWidget(self.chat_stream)
        self.pages.addWidget(self._memory_page())
        self.pages.addWidget(self._persona_page())
        self.pages.addWidget(self._model_page())
        self.pages.addWidget(self._qq_page())
        self.pages.addWidget(self._plugins_page())
        self.pages.addWidget(self._logs_page())
        self.pages.addWidget(self._system_page())
        self.pages.addWidget(self._home_voice_page())
        self.switch_page(PAGE_HOME)
        QTimer.singleShot(0, self.show_storage_migration_result)
        self.setStyleSheet(STYLE)
        self.statusBar().showMessage("控制台就绪")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start(3000)
        import_external_events()
        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self.refresh_logs)
        self.log_timer.timeout.connect(self.chat_stream.refresh)
        self.log_timer.start(1500)
        self._setup_tray()
        self.refresh_status()
        self._log("控制台已启动")
        self.refresh_logs()
        QTimer.singleShot(0, self.enable_window_resize_tracking)

    def _setup_tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("庄方宜 · ZhuangFY")
        menu = self.tray.contextMenu()
        if menu is None:
            from PySide6.QtWidgets import QMenu
            menu = QMenu(self)
        for label, callback in (("打开控制台", self.show_console),
                                ("打开语音窗口", self.open_voice),
                                ("语音交互", lambda: self.show_console(PAGE_VOICE)),
                                ("查看运行监控", lambda: self.show_console(PAGE_MONITOR)),
                                ("重启连接", self.restart_connections)):
            action = QAction(label, self)
            action.triggered.connect(callback)
            menu.addAction(action)
        menu.addSeparator()
        quit_action = QAction("退出 ZhuangFY", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_console()
                                    if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                                                  QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.show()
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def show_console(self, page: int | None = None) -> None:
        if type(page) is int:
            self.switch_page(page)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._really_quit = True
        self.close()
        for window in (self.voice_window, self.chat_window, self.memory_window, self.curated_memory_window):
            if window is not None:
                if window is self.voice_window:
                    window.hide_on_close = False
                window.close()
        if self.tray is not None:
            self.tray.hide()
        QApplication.instance().quit()

    def restart_connections(self) -> None:
        bridge_running = self.bridge.state() != QProcess.ProcessState.NotRunning
        speech_running = self.tts_server.state() != QProcess.ProcessState.NotRunning
        self._auto_bridge_pending = False
        if bridge_running:
            self._stop(self.bridge)
        if speech_running:
            self._stop(self.tts_server)
        if bridge_running:
            self._start_bridge()
        if speech_running:
            self.toggle_tts_service()
        if self.voice_window is not None:
            self.voice_window.refresh_speech_engine()
        active = [name for name, running in (("QQ 桥接", bridge_running),
                                              ("GPT-SoVITS", speech_running)) if running]
        message = (f"已重启 {'、'.join(active)}；控制台与 SnowLuma 保持运行" if active else
                   "没有由控制台管理的连接需要重启；界面设置会在下一轮读取")
        self._log(message)
        self.statusBar().showMessage(message)

    def _receive_child_events(self) -> None:
        received = False
        while self._event_receiver.hasPendingDatagrams():
            datagram = self._event_receiver.receiveDatagram(20_000)
            if datagram.senderAddress().isLoopback():
                received = receive_event(bytes(datagram.data()), self._event_token) or received
        if received:
            if self.pages.currentIndex() == PAGE_MONITOR:
                self.refresh_logs()
            elif self.pages.currentIndex() == PAGE_STREAM:
                self.chat_stream.refresh()

    def _page(self, title: str, subtitle: str) -> tuple[QScrollArea, QVBoxLayout]:
        wrapper = QScrollArea()
        wrapper.setWidgetResizable(True)
        wrapper.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("content")
        column = QVBoxLayout(content)
        column.setContentsMargins(36, 28, 36, 34)
        column.setSpacing(18)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        column.addWidget(heading)
        if subtitle:
            description = QLabel(subtitle)
            description.setObjectName("muted")
            description.setWordWrap(True)
            column.addWidget(description)
        wrapper.setWidget(content)
        return wrapper, column

    def _overview(self) -> QScrollArea:
        page, column = self._page("欢迎回来", "从这里开始对话，或查看庄方宜正在使用的服务。")
        hero, layout = _card("今天想聊些什么？", "文字与语音入口都在这里；对话使用当前角色、模型与已启用的记忆服务。")
        hero.setObjectName("heroCard")
        actions = QHBoxLayout()
        actions.addWidget(_button("开始聊天  →", lambda: self.switch_page(PAGE_CHAT), primary=True))
        actions.addWidget(_button("语音交互", lambda: self.switch_page(PAGE_VOICE)))
        actions.addWidget(_button("打开完整语音窗口  ↗", self.open_voice))
        actions.addStretch()
        layout.addLayout(actions)
        column.addWidget(hero)

        row = QHBoxLayout()
        model_card, model_layout = _card("对话能力")
        self.model_state = QLabel()
        model_layout.addWidget(self.model_state)
        self.api_state = QLabel()
        model_layout.addWidget(self.api_state)
        self.memory_state = QLabel()
        model_layout.addWidget(self.memory_state)
        self.memory_core_state = QLabel()
        model_layout.addWidget(self.memory_core_state)
        model_layout.addWidget(_button("查看模型管理  →", lambda: self.switch_page(PAGE_MODEL)))
        row.addWidget(model_card)
        qq_card, qq_layout = _card("平台与声音")
        self.snow_state = QLabel()
        qq_layout.addWidget(self.snow_state)
        self.onebot_state = QLabel()
        qq_layout.addWidget(self.onebot_state)
        self.bridge_state = QLabel()
        qq_layout.addWidget(self.bridge_state)
        self.tts_home_state = QLabel()
        qq_layout.addWidget(self.tts_home_state)
        qq_layout.addWidget(_button("查看平台连接  →", lambda: self.switch_page(PAGE_PLATFORM)))
        row.addWidget(qq_card)
        column.addLayout(row)
        activity, activity_layout = _card("最近运行")
        self.recent_overview = QLabel("本次启动尚无记录")
        self.recent_overview.setObjectName("muted")
        self.recent_overview.setWordWrap(True)
        activity_layout.addWidget(self.recent_overview)
        activity_layout.addWidget(_button("打开运行监控  →", lambda: self.switch_page(PAGE_MONITOR)))
        column.addWidget(activity)
        column.addStretch()
        return page

    def _memory_page(self) -> QScrollArea:
        page, column = self._page("记忆中心", "查看新记忆服务与本机旧版记忆的状态，并管理已经保存的内容。")
        current, layout = _card("新记忆服务", "QQ 与桌面入口通过已核验的身份共用人物记忆；具体内容和授权可在管理窗口查看。")
        self.memory_service_state = QLabel()
        layout.addWidget(self.memory_service_state)
        layout.addWidget(_button("打开新记忆管理  ↗", self.open_curated_memory, primary=True))
        column.addWidget(current)
        legacy, legacy_layout = _card("旧版记忆档案", "旧数据保留供查看和后续迁移；新服务接管时不再读取或写入旧库。")
        self.memory_enabled = ToggleSwitch("在对话中使用旧版长期记忆")
        self.memory_enabled.setChecked(load_settings().memory_enabled and legacy_memory_allowed("desktop"))
        self.memory_enabled.setEnabled(legacy_memory_allowed("desktop"))
        legacy_layout.addWidget(self.memory_enabled)
        actions = QHBoxLayout()
        legacy_save = _button("保存旧版记忆开关", self.save_memory_setting)
        legacy_save.setEnabled(legacy_memory_allowed("desktop"))
        actions.addWidget(legacy_save)
        legacy_manager = _button("管理旧版记忆  ↗", self.open_memory)
        legacy_manager.setEnabled(legacy_memory_allowed("desktop"))
        actions.addWidget(legacy_manager)
        actions.addStretch()
        legacy_layout.addLayout(actions)
        self.memory_feedback = QLabel("新记忆服务已接管桌面入口。" if new_memory_selected("desktop") else
                                      "旧版记忆仅在新服务未接管时可用。")
        self.memory_feedback.setObjectName("muted")
        legacy_layout.addWidget(self.memory_feedback)
        column.addWidget(legacy)
        column.addStretch()
        return page

    def _persona_page(self) -> QScrollArea:
        page, column = self._page("角色设置", "选择人格包或直接调整庄方宜的行为提示词；保存后供下一轮对话使用。")
        persona, layout = _card("庄方宜的人格")
        row = QHBoxLayout()
        self.persona_pack = QComboBox()
        for pack in BUILTIN_PACKS:
            self.persona_pack.addItem(pack.name, pack)
        row.addWidget(self.persona_pack, 1)
        row.addWidget(_button("载入人格包", self.load_persona_pack))
        layout.addLayout(row)
        self.prompt = QPlainTextEdit(load_settings().system_prompt or DEFAULT_PROMPT)
        self.prompt.setMinimumHeight(330)
        layout.addWidget(self.prompt)
        self.persona_state = QLabel()
        self.persona_state.setObjectName("muted")
        self.prompt.textChanged.connect(self.update_persona_state)
        layout.addWidget(self.persona_state)
        layout.addWidget(_button("保存角色设置", self.save_persona, primary=True))
        self.update_persona_state()
        column.addWidget(persona)
        column.addStretch()
        return page

    def _model_page(self) -> QWidget:
        return ModelManagementPage(self)

    def load_persona_pack(self) -> None:
        pack = self.persona_pack.currentData()
        if pack is not None:
            self.prompt.setPlainText(pack.prompt())

    def update_persona_state(self) -> None:
        pack = matching_pack(self.prompt.toPlainText())
        self.persona_state.setText(
            f"当前：{pack.name}。载入后仍需保存才会生效。" if pack else
            "当前：自定义提示词。选择人格包不会覆盖；点击“载入人格包”才会替换编辑框。"
        )

    def save_persona(self) -> bool:
        try:
            save_settings(replace(load_settings(), system_prompt=self.prompt.toPlainText()))
            self.update_persona_state()
            self.statusBar().showMessage("角色设置已保存", 5000)
            return True
        except (OSError, ValueError) as error:
            self.persona_state.setText(f"保存失败：{error}")
            return False

    def save_memory_setting(self) -> bool:
        try:
            if not legacy_memory_allowed("desktop"):
                raise ValueError("新记忆服务已接管桌面入口，不能同时启用旧版记忆")
            enabled = self.memory_enabled.isChecked()
            if enabled and not load_key("siliconflow"):
                raise ValueError("启用旧版长期记忆前，需要保存硅基流动 API Key")
            save_settings(replace(load_settings(), memory_enabled=enabled))
            self.memory_feedback.setText("旧版长期记忆设置已保存。")
            self.refresh_status()
            return True
        except (OSError, ValueError) as error:
            self.memory_feedback.setText(f"保存失败：{error}")
            return False

    def _qq_page(self) -> QScrollArea:
        page, column = self._page("平台连接", "在这里连接 QQ 与 SnowLuma。WebUI 用于登录和配置，OneBot WebSocket 用于消息收发。")
        service, layout = _card("服务控制", "首次使用仍须在 SnowLuma WebUI 登录机器人 QQ，并开启 OneBot WebSocket 服务端。")
        self.service_state = QLabel()
        layout.addWidget(self.service_state)
        row = QHBoxLayout()
        self.snow_button = _button("启动 SnowLuma", self.toggle_snowluma, primary=True)
        self.bridge_button = _button("启动 QQ 桥接", self.toggle_bridge)
        if _hybrid_preview_active():
            self.bridge_button.setEnabled(False)
            self.bridge_button.setToolTip("Hybrid 预览正由独立进程管理 QQ 连接")
        row.addWidget(self.snow_button)
        row.addWidget(_button("打开 SnowLuma 配置页面  ↗", self.open_snowluma_webui))
        row.addWidget(self.bridge_button)
        row.addStretch()
        layout.addLayout(row)
        self.auto_connect = ToggleSwitch("启动 SnowLuma 后自动连接 OneBot 并运行 QQ 桥接")
        self.auto_connect.setChecked(not _hybrid_preview_active())
        self.auto_connect.setEnabled(not _hybrid_preview_active())
        layout.addWidget(self.auto_connect)
        column.addWidget(service)

        try:
            saved = QqSettings.load()
            config_message = ""
        except (OSError, ValueError, TypeError) as error:
            saved = QqSettings()
            config_message = str(error)
        connection, conn_layout = _card("OneBot 连接", "只接受本机 ws:// 地址。Token 与模型 API Key 分开存放。")
        form = QFormLayout()
        form.setSpacing(12)
        self.ws_url = _line("OneBot WebSocket 地址", saved.ws_url)
        form.addRow("WebSocket", self.ws_url)
        self.qq_token = QLineEdit()
        self.qq_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.qq_token.setPlaceholderText("留空则保持现有 Token；未设置 Token 时可留空")
        form.addRow("OneBot Token", self.qq_token)
        self.token_state = QLabel("已保存" if load_snowluma_token() else "未设置")
        self.token_state.setObjectName("muted")
        form.addRow("Token 状态", self.token_state)
        conn_layout.addLayout(form)
        column.addWidget(connection)

        rules, rules_layout = _card("回复范围与频率", "私聊仅对白名单账号回复；群聊仅在白名单群内被 @ 时回复。白名单为空时桥接不会启动。")
        rule_form = QFormLayout()
        rule_form.setSpacing(12)
        self.private_ids = _line("私聊白名单", ", ".join(map(str, sorted(saved.allowed_private_ids))))
        self.private_ids.setPlaceholderText("多个 QQ 号用逗号分隔")
        rule_form.addRow("私聊 QQ 号", self.private_ids)
        self.initial_l0_private_ids = _line(
            "初识 L0 QQ 号", ", ".join(map(str, sorted(saved.initial_l0_private_ids))))
        self.initial_l0_private_ids.setPlaceholderText("测试账号用初识语气；须先加入私聊 QQ 号")
        rule_form.addRow("初识 L0 QQ 号", self.initial_l0_private_ids)
        self.voice_private_ids = _line("允许模型选择语音的 QQ 号", ", ".join(map(str, sorted(saved.voice_private_ids))))
        self.voice_private_ids.setPlaceholderText("留空则只发文字；仅限已在私聊白名单中的账号")
        rule_form.addRow("语音回复 QQ 号", self.voice_private_ids)
        self.group_ids = _line("群聊白名单", ", ".join(map(str, sorted(saved.allowed_group_ids))))
        self.group_ids.setPlaceholderText("不填则关闭群聊")
        rule_form.addRow("群号", self.group_ids)
        self.private_cooldown = self._number(saved.private_cooldown_seconds, 0, 120, decimal=True)
        rule_form.addRow("私聊间隔 / 秒", self.private_cooldown)
        self.private_reply_parts = self._number(saved.private_max_reply_parts, 1, 3)
        rule_form.addRow("私聊一轮最多分几条", self.private_reply_parts)
        self.group_cooldown = self._number(saved.group_cooldown_seconds, 0, 120, decimal=True)
        rule_form.addRow("群聊间隔 / 秒", self.group_cooldown)
        self.private_hourly = self._number(saved.private_max_replies_per_hour, 1, 120)
        rule_form.addRow("私聊每小时上限", self.private_hourly)
        self.group_hourly = self._number(saved.group_max_replies_per_hour, 1, 120)
        rule_form.addRow("群聊每小时上限", self.group_hourly)
        self.quiet_seconds = self._number(saved.quiet_seconds, 0, 120, decimal=True)
        rule_form.addRow("合并消息等待 / 秒", self.quiet_seconds)
        rules_layout.addLayout(rule_form)
        rules_layout.addWidget(_button("保存 QQ 配置", self.save_qq, primary=True))
        self.qq_feedback = QLabel(config_message or "设置保存在本机 qq_settings.json；运行中的桥接需重启生效。")
        self.qq_feedback.setObjectName("muted")
        self.qq_feedback.setWordWrap(True)
        rules_layout.addWidget(self.qq_feedback)
        column.addWidget(rules)
        proactive = ProactiveConfig.load(DATA_ROOT / "proactive_settings.json")
        outreach, outreach_layout = _card(
            "主动互动", "经 B2 审阅的后台任务建议可主动发送；日常问候单独开关。默认安静时段 22:00–08:00，至少 30 分钟未交谈、每 8 小时至多一次、24 小时内至多一条。")
        outreach_form = QFormLayout()
        self.proactive_enabled = ToggleSwitch("允许主动私聊")
        self.proactive_enabled.setChecked(proactive.enabled)
        outreach_form.addRow("总开关", self.proactive_enabled)
        self.proactive_channel = QComboBox()
        self.proactive_channel.addItem("QQ 私聊", "qq")
        outreach_form.addRow("消息通道", self.proactive_channel)
        self.proactive_recipient = _line("主动互动收件人 QQ 号", str(proactive.recipient_id or ""))
        outreach_form.addRow("收件人", self.proactive_recipient)
        self.proactive_checkin = ToggleSwitch("没有后台任务建议时，允许每日一次自然问候")
        self.proactive_checkin.setChecked(proactive.daily_checkin)
        outreach_form.addRow("日常问候", self.proactive_checkin)
        outreach_layout.addLayout(outreach_form)
        outreach_layout.addWidget(_button("保存主动互动设置", self.save_proactive, primary=True))
        self.proactive_feedback = QLabel("只会发给已在 QQ 私聊白名单中的账号；保存后重启 QQ 桥接生效。")
        self.proactive_feedback.setObjectName("muted")
        outreach_layout.addWidget(self.proactive_feedback)
        column.addWidget(outreach)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(250)
        self.log.setMinimumHeight(115)
        self.log.setPlaceholderText("由控制台启动的服务日志会显示在这里（不显示密钥）")
        column.addWidget(self.log)
        column.addStretch()
        return page

    def _plugins_page(self) -> QScrollArea:
        page, column = self._page("插件管理", "查看本机接入组件的可用状态，并进入各自的控制页面。")
        self.plugin_states = {}
        for key, title, detail, label, callback in (
            ("snowluma", "SnowLuma", "QQ 登录、OneBot 消息通道", "打开平台连接", lambda: self.switch_page(PAGE_PLATFORM)),
            ("qq_bridge", "QQ 桥接", "私聊与群聊消息收发、频率控制", "打开平台连接", lambda: self.switch_page(PAGE_PLATFORM)),
            ("sensevoice", "SenseVoice", "本地语音识别与持续监听", "打开语音交互", lambda: self.switch_page(PAGE_VOICE)),
            ("gpt_sovits", "GPT-SoVITS", "本地角色语音生成服务", "打开语音设置", lambda: self.switch_page(PAGE_VOICE)),
            ("memory", "新记忆服务", "人物身份、时间线、整理与检索", "打开记忆中心", lambda: self.switch_page(PAGE_MEMORY)),
        ):
            card, layout = _card(title, detail)
            status = QLabel("正在检查连接…")
            status.setObjectName("pluginStatus")
            layout.addWidget(status)
            layout.addWidget(_button(label + "  →", callback))
            self.plugin_states[key] = status
            column.addWidget(card)
        column.addStretch()
        return page

    def _system_page(self) -> QScrollArea:
        page, column = self._page("系统设置", "调整窗口与本地文件保存位置。")
        runtime, layout = _card("窗口大小")
        size_row = QHBoxLayout()
        self.window_size_preset = QComboBox()
        self.window_size_preset.addItem("自定义大小", None)
        for width, height in WINDOW_PRESETS:
            self.window_size_preset.addItem(f"{width} × {height}", (width, height))
        selected = self._window_preset_index(
            (self.window_preferences.width, self.window_preferences.height))
        self.window_size_preset.setCurrentIndex(selected if selected >= 0 else 0)
        size_row.addWidget(self.window_size_preset)
        size_row.addStretch()
        layout.addLayout(size_row)
        self.window_size_preset.currentIndexChanged.connect(self.select_window_preset)
        column.addWidget(runtime)
        storage, storage_layout = _card("文件保存", "配置、聊天记录、记忆库与本地语音模型存放在此。")
        location_row = QHBoxLayout()
        self.storage_address = QLineEdit(str(Paths.current().storage_root or Paths.current().project_root))
        self.storage_address.setReadOnly(True)
        location_row.addWidget(self.storage_address, 1)
        location_row.addWidget(_button("更换", self.change_storage_location))
        storage_layout.addLayout(location_row)
        storage_layout.addWidget(QLabel("更换后程序会自动迁移并重启；原文件保留为备份。"))
        column.addWidget(storage)
        column.addStretch()
        return page

    def change_storage_location(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择新的文件保存位置")
        if not selected:
            return
        destination = Path(selected)
        try:
            required = estimated_bytes(Paths.current().project_root, destination)
            if shutil.disk_usage(destination).free < required + 256 * 1024 * 1024:
                raise ValueError("目标磁盘剩余空间不足")
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "无法更换位置", str(error))
            return
        if QMessageBox.question(
            self, "迁移本地文件",
            f"将本地配置、数据库、日志和语音模型复制到：\n{destination}\n\n"
            "复制校验成功后自动切换并重启。旧文件会保留作备份。继续吗？",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            subprocess.Popen(
                [sys.executable, "-m", "zhuangfy.storage", "--project-root",
                 str(Paths.current().project_root), "--target", str(destination),
                 "--wait-pid", str(os.getpid())],
                cwd=str(Paths.current().project_root),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )
        except OSError as error:
            QMessageBox.warning(self, "迁移启动失败", str(error))
            return
        self.quit_app()

    def show_storage_migration_result(self) -> None:
        result_file = Paths.current().project_root / RESULT_FILE
        if not result_file.is_file():
            return
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
            result_file.unlink()
        except (OSError, ValueError):
            return
        if result.get("ok"):
            self.storage_address.setText(result["path"])
            QMessageBox.information(self, "迁移完成", "本地文件已校验并切换到新地址。")
        else:
            QMessageBox.warning(self, "迁移未完成", result.get("error", "未知错误"))

    def apply_window_preferences(self, value: WindowPreferences) -> tuple[int, int]:
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        if screen is None:
            applied = (value.width, value.height)
        else:
            available = screen.availableGeometry()
            applied = fit_to_screen(value, available.width(), available.height())
        self._applying_window_size = True
        try:
            if self.isMaximized():
                self.showNormal()
            self.resize(*applied)
            if screen is not None:
                available = screen.availableGeometry()
                self.move(available.x() + max(0, (available.width() - applied[0]) // 2),
                          available.y() + max(0, (available.height() - applied[1]) // 2))
        finally:
            self._applying_window_size = False
        return applied

    def _window_preset_index(self, pair: tuple[int, int]) -> int:
        for index in range(self.window_size_preset.count()):
            value = self.window_size_preset.itemData(index)
            if value is not None and tuple(value) == pair:
                return index
        return -1

    def enable_window_resize_tracking(self) -> None:
        self._window_resize_ready = True

    def select_window_preset(self, index: int) -> None:
        value = self.window_size_preset.itemData(index)
        if value is None:
            self.statusBar().showMessage("拖动窗口边缘调整大小后会自动保存", 5000)
            return
        self.window_resize_timer.stop()
        preference = WindowPreferences(*value)
        try:
            save_window_preferences(preference)
        except (OSError, ValueError) as error:
            self.statusBar().showMessage(f"窗口大小保存失败：{error}", 5000)
            return
        self.window_preferences = preference
        self.apply_window_preferences(preference)
        self.statusBar().showMessage(f"窗口大小已保存：{value[0]} × {value[1]}", 5000)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if (not getattr(self, "_window_resize_ready", False)
                or self._applying_window_size or self.isMaximized() or self.isMinimized()):
            return
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        applied = (self.window_preferences.width, self.window_preferences.height)
        if screen is not None:
            available = screen.availableGeometry()
            applied = fit_to_screen(self.window_preferences,
                                    available.width(), available.height())
        if (self.width(), self.height()) == applied:
            return
        self.window_size_preset.blockSignals(True)
        self.window_size_preset.setCurrentIndex(0)
        self.window_size_preset.blockSignals(False)
        self.window_resize_timer.start()

    def save_custom_window_size(self) -> None:
        if self.isMaximized():
            return
        value = WindowPreferences(self.width(), self.height())
        if value == self.window_preferences:
            return
        try:
            save_window_preferences(value)
        except (OSError, ValueError) as error:
            self.statusBar().showMessage(f"窗口大小保存失败：{error}", 5000)
            return
        self.window_preferences = value

    def _logs_page(self) -> QScrollArea:
        page, column = self._page(
            "运行监控", "按时间查看本次启动的服务、消息、模型请求与回复。")
        card, layout = _card("运行控制台", "当前启动的实时事件流；内容留在内存中，退出后清空。")
        controls = QHBoxLayout()
        self.log_filter = QComboBox()
        self.log_filter.addItems(("全部来源", "控制台", "语音", "文字", "QQ", "模型", "记忆", "SnowLuma", "Hybrid", "Maisaka"))
        self.log_filter.currentIndexChanged.connect(lambda _index: self.refresh_logs(force=True))
        controls.addWidget(QLabel("来源"))
        controls.addWidget(self.log_filter)
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("搜索消息、回复或状态")
        self.log_search.textChanged.connect(lambda _text: self.refresh_logs(force=True))
        controls.addWidget(self.log_search, 1)
        layout.addLayout(controls)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("consoleView")
        self.log_view.setReadOnly(True)
        self.log_view.setUndoRedoEnabled(False)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setMinimumHeight(480)
        self.log_view.setPlaceholderText("等待服务事件…")
        self.log_follow = FollowScroll(self.log_view.verticalScrollBar())
        self.log_highlighter = ConsoleHighlighter(self.log_view.document())
        layout.addWidget(self.log_view)
        bottom = QHBoxLayout()
        self.log_count = QLabel()
        bottom.addWidget(self.log_count)
        bottom.addStretch()
        bottom.addWidget(_button("刷新", lambda: self.refresh_logs(force=True)))
        bottom.addWidget(_button("复制当前视图", lambda: QApplication.clipboard().setText(self.log_view.toPlainText())))
        bottom.addWidget(_button("到底部", self.log_follow.jump_to_latest))
        layout.addLayout(bottom)
        column.addWidget(card)
        column.addStretch()
        self._log_signature = None
        self._log_lines: list[str] = []
        return page

    def _home_voice_page(self) -> QScrollArea:
        page, column = self._page("语音交互", "")
        link_card, link_layout = _card("同一个人，同一份记忆")
        self.voice_link_state = QLabel()
        self.voice_link_state.setWordWrap(True)
        link_layout.addWidget(self.voice_link_state)
        if legacy_memory_allowed("desktop"):
            link_row = QHBoxLayout()
            self.voice_owner_qq = _line("关联的个人 QQ 号", "")
            self.voice_owner_qq.setPlaceholderText("填写已加入私聊白名单的 QQ 号")
            self.voice_owner_qq.setMaximumWidth(190)
            linked = load_link(validate=False)
            if linked is not None:
                self.voice_owner_qq.setText(str(linked.qq_id))
            link_row.addWidget(QLabel("你的 QQ 号"))
            link_row.addWidget(self.voice_owner_qq)
            link_row.addWidget(_button("关联到当前机器人", self.link_voice_owner))
            link_row.addStretch()
            link_layout.addLayout(link_row)
            self.voice_link_feedback = QLabel("仅白名单中已开启个人记忆的 QQ 账号可以关联。")
            self.voice_link_feedback.setObjectName("muted")
            self.voice_link_feedback.setWordWrap(True)
            link_layout.addWidget(self.voice_link_feedback)
        column.addWidget(link_card)

        control, layout = _card("语音对话")
        self.home_voice_state = QLabel("尚未启动")
        layout.addWidget(self.home_voice_state)
        form = QFormLayout()
        self.home_mic = QComboBox()
        self.home_mic.setMinimumContentsLength(38)
        self.home_mic.currentIndexChanged.connect(self.select_home_mic)
        mic_row = QHBoxLayout()
        mic_row.addWidget(self.home_mic, 1)
        mic_row.addWidget(_button("刷新设备", self.refresh_home_devices))
        form.addRow("麦克风", mic_row)
        self.home_voice = QComboBox()
        self.home_voice.currentIndexChanged.connect(self.select_home_voice)
        self.home_voice.hide()
        layout.addLayout(form)
        action_row = QHBoxLayout()
        self.home_start = _button("开始回家对话", self.start_home_voice, primary=True)
        self.home_stop = _button("停止监听", self.stop_home_voice)
        action_row.addWidget(self.home_start)
        action_row.addWidget(self.home_stop)
        action_row.addWidget(_button("打开完整语音窗口  ↗", self.open_voice))
        action_row.addStretch()
        layout.addLayout(action_row)
        tests = QHBoxLayout()
        self.home_record = _button("单句麦克风测试", self.toggle_home_record)
        tests.addWidget(self.home_record)
        tests.addWidget(_button("示例音频识别", self.try_home_sample))
        self.home_tts_test = _button("播报测试", self.test_home_tts)
        tests.addWidget(self.home_tts_test)
        tests.addStretch()
        layout.addLayout(tests)
        self.home_transcript = QPlainTextEdit()
        self.home_transcript.setReadOnly(True)
        self.home_transcript.setMinimumHeight(180)
        self.home_transcript.setPlaceholderText("识别文字和模型回复会在这里同步显示。")
        layout.addWidget(self.home_transcript)
        column.addWidget(control)

        speech = load_speech_settings()
        voice_card, voice_layout = _card("GPT-SoVITS v2Pro")
        self.tts_service_state = QLabel()
        voice_layout.addWidget(self.tts_service_state)
        voice_form = QFormLayout()
        self.tts_engine = QComboBox()
        self.tts_engine.addItem("系统语音（当前可用）", "system")
        self.tts_engine.addItem("GPT-SoVITS v2Pro", "gpt_sovits")
        self.tts_engine.setCurrentIndex(self.tts_engine.findData(speech.engine))
        voice_form.addRow("语音引擎", self.tts_engine)
        self.tts_models = available_models()
        self.tts_model = QComboBox()
        for model in self.tts_models:
            self.tts_model.addItem(model.label, model.model_id)
        model_id = speech.model_id or (
            "zhuangfangyi_v2pro" if "zhaungfyvoice" in speech.reference_audio.lower() else "v2pro_base")
        model_index = self.tts_model.findData(model_id)
        self.tts_model.setCurrentIndex(model_index if model_index >= 0 else 0)
        self.tts_model.setEnabled(speech.engine == "gpt_sovits")
        voice_form.addRow("本地模型", self.tts_model)
        self.tts_reference = _line("参考音频路径", speech.reference_audio)
        reference_row = QHBoxLayout()
        reference_row.addWidget(self.tts_reference, 1)
        reference_row.addWidget(_button("选择音频…", self.choose_tts_reference))
        voice_form.addRow("参考音频", reference_row)
        self.tts_reference_text = _line("参考音频中的准确台词", speech.reference_text)
        self.tts_reference_text.setPlaceholderText("与参考音频逐字一致的中文台词")
        voice_form.addRow("参考台词", self.tts_reference_text)
        self.tts_model_hint = QLabel("可改用自己的参考片段；切换模型会填入该模型的默认参考。")
        self.tts_model_hint.setObjectName("muted")
        self.tts_model_hint.setWordWrap(True)
        voice_form.addRow("", self.tts_model_hint)
        voice_layout.addLayout(voice_form)
        self.tts_model.currentIndexChanged.connect(self.select_tts_model)
        self.tts_engine.currentIndexChanged.connect(self.select_tts_engine)
        voice_actions = QHBoxLayout()
        self.tts_service_button = _button("启动本地语音服务", self.toggle_tts_service)
        voice_actions.addWidget(self.tts_service_button)
        self.tts_save_button = _button("应用模型与语音设置", self.save_tts_settings, primary=True)
        voice_actions.addWidget(self.tts_save_button)
        voice_actions.addStretch()
        voice_layout.addLayout(voice_actions)
        self.tts_feedback = QLabel("服务默认绑定 127.0.0.1:9880；参考音频只存于本机。")
        self.tts_feedback.setObjectName("muted")
        self.tts_feedback.setWordWrap(True)
        voice_layout.addWidget(self.tts_feedback)
        column.addWidget(voice_card)
        column.addStretch()
        self.refresh_home_voice_status()
        return page

    def choose_tts_reference(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "选择参考音频", str(Path.home()), "音频文件 (*.wav *.mp3 *.flac)")
        if filename:
            self.tts_reference.setText(filename)

    def select_tts_model(self, _index: int = -1) -> None:
        index = self.tts_model.currentIndex()
        if index < 0 or index >= len(self.tts_models):
            return
        model = self.tts_models[index]
        self.tts_reference.setText(model.reference_audio)
        self.tts_reference_text.setText(model.reference_text)
        self.tts_model_hint.setText(
            "庄方宜的参考录音和准确台词已自动填入；可以按需要替换。" if model.reference_audio else
            "这个模型没有预设参考。请选一段清晰录音，并填写其中的准确台词。")

    def select_tts_engine(self, _index: int = -1) -> None:
        self.tts_model.setEnabled(self.tts_engine.currentData() == "gpt_sovits")

    def save_tts_settings(self) -> None:
        try:
            model = next((item for item in self.tts_models
                          if item.model_id == self.tts_model.currentData()), None)
            settings = SpeechSettings(self.tts_engine.currentData(), "http://127.0.0.1:9880",
                                      self.tts_reference.text().strip(),
                                      self.tts_reference_text.text().strip(),
                                      model.model_id if model else "")
            settings.validated()
            if settings.engine == "gpt_sovits" and model is None:
                raise ValueError("未找到可用的 GPT 与 SoVITS 模型权重")
            if settings.engine == "gpt_sovits" and _port_open(settings.endpoint):
                self._tts_pending_settings = settings
                self._tts_pending_model = model
                self._tts_switch_step = 0
                self.tts_save_button.setEnabled(False)
                self.tts_feedback.setText("正在加载所选模型权重…")
                self._send_tts_model_switch()
            else:
                if settings.engine == "gpt_sovits":
                    save_model_config(model)
                self._finish_tts_save(settings)
        except (OSError, ValueError, TypeError) as error:
            self.tts_feedback.setText(f"保存失败：{error}")

    def _send_tts_model_switch(self) -> None:
        model = self._tts_pending_model
        if model is None:
            return
        endpoint, weight = (("set_sovits_weights", model.sovits_weight)
                            if self._tts_switch_step == 0 else
                            ("set_gpt_weights", model.gpt_weight))
        url = QUrl(f"http://127.0.0.1:9880/{endpoint}")
        query = QUrlQuery()
        query.addQueryItem("weights_path", str(weight))
        url.setQuery(query)
        request = QNetworkRequest(url)
        request.setTransferTimeout(30_000)
        reply = self._tts_network.get(request)
        reply.finished.connect(lambda r=reply: self._tts_model_switch_finished(r))

    def _tts_model_switch_finished(self, reply: QNetworkReply) -> None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        error = reply.error()
        error_text = reply.errorString()
        detail = bytes(reply.readAll()).decode("utf-8", errors="replace")[:240]
        reply.deleteLater()
        if error != QNetworkReply.NetworkError.NoError or status != 200:
            self.tts_feedback.setText(f"模型加载失败：{detail or error_text}")
            self.tts_save_button.setEnabled(True)
            self._tts_pending_settings = None
            self._tts_pending_model = None
            return
        self._tts_switch_step += 1
        if self._tts_switch_step < 2:
            self._send_tts_model_switch()
            return
        try:
            save_model_config(self._tts_pending_model)
            self._finish_tts_save(self._tts_pending_settings)
        except (OSError, ValueError, TypeError) as error:
            self.tts_feedback.setText(f"模型已加载，但保存设置失败：{error}")
        finally:
            self.tts_save_button.setEnabled(True)
            self._tts_pending_settings = None
            self._tts_pending_model = None

    def _finish_tts_save(self, settings: SpeechSettings) -> None:
        save_speech_settings(settings)
        self.tts_feedback.setText("已应用语音设置；下一轮播报使用所选模型。")
        self._log(f"语音引擎已设置为 {settings.engine}；模型 {settings.model_id or '系统语音'}")
        if self.voice_window is not None:
            self.voice_window.refresh_speech_engine()
        self.home_voice.setEnabled(settings.engine == "system" and self.home_voice.count() > 0)
        if settings.engine == "gpt_sovits":
            QTimer.singleShot(0, self.start_tts_service)

    def toggle_tts_service(self) -> None:
        if self.tts_server.state() != QProcess.ProcessState.NotRunning:
            self._stop(self.tts_server)
            self.refresh_home_voice_status()
            return
        self.start_tts_service()

    def start_tts_service(self) -> None:
        """Start the selected local TTS once, also used during console startup."""
        if self.tts_server.state() != QProcess.ProcessState.NotRunning:
            return
        if _port_open("http://127.0.0.1:9880"):
            self.tts_feedback.setText("本机 9880 端口已有语音服务运行；可以直接使用（不由本控制台管理）。")
            self.refresh_home_voice_status()
            return
        config = ENGINE_ROOT / "GPT_SoVITS" / "configs" / "shimano_v2pro.yaml"
        if not config.is_file():
            self.tts_feedback.setText("GPT-SoVITS v2Pro 配置尚未安装完成。")
            return
        try:
            interpreter, base_home = _tts_python()
        except (OSError, ValueError) as error:
            self.tts_feedback.setText(f"GPT-SoVITS Python 启动失败：{error}")
            self._log(f"GPT-SoVITS Python 启动失败：{error}")
            return
        self.tts_server.setProcessEnvironment(
            _drop_memory_non_bridge_secrets(_tts_process_environment(base_home)))
        self.tts_server.setWorkingDirectory(str(ENGINE_ROOT))
        self.tts_server.start(str(interpreter), [str(ENGINE_ROOT / "api_v2.py"),
                                                  "-a", "127.0.0.1", "-p", "9880", "-c", str(config)])
        self.tts_feedback.setText("正在加载 v2Pro 模型；首次启动可能需要几十秒。")
        self._log("正在启动 GPT-SoVITS v2Pro…")
        self.refresh_home_voice_status()

    def _ensure_voice(self):
        if self.voice_window is None:
            from gui import MainWindow
            self.voice_window = MainWindow()
            self.voice_window.hide_on_close = True
        return self.voice_window

    def refresh_home_devices(self) -> None:
        voice = self._ensure_voice()
        if voice.continuous_worker is None and voice.stream is None:
            voice.refresh_devices()
        self.home_mic.blockSignals(True)
        self.home_mic.clear()
        for index in range(voice.device_combo.count()):
            self.home_mic.addItem(voice.device_combo.itemText(index), voice.device_combo.itemData(index))
        self.home_mic.setCurrentIndex(voice.device_combo.currentIndex())
        self.home_mic.blockSignals(False)
        self.home_voice.blockSignals(True)
        self.home_voice.clear()
        for index in range(voice.voice_combo.count()):
            self.home_voice.addItem(voice.voice_combo.itemText(index), index)
        self.home_voice.setCurrentIndex(voice.voice_combo.currentIndex())
        self.home_voice.blockSignals(False)
        self.home_voice.setEnabled(load_speech_settings().engine == "system" and voice.voice_combo.isEnabled())
        self.refresh_home_voice_status()

    def select_home_mic(self) -> None:
        if self.voice_window is not None and self.voice_window.device_combo.isEnabled():
            self.voice_window.device_combo.setCurrentIndex(self.home_mic.currentIndex())

    def select_home_voice(self) -> None:
        if self.voice_window is not None and load_speech_settings().engine == "system":
            self.voice_window.voice_combo.setCurrentIndex(self.home_voice.currentIndex())

    def link_voice_owner(self) -> None:
        if new_memory_selected("desktop"):
            return
        try:
            raw = self.voice_owner_qq.text().strip()
            if not raw.isdecimal() or int(raw) <= 0:
                raise ValueError("请输入有效的 QQ 号")
            bot_id = current_bot_id()
            if bot_id is None:
                raise ValueError("尚未取得机器人账号；请先连接 QQ 桥接")
            link = save_link(bot_id, int(raw))
            self.voice_link_feedback.setText(f"已关联 QQ {link.qq_id}。从下一轮语音对话开始共享个人记忆。")
            self._log(f"语音已关联个人 QQ {link.qq_id}")
        except (OSError, ValueError, TypeError) as error:
            self.voice_link_feedback.setText(f"关联失败：{error}")
        self.refresh_home_voice_status()

    def start_home_voice(self) -> None:
        voice = self._ensure_voice()
        if voice.continuous_worker is not None:
            self.open_voice()
            return
        if voice.stream is not None:
            self.voice_link_feedback.setText("请先停止单句麦克风测试。")
            return
        voice.auto_reply.setChecked(True)
        if voice.tts is not None or load_speech_settings().engine == "gpt_sovits":
            voice.speak_reply.setChecked(True)
        voice.start_continuous()
        self.refresh_home_voice_status()
        self._log("已从语音交互页面开启语音监听")

    def stop_home_voice(self) -> None:
        voice = self.voice_window
        if voice is not None and voice.continuous_worker is not None:
            voice.stop_continuous()
        self.refresh_home_voice_status()

    def toggle_home_record(self) -> None:
        voice = self._ensure_voice()
        if voice.continuous_worker is not None:
            self.voice_link_feedback.setText("请先停止持续监听，再做单句测试。")
            return
        voice.toggle_recording()
        self.refresh_home_voice_status()

    def try_home_sample(self) -> None:
        voice = self._ensure_voice()
        if voice.continuous_worker is None and voice.stream is None:
            voice.try_sample()
        self.refresh_home_voice_status()

    def test_home_tts(self) -> None:
        if load_speech_settings().engine == "gpt_sovits" and not _port_open("http://127.0.0.1:9880"):
            self.tts_feedback.setText("请先启动本地语音服务，再做播报测试。")
            return
        voice = self._ensure_voice()
        if not voice.play_test_phrase("欢迎回来，我在这里。"):
            self.tts_feedback.setText("语音正在工作，请等当前录音或播报结束后重试。")
        self.refresh_home_voice_status()

    def refresh_home_voice_status(self) -> None:
        if not hasattr(self, "voice_link_state"):
            return
        if new_memory_selected("desktop"):
            self.voice_link_state.setText("新记忆服务已接管语音；QQ 与桌面身份关联在记忆中心管理。")
        else:
            link = load_link()
            self.voice_link_state.setText(
                f"已连接：QQ{link.qq_id}" if link else
                "尚未建立有效关联。语音目前不会读取或写入 QQ 个人档案。")
        server_running = self.tts_server.state() != QProcess.ProcessState.NotRunning
        service_ready = _port_open("http://127.0.0.1:9880")
        self.tts_service_state.setText("本地服务：已就绪" if service_ready else
                                       ("本地服务：加载模型中" if server_running else "本地服务：未启动"))
        self.tts_service_button.setText("停止本地语音服务" if server_running else "启动本地语音服务")
        voice = self.voice_window
        if voice is None:
            self.home_voice_state.setText("语音未启动 · 可先选设备并测试")
            self.home_stop.setEnabled(False)
            self.home_record.setText("单句麦克风测试")
            return
        active = voice.continuous_worker is not None
        self.home_voice_state.setText(voice.state_label.text() + " · " + voice.status_label.text())
        self.home_stop.setEnabled(active)
        self.home_start.setEnabled(not active and voice.worker is None and voice.stream is None)
        self.home_mic.setEnabled(voice.device_combo.isEnabled())
        self.home_record.setText("停止单句录音" if voice.stream is not None else "单句麦克风测试")
        self.home_record.setEnabled(not active)
        self.home_tts_test.setEnabled(voice.tts is not None and not active)
        if self.home_mic.currentIndex() != voice.device_combo.currentIndex():
            self.home_mic.blockSignals(True)
            self.home_mic.setCurrentIndex(voice.device_combo.currentIndex())
            self.home_mic.blockSignals(False)
        transcript = voice.result_edit.toPlainText()
        if transcript != self.home_transcript.toPlainText():
            self.home_transcript.setPlainText(transcript)
            self.home_transcript.moveCursor(QTextCursor.MoveOperation.End)

    def refresh_logs(self, *, force: bool = False) -> None:
        import_external_events()
        revision, entries = session_snapshot()
        signature = (revision, self.log_filter.currentText(), self.log_search.text())
        if not force and signature == self._log_signature:
            return
        self._log_signature = signature
        source = self.log_filter.currentText()
        query = self.log_search.text().strip().casefold()
        lines = []
        for entry in entries:
            if source != "全部来源" and source not in str(entry["source"]):
                continue
            line = format_event(entry)
            if not query or query in line.casefold():
                lines.append(line)
        if lines != self._log_lines:
            self.log_follow.begin_update()
            if self._log_lines and lines[:len(self._log_lines)] == self._log_lines:
                self.log_view.appendPlainText("\n".join(lines[len(self._log_lines):]))
            else:
                self.log_view.setPlainText("\n".join(lines))
            self._log_lines = lines
            self.log_follow.end_update()
        self.log_count.setText(f"显示 {len(lines)} / {len(entries)} 条 · 本次启动（最多保留 2000 条）")

    @staticmethod
    def _number(value: float, minimum: int, maximum: int, *, decimal: bool = False) -> QSpinBox | QDoubleSpinBox:
        widget = QDoubleSpinBox() if decimal else QSpinBox()
        widget.setRange(minimum, maximum)
        if decimal:
            widget.setDecimals(2)
            widget.setSingleStep(0.1)
        widget.setValue(value if decimal else round(value))
        return widget

    def switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        if index == PAGE_STREAM:
            self.chat_stream.refresh(force=True)
        if index == PAGE_VOICE and self.home_mic.count() == 0:
            self.refresh_home_devices()
        for i, button in enumerate(self.nav):
            button.setProperty("selected", i == index)
            button.style().unpolish(button)
            button.style().polish(button)
        self.refresh_status()

    def save_qq(self) -> bool:
        temporary = QQ_SETTINGS_FILE.with_suffix(".tmp")
        try:
            data = asdict(replace(QqSettings.load(),
                ws_url=self.ws_url.text().strip(),
                allowed_private_ids=frozenset(_ids(self.private_ids.text())),
                initial_l0_private_ids=frozenset(_ids(self.initial_l0_private_ids.text())),
                allowed_group_ids=frozenset(_ids(self.group_ids.text())),
                voice_private_ids=frozenset(_ids(self.voice_private_ids.text())),
                private_cooldown_seconds=self.private_cooldown.value(),
                private_max_reply_parts=self.private_reply_parts.value(),
                group_cooldown_seconds=self.group_cooldown.value(),
                private_max_replies_per_hour=self.private_hourly.value(),
                group_max_replies_per_hour=self.group_hourly.value(),
                quiet_seconds=self.quiet_seconds.value(),
            ))
            for key, value in data.items():
                if isinstance(value, (set, frozenset)):
                    data[key] = sorted(value)
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            QqSettings.load(temporary)
            if self.qq_token.text().strip():
                _save_snowluma_token(self.qq_token.text())
                self.qq_token.clear()
            os.replace(temporary, QQ_SETTINGS_FILE)
            self.token_state.setText("已保存" if load_snowluma_token() else "未设置")
            self.qq_feedback.setText("QQ 配置已保存。运行中的桥接需重启后生效。")
            self.statusBar().showMessage("QQ 配置已保存", 5000)
            self.refresh_status()
            return True
        except (OSError, ValueError, TypeError) as error:
            self.qq_feedback.setText(f"保存失败：{error}")
            return False
        finally:
            temporary.unlink(missing_ok=True)

    def save_proactive(self) -> bool:
        path = DATA_ROOT / "proactive_settings.json"
        temporary = path.with_suffix(".tmp")
        try:
            raw_id = self.proactive_recipient.text().strip()
            if raw_id and (not raw_id.isdecimal() or int(raw_id) <= 0):
                raise ValueError("收件人必须是正整数 QQ 号")
            config = ProactiveConfig(
                enabled=self.proactive_enabled.isChecked(),
                channel=self.proactive_channel.currentData(),
                recipient_id=int(raw_id) if raw_id else 0,
                daily_checkin=self.proactive_checkin.isChecked(),
            )
            config.validate()
            if config.enabled and config.recipient_id not in QqSettings.load().allowed_private_ids:
                raise ValueError("请先把收件人加入 QQ 私聊白名单并保存")
            temporary.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, path)
            self.proactive_feedback.setText("主动互动设置已保存；重启 QQ 桥接后生效。")
            return True
        except (OSError, ValueError, TypeError) as error:
            self.proactive_feedback.setText(f"保存失败：{error}")
            return False
        finally:
            temporary.unlink(missing_ok=True)

    def open_voice(self) -> None:
        voice = self._ensure_voice()
        voice.show()
        voice.raise_()
        voice.activateWindow()
        self.refresh_home_devices()

    def open_chat(self) -> None:
        self.show_console(PAGE_CHAT)

    def open_curated_memory(self) -> None:
        # New curated memory system is independent of the old Mem0 manager.
        from zhuangfy.ui.memory_manager import CuratedMemoryDialog
        if self.curated_memory_window is None:
            self.curated_memory_window = CuratedMemoryDialog(self)
        self.curated_memory_window.show()
        self.curated_memory_window.raise_()
        self.curated_memory_window.activateWindow()

    def open_memory(self) -> None:
        if not legacy_memory_allowed("desktop"):
            self.statusBar().showMessage("新记忆服务已接管；旧版记忆档案暂不开放编辑", 6000)
            return
        if self.memory_window is None:
            from memory_gui import MemoryDialog
            self.memory_window = MemoryDialog(self)
        self.memory_window.show()
        self.memory_window.raise_()
        self.memory_window.activateWindow()

    def open_snowluma_webui(self) -> None:
        if not QDesktopServices.openUrl(SNOWLUMA_WEBUI):
            QMessageBox.warning(self, "无法打开浏览器", "请手动访问 http://127.0.0.1:5099/")

    def toggle_snowluma(self) -> None:
        if self.snowluma.state() != QProcess.ProcessState.NotRunning:
            self._auto_bridge_pending = False
            if self.bridge.state() != QProcess.ProcessState.NotRunning:
                self._stop(self.bridge)
            self._stop(self.snowluma)
            return
        self._auto_bridge_pending = self.save_qq() if self.auto_connect.isChecked() else False
        if _port_open(SNOWLUMA_WEBUI.toString()):
            self.qq_feedback.setText("SnowLuma 已在本机运行；正在检查 OneBot 连接。")
            self.refresh_status()
            return
        executable = SNOWLUMA_DIR / "node.exe"
        script = SNOWLUMA_DIR / "index.mjs"
        if not executable.is_file() or not script.is_file():
            self._auto_bridge_pending = False
            self.qq_feedback.setText(f"未找到 SnowLuma：{SNOWLUMA_DIR}。请先安装，再从控制台启动。")
            return
        self.snowluma.setWorkingDirectory(str(SNOWLUMA_DIR))
        self.snowluma.start(str(executable), [str(script)])
        self._log("正在启动 SnowLuma；等待 OneBot 端口就绪…")
        self.refresh_status()

    def toggle_bridge(self) -> None:
        if _hybrid_preview_active():
            self.qq_feedback.setText("Hybrid 预览独占 QQ 连接；请先退出 Hybrid 再启动旧桥接。")
            return
        self._auto_bridge_pending = False
        if self.bridge.state() != QProcess.ProcessState.NotRunning:
            self._stop(self.bridge)
            return
        if not self.save_qq():
            return
        self._start_bridge()

    def _start_bridge(self) -> None:
        if _hybrid_preview_active():
            return
        if self._closing or self.bridge.state() != QProcess.ProcessState.NotRunning:
            return
        try:
            settings = QqSettings.load()
        except (OSError, ValueError, TypeError) as error:
            self.qq_feedback.setText(f"QQ 配置不可用：{error}")
            return
        if not settings.allowed_private_ids and not settings.allowed_group_ids:
            self.qq_feedback.setText("请先填写至少一个私聊 QQ 号或群号。")
            return
        environment = _drop_memory_admin_secrets(QProcessEnvironment.systemEnvironment())
        environment.insert("PYTHONIOENCODING", "utf-8")
        environment.insert("PYTHONUNBUFFERED", "1")
        if self._event_receiver.localPort():
            environment.insert("ZHUANGFY_EVENT_PORT", str(self._event_receiver.localPort()))
            environment.insert("ZHUANGFY_EVENT_TOKEN", self._event_token)
        self.bridge.setProcessEnvironment(environment)
        self.bridge.setWorkingDirectory(str(ROOT))
        interpreter = Path(sys.executable)
        if interpreter.name.lower() == "pythonw.exe":
            interpreter = interpreter.with_name("python.exe")
        self.bridge.start(str(interpreter), [str(ROOT / "qq_bridge.py")])
        self._log("正在启动 QQ 桥接…")
        self.refresh_status()

    def _stop(self, process: QProcess) -> None:
        if process is self.snowluma and os.name == "nt" and process.processId():
            # QProcess.terminate() only stops the launcher, leaving SnowLuma's
            # Node child and its OneBot port alive. Stop the managed tree.
            try:
                subprocess.run(["taskkill", "/PID", str(process.processId()), "/T", "/F"],
                               capture_output=True, timeout=10, check=False)
                process.waitForFinished(1500)
                if process.state() == QProcess.ProcessState.NotRunning:
                    self.refresh_status()
                    return
            except (OSError, subprocess.TimeoutExpired):
                pass
        process.terminate()
        if not process.waitForFinished(1500):
            process.kill()
            process.waitForFinished(1500)
        self.refresh_status()

    def _log(self, message: str, source: str = "控制台") -> None:
        self.log.appendPlainText(message)
        log_event(source, "服务", message)

    def _read_output(self, process: QProcess) -> None:
        if self._closing:
            return
        for chunk in (process.readAllStandardOutput(), process.readAllStandardError()):
            if chunk:
                if process is self.snowluma:
                    # SnowLuma may print its initial WebUI password at first launch.
                    continue
                message = bytes(chunk).decode('utf-8', errors='replace').strip()
                if "已连接 SnowLuma" in message:
                    self._bridge_connected = True
                elif "连接中断" in message:
                    self._bridge_connected = False
                self._log(f"[{self._managed[process]}] {message}", self._managed[process])

    def _process_error(self, process: QProcess) -> None:
        if self._closing:
            return
        self._log(f"[{self._managed[process]}] 启动或运行失败：{process.errorString()}")
        self.refresh_status()

    def _process_finished(self, process: QProcess) -> None:
        if self._closing:
            return
        self._read_output(process)
        self._log(f"[{self._managed[process]}] 已停止")
        if process is self.bridge:
            self._bridge_connected = False
        if process is self.snowluma and not _port_open(SNOWLUMA_WEBUI.toString()):
            self._auto_bridge_pending = False
        self.refresh_status()

    def refresh_status(self) -> None:
        self.refresh_home_voice_status()
        voice = self.voice_window
        voice_active = bool(voice is not None and
                            (voice.continuous_worker is not None or voice.stream is not None))
        heartbeat = Paths.current().project_data_root / "voice_activity.json"
        try:
            heartbeat.write_text(json.dumps({"active": voice_active, "at": time.time()}), encoding="utf-8")
        except OSError:
            pass
        settings = load_settings()
        reply_candidates = [candidate for candidate in load_routes().reply.candidates
                            if candidate.enabled]
        if reply_candidates:
            primary = reply_candidates[0]
            self.model_state.setText(f"回复模型：{primary.model}")
            has_key = (primary.provider == "custom" or
                       bool(load_route_key(primary.id, primary.provider, primary.base_url)))
        else:
            self.model_state.setText(f"回复模型：{settings.model}")
            has_key = bool(load_key(settings.provider))
        self.api_state.setText("API Key：已保存" if has_key else "API Key：未保存")
        self.memory_state.setText(
            "旧版长期记忆：已停用（新服务接管）" if new_memory_selected("desktop") else
            "旧版长期记忆：已开启" if settings.memory_enabled else "旧版长期记忆：已关闭")
        memory_online = _port_open("http://127.0.0.1:8769")
        tts_online = _port_open("http://127.0.0.1:9880")
        self.memory_core_state.setText("新记忆服务：可连接" if memory_online else "新记忆服务：未连接")
        self.memory_service_state.setText("服务状态：可连接" if memory_online else "服务状态：未连接")
        self.tts_home_state.setText("GPT-SoVITS：可连接" if tts_online else "GPT-SoVITS：未连接")
        snow_online = _port_open(SNOWLUMA_WEBUI.toString())
        onebot_online = _port_open(self.ws_url.text() if hasattr(self, "ws_url") else QqSettings().ws_url)
        bridge_managed = self.bridge.state() != QProcess.ProcessState.NotRunning
        if self._auto_bridge_pending and onebot_online:
            self._auto_bridge_pending = False
            if not bridge_managed:
                QTimer.singleShot(0, self._start_bridge)
        self.snow_state.setText("SnowLuma WebUI：可连接" if snow_online else "SnowLuma WebUI：未连接")
        self.onebot_state.setText("OneBot 端口：可连接" if onebot_online else "OneBot 端口：未连接")
        self.bridge_state.setText("QQ 桥接：Hybrid 预览由独立进程管理" if _hybrid_preview_active() else
                                  "QQ 桥接：已连接" if self._bridge_connected else
                                  ("QQ 桥接：运行中，等待登录/连接" if bridge_managed else "QQ 桥接：未由控制台运行"))
        progress = "   ·   等待 OneBot 后自动连接" if self._auto_bridge_pending else ""
        self.service_state.setText(f"WebUI {'可连接' if snow_online else '未连接'}   ·   OneBot 端口 {'可连接' if onebot_online else '未连接'}{progress}")
        self.snow_button.setText("停止 SnowLuma" if self.snowluma.state() != QProcess.ProcessState.NotRunning else "启动 SnowLuma")
        self.bridge_button.setText("停止 QQ 桥接" if bridge_managed else "启动 QQ 桥接")
        plugin_status = {
            "snowluma": "已连接 · OneBot 可用" if snow_online and onebot_online else
                         ("WebUI 可连接 · OneBot 未就绪" if snow_online else "未连接"),
            "qq_bridge": "已连接" if self._bridge_connected else
                         ("运行中 · 等待连接" if bridge_managed else "未运行"),
            "sensevoice": "模型目录已就绪 · 随语音窗口运行" if Paths.current().models_root.is_dir() else "模型目录未找到",
            "gpt_sovits": "本地服务运行中" if tts_online else "本地服务未连接",
            "memory": "本机服务运行中" if memory_online else "本机服务未连接",
        }
        for name, label in self.plugin_states.items():
            label.setText(plugin_status[name])
        running = any((snow_online, onebot_online, bridge_managed, tts_online, memory_online))
        self.sidebar_service.setText("服务运行中" if running else "服务未连接")
        if self.service_dot.property("online") != running:
            self.service_dot.setProperty("online", running)
            self.service_dot.style().unpolish(self.service_dot)
            self.service_dot.style().polish(self.service_dot)
        _, events = session_snapshot()
        self.recent_overview.setText("\n".join(format_event(entry)[:145] for entry in events[-3:])
                                     if events else "本次启动尚无记录")

    def closeEvent(self, event) -> None:
        if self.window_resize_timer.isActive():
            self.window_resize_timer.stop()
            self.save_custom_window_size()
        if not self._really_quit and self.tray is not None and self.tray.isVisible():
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self.tray.showMessage("庄方宜仍在运行", "控制台已收起到系统托盘；从托盘菜单可以重新打开或退出。")
                self._tray_hint_shown = True
            return
        self._closing = True
        self.chat_page.shutdown()
        self.timer.stop()
        self.log_timer.stop()
        for process in (self.bridge, self.snowluma, self.tts_server):
            if process.state() != QProcess.ProcessState.NotRunning:
                self._stop(process)
        self._log("控制台退出")
        super().closeEvent(event)


STYLE = """
QWidget#root, QWidget#content, QScrollArea, QStackedWidget#pages { background: #f4f6fa; color: #263247; }
QWidget#streamPage, QWidget#streamMain, QWidget#streamContent, QScrollArea#streamScroll { background: #f8f9fc; color: #263247; }
QWidget { font-family: 'Microsoft YaHei UI'; font-size: 13px; }
QFrame#sidebar { background: #192338; color: #eef2fa; }
QFrame#sidebar QLabel { color: #c4cede; background: transparent; }
QFrame#sidebar QLabel#brand { color: white; font-size: 25px; font-weight: 700; }
QFrame#sidebar QLabel#subbrand { color: #8197b8; font-size: 10px; letter-spacing: 1px; }
QFrame#sidebar QLabel#navGroup { color: #8799b7; font-size: 10px; font-weight: 700; padding-left: 12px; }
QFrame#sidebar QLabel#sidebarService { color: #cbd7e9; font-size: 12px; }
QFrame#sidebar QLabel#serviceDot { color: #70819e; font-size: 16px; }
QFrame#sidebar QLabel#serviceDot[online="true"] { color: #56d79b; }
QFrame#sidebar QPushButton { background: transparent; color: #d3dbea; border: 0; border-radius: 9px; text-align: left; padding: 11px 13px; }
QFrame#sidebar QPushButton:hover, QFrame#sidebar QPushButton[selected="true"] { background: #2d3d5b; color: white; }
QLabel#pageTitle { font-size: 26px; font-weight: 700; color: #1e2a40; }
QLabel#cardTitle { font-size: 17px; font-weight: 700; color: #243149; }
QLabel#muted { color: #66758b; }
QFrame#card { background: white; border: 1px solid #e1e7f0; border-radius: 14px; }
QFrame#card QLabel, QFrame#card QCheckBox { background: transparent; }
QFrame#streamRail { background: #edf1f8; border-right: 1px solid #dbe3ef; }
QLabel#streamRailTitle, QLabel#streamMainTitle { color: #20304a; font-size: 20px; font-weight: 700; }
QLabel#streamRailCaption { color: #697b93; font-size: 11px; font-weight: 700; }
QFrame#streamRole { background: white; border: 1px solid #dbe4f1; border-radius: 11px; }
QFrame#streamRole QLabel { border: 0; background: transparent; }
QLabel#streamRoleName { color: #253653; font-size: 14px; font-weight: 700; }
QLabel#streamRoleHint { color: #8492a6; font-size: 11px; }
QLineEdit#streamSearch { background: white; border: 1px solid #d8e1ee; border-radius: 8px; padding: 7px; }
QListWidget#streamConversations { background: transparent; border: 0; outline: 0; color: #344760; }
QListWidget#streamConversations::item { padding: 11px 8px; border-radius: 8px; margin-bottom: 3px; }
QListWidget#streamConversations::item:hover { background: #e1e8f5; }
QListWidget#streamConversations::item:selected { background: #dce6f7; color: #23416a; }
QPushButton#streamJump { background: white; border: 1px solid #dce5f1; border-radius: 8px; color: #557195; padding: 7px 10px; }
QFrame#streamTurn { background: transparent; border-bottom: 1px solid #dfe6f0; }
QFrame#streamTurn QLabel, QFrame#streamThinking QLabel, QFrame#streamUserBubble QLabel, QFrame#streamAssistantBubble QLabel { background: transparent; border: 0; }
QLabel#streamSpeakerName { color: #263d5b; font-size: 12px; font-weight: 700; }
QLabel#streamSpeakerAvatar { background: #dfe7f3; color: #496684; border-radius: 13px; min-width: 27px; min-height: 27px; max-width: 27px; max-height: 27px; font-size: 11px; font-weight: 700; }
QFrame#streamUserBubble { background: #e8eef9; border: 1px solid #d8e3f4; border-radius: 10px; }
QFrame#streamAssistantBubble { background: white; border: 1px solid #e1e7f0; border-radius: 10px; }
QLabel#streamIncoming, QLabel#streamReply { color: #263247; font-size: 13px; }
QLabel#streamSection { color: #32788a; font-size: 12px; font-weight: 700; }
QLabel#streamDecision { color: #7b8d9f; font-size: 11px; }
QLabel#streamPending, QLabel#streamEmpty { color: #8491a5; }
QFrame#streamThinking { background: #f1f7f6; border: 1px solid #d7e9e6; border-radius: 9px; }
QLabel#streamPreview { color: #58737a; }
QPushButton#streamToggle { border: 0; background: transparent; color: #3d8a8a; padding: 2px 4px; }
QPlainTextEdit#streamFull { background: #fbfefe; border: 1px solid #d9e8e6; color: #3d5962; }
QPlainTextEdit#consoleView { background: #0b1013; color: #d5e0e3; border: 1px solid #24363b; border-radius: 7px; padding: 11px; selection-background-color: #264954; }
QPlainTextEdit#consoleView:focus { border-color: #3c6871; }
QFrame#heroCard { background: #e9eefb; border: 1px solid #d5dff4; border-radius: 16px; }
QFrame#heroCard QLabel { background: transparent; }
QLabel#pluginStatus { color: #4c617b; font-weight: 600; }
QWidget#chatPage, QWidget#chatContent { background: #f8fafd; }
QFrame#chatRail { background: #eef2f8; border-right: 1px solid #dce4ef; }
QLabel#chatRailTitle { font-size: 21px; font-weight: 700; color: #213149; }
QListWidget#chatList { background: transparent; border: 0; outline: 0; color: #31415b; }
QListWidget#chatList::item { padding: 12px 10px; border-radius: 8px; margin-bottom: 3px; }
QListWidget#chatList::item:hover { background: #e2e9f6; }
QListWidget#chatList::item:selected { background: #d4dff6; color: #23375b; }
QScrollArea#chatScroll, QWidget#chatMessages { background: #f8fafd; }
QFrame#chatRow { background: transparent; border: 0; }
QFrame#chatBubble { background: white; border: 1px solid #e2e8f1; border-radius: 13px; }
QFrame#chatBubble[speaker="user"] { background: #e3eafa; border-color: #d3def4; }
QFrame#chatBubble QLabel { background: transparent; color: #263247; }
QLabel#bubbleName { color: #64758e; font-size: 11px; font-weight: 700; }
QLabel#chatWelcome { color: #8795aa; font-size: 17px; }
QFrame#composerBox { background: white; border: 1px solid #dbe3ee; border-radius: 13px; }
QFrame#composerBox QPlainTextEdit { border: 0; background: transparent; }
QLineEdit, QComboBox, QPlainTextEdit, QSpinBox { background: #fbfcfe; color: #243149; border: 1px solid #cdd7e5; border-radius: 7px; padding: 7px; selection-background-color: #7386db; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: #6274cb; }
QPushButton { background: #edf1f8; color: #30405d; border: 1px solid #dce4ef; border-radius: 8px; padding: 9px 15px; font-weight: 600; }
QPushButton:hover { background: #e2e9f6; }
QPushButton[primary="true"] { background: #5269c8; color: white; border-color: #5269c8; }
QPushButton[primary="true"]:hover { background: #4259b6; }
QStatusBar { background: #f4f6fa; color: #66758b; }
"""


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setWindowIcon(app_icon())
    # Qt's offscreen plugin and some minimal Windows installations do not
    # discover the system's CJK fonts automatically.
    font_file = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"
    if font_file.is_file():
        QFontDatabase.addApplicationFont(str(font_file))
    window = Dashboard()
    window.show()
    if "--arm-demo" in sys.argv:
        window._ensure_voice().one_shot_enabled.setChecked(True)
    if "--start-services" in sys.argv:
        QTimer.singleShot(0, window.toggle_snowluma)
    if "--start-tts" in sys.argv or load_speech_settings().engine == "gpt_sovits":
        QTimer.singleShot(0, window.start_tts_service)
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
