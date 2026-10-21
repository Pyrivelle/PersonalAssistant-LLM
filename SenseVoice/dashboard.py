"""Local control center for the existing voice, LLM, memory and QQ modules."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer, QUrl, QUrlQuery
from PySide6.QtGui import QAction, QDesktopServices, QFont, QFontDatabase, QTextCursor
from PySide6.QtNetwork import QHostAddress, QNetworkAccessManager, QNetworkReply, QNetworkRequest, QUdpSocket
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox, QDoubleSpinBox, QStackedWidget, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)

from activity_log import format_event, log_event, receive_event, session_snapshot
from branding import APP_NAME, app_icon
from llm import (
    DEFAULT_PROMPT, LlmSettings, PROVIDERS, PROVIDER_BASE_URLS,
    PROVIDER_DEFAULT_MODELS, PROVIDER_SUGGESTED_MODELS,
    load_key, load_settings, save_key, save_settings,
)
from model_catalog import ModelCatalogWorker
from persona_packs import BUILTIN_PACKS, matching_pack
from owner_memory import current_bot_id, load_link, save_link
from gpt_sovits import (ENGINE_ROOT, SpeechSettings, available_models, save_model_config,
                        load_settings as load_speech_settings, save_settings as save_speech_settings)
from qq_bridge import QqSettings, SETTINGS_FILE as QQ_SETTINGS_FILE, load_snowluma_token


ROOT = Path(__file__).resolve().parent
SNOWLUMA_WEBUI = QUrl("http://127.0.0.1:5099/")
SNOWLUMA_DIR = ROOT.parents[1] / "SnowLuma" / "v2.0.3"


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
    path = ROOT / ".env.local"
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


class Dashboard(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} · 控制台")
        self.resize(1120, 760)
        self.setMinimumSize(840, 590)
        self.voice_window = None
        self.chat_window = None
        self.memory_window = None
        self._model_worker = None
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
        side.setContentsMargins(18, 28, 18, 22)
        side.setSpacing(9)
        brand = QLabel("庄方宜")
        brand.setObjectName("brand")
        side.addWidget(brand)
        subbrand = QLabel("ZhuangFY  /  CONTROL")
        subbrand.setObjectName("subbrand")
        side.addWidget(subbrand)
        side.addSpacing(36)
        self.nav = []
        for name in ("总览", "模型与人格", "QQ · SnowLuma", "运行过程", "语音交互"):
            button = _button(name, lambda _checked=False, i=len(self.nav): self.switch_page(i))
            button.setObjectName("nav")
            side.addWidget(button)
            self.nav.append(button)
        side.addSpacing(12)
        side.addWidget(QLabel("快捷入口"))
        side.addWidget(_button("打开语音窗口  ↗", self.open_voice))
        side.addWidget(_button("文字对话测试  ↗", self.open_chat))
        side.addWidget(_button("长期记忆管理  ↗", self.open_memory))
        side.addWidget(_button("重启连接  ↻", self.restart_connections))
        side.addStretch()
        shell.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        shell.addWidget(self.pages, 1)
        self.pages.addWidget(self._overview())
        self.pages.addWidget(self._model_page())
        self.pages.addWidget(self._qq_page())
        self.pages.addWidget(self._logs_page())
        self.pages.addWidget(self._home_voice_page())
        self.switch_page(0)
        self.setStyleSheet(STYLE)
        self.statusBar().showMessage("控制台就绪")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start(3000)
        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self.refresh_logs)
        self.log_timer.start(1500)
        self._setup_tray()
        self.refresh_status()
        self._log("控制台已启动")
        self.refresh_logs()

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
                                ("语音交互", lambda: self.show_console(4)),
                                ("查看运行过程", lambda: self.show_console(3)),
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
        for window in (self.voice_window, self.chat_window, self.memory_window):
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
        if received and self.pages.currentIndex() == 3:
            self.refresh_logs()

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
        page, column = self._page("今天，和小庄聊聊。", "从这里查看各模块状态，进入语音对话，或者配置消息连接。")
        hero, layout = _card("语音对话", "本机 SenseVoice 识别 + 唤醒监听 + 模型回复 + 分段语音播报。")
        actions = QHBoxLayout()
        actions.addWidget(_button("打开语音窗口", self.open_voice, primary=True))
        actions.addWidget(_button("语音交互", lambda: self.switch_page(4)))
        actions.addWidget(_button("文字测试", self.open_chat))
        actions.addStretch()
        layout.addLayout(actions)
        column.addWidget(hero)

        row = QHBoxLayout()
        model_card, model_layout = _card("模型与记忆")
        self.model_state = QLabel()
        model_layout.addWidget(self.model_state)
        self.api_state = QLabel()
        model_layout.addWidget(self.api_state)
        self.memory_state = QLabel()
        model_layout.addWidget(self.memory_state)
        model_layout.addWidget(_button("配置模型与人格  →", lambda: self.switch_page(1)))
        row.addWidget(model_card)
        qq_card, qq_layout = _card("QQ 连接")
        self.snow_state = QLabel()
        qq_layout.addWidget(self.snow_state)
        self.onebot_state = QLabel()
        qq_layout.addWidget(self.onebot_state)
        self.bridge_state = QLabel()
        qq_layout.addWidget(self.bridge_state)
        qq_layout.addWidget(_button("配置 SnowLuma  →", lambda: self.switch_page(2)))
        row.addWidget(qq_card)
        column.addLayout(row)
        tip, tip_layout = _card("当前工作方式", "语音窗口负责麦克风与 TTS；QQ 桥接只响应白名单中的文字消息。SnowLuma 保持独立安装，配置页面由它自己的 WebUI 提供。")
        column.addWidget(tip)
        column.addStretch()
        return page

    def _model_page(self) -> QScrollArea:
        page, column = self._page("模型与人格", "这里的设置会被语音窗口、文字测试窗口和 QQ 桥接在下一次使用时读取。运行中的 QQ 桥接需要重启才会切换配置。")
        saved = load_settings()
        self._provider_values = {saved.provider: (saved.base_url, saved.model)}
        self._model_choices = {}
        self._last_provider = saved.provider
        card, layout = _card("默认模型（兼容单 API 设置）", "只有对应阶段没有配置模型候选时才使用这里的供应商与模型。人格与记忆仍使用下方原有设置。")
        form = QFormLayout()
        form.setSpacing(12)
        self.provider = QComboBox()
        for code, label in PROVIDERS.items():
            self.provider.addItem(label, code)
        self.provider.setCurrentIndex(self.provider.findData(saved.provider))
        self.provider.currentIndexChanged.connect(self.provider_changed)
        form.addRow("供应商", self.provider)
        self.api_url = _line("API Base URL", saved.base_url)
        self.api_url.setPlaceholderText("输入供应商提供的 API Base URL，例如 https://example.com/v1")
        form.addRow("API 地址", self.api_url)
        self.model_id = QComboBox()
        self.model_id.setEditable(True)
        self.model_id.setMaxVisibleItems(16)
        self.model_id.addItems(PROVIDER_SUGGESTED_MODELS[saved.provider])
        self.model_id.setCurrentText(saved.model)
        self.model_id.setAccessibleName("模型 ID")
        self.model_id.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.model_id.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_id, 1)
        self.fetch_button = _button("获取模型列表", self.fetch_models)
        model_row.addWidget(self.fetch_button)
        form.addRow("模型 ID", model_row)
        self.catalog_state = QLabel("可手动输入模型 ID；点击获取列表后可从下拉框选择。")
        self.catalog_state.setObjectName("muted")
        form.addRow("模型列表", self.catalog_state)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("留空则继续使用本机已保存的密钥")
        form.addRow("API Key", self.api_key)
        self.key_state = QLabel()
        self.key_state.setObjectName("muted")
        form.addRow("密钥状态", self.key_state)
        layout.addLayout(form)
        column.addWidget(card)
        routes_card, routes_layout = _card(
            "分阶段模型 API 与容灾",
            "思考、回复、识图可分别配置多条 API；按优先级故障切换，支持单次超时和重试。"
            "识图目前仅配置备用服务，尚未接入图片输入。")
        routes_layout.addWidget(_button(
            "配置各阶段的 API 候选、密钥及重试策略  →",
            self.open_model_routes, primary=True))
        column.addWidget(routes_card)

        persona, persona_layout = _card("人格与长期记忆", "可编辑庄方宜的角色提示词。长期记忆启用后，记忆提取与向量化会使用硅基流动 API。")
        pack_row = QHBoxLayout()
        self.persona_pack = QComboBox()
        for pack in BUILTIN_PACKS:
            self.persona_pack.addItem(pack.name, pack)
        pack_row.addWidget(self.persona_pack, 1)
        pack_row.addWidget(_button("载入人格包", self.load_persona_pack))
        persona_layout.addLayout(pack_row)
        self.prompt = QPlainTextEdit(saved.system_prompt or DEFAULT_PROMPT)
        self.prompt.setMinimumHeight(175)
        persona_layout.addWidget(self.prompt)
        self.persona_state = QLabel()
        self.persona_state.setObjectName("muted")
        self.prompt.textChanged.connect(self.update_persona_state)
        persona_layout.addWidget(self.persona_state)
        self.update_persona_state()
        self.memory_enabled = QCheckBox("启用长期记忆")
        self.memory_enabled.setChecked(saved.memory_enabled)
        persona_layout.addWidget(self.memory_enabled)
        buttons = QHBoxLayout()
        buttons.addWidget(_button("保存模型设置", self.save_model, primary=True))
        buttons.addWidget(_button("管理记忆  ↗", self.open_memory))
        buttons.addStretch()
        persona_layout.addLayout(buttons)
        self.model_feedback = QLabel("密钥只存于本机 .env.local，不会在界面回显。")
        self.model_feedback.setObjectName("muted")
        persona_layout.addWidget(self.model_feedback)
        column.addWidget(persona)
        column.addStretch()
        self.provider_changed()
        return page

    def open_model_routes(self) -> None:
        from model_routes_gui import ModelRoutesDialog
        try:
            ModelRoutesDialog(self).exec()
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, "API 路由配置无效", str(error))

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

    def _qq_page(self) -> QScrollArea:
        page, column = self._page("QQ · SnowLuma", "WebUI 用于登录与配置；OneBot WebSocket 用于 QQ 消息收发。启动 SnowLuma 后，控制台可以自动等待 OneBot 就绪并连接桥接。")
        service, layout = _card("服务控制", "首次使用仍须在 SnowLuma WebUI 登录机器人 QQ，并开启 OneBot WebSocket 服务端。")
        self.service_state = QLabel()
        layout.addWidget(self.service_state)
        row = QHBoxLayout()
        self.snow_button = _button("启动 SnowLuma", self.toggle_snowluma, primary=True)
        self.bridge_button = _button("启动 QQ 桥接", self.toggle_bridge)
        row.addWidget(self.snow_button)
        row.addWidget(_button("打开 SnowLuma 配置页面  ↗", self.open_snowluma_webui))
        row.addWidget(self.bridge_button)
        row.addStretch()
        layout.addLayout(row)
        self.auto_connect = QCheckBox("启动 SnowLuma 后自动连接 OneBot 并运行 QQ 桥接")
        self.auto_connect.setChecked(True)
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
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(250)
        self.log.setMinimumHeight(115)
        self.log.setPlaceholderText("由控制台启动的服务日志会显示在这里（不显示密钥）")
        column.addWidget(self.log)
        column.addStretch()
        return page

    def _logs_page(self) -> QScrollArea:
        page, column = self._page(
            "运行过程", "查看本次启动的语音、文字、QQ 与模型调用。这里的“思考”是规划器返回的表达策略，不是模型内部推理。")
        card, layout = _card("本次启动的对话与调用", "内容只保存在当前程序内存中，退出后清空；新运行不写入日志文件。")
        controls = QHBoxLayout()
        self.log_filter = QComboBox()
        self.log_filter.addItems(("全部来源", "控制台", "语音", "文字", "QQ", "模型", "记忆", "SnowLuma"))
        self.log_filter.currentIndexChanged.connect(lambda _index: self.refresh_logs(force=True))
        controls.addWidget(QLabel("来源"))
        controls.addWidget(self.log_filter)
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("搜索消息、回复或状态")
        self.log_search.textChanged.connect(lambda _text: self.refresh_logs(force=True))
        controls.addWidget(self.log_search, 1)
        layout.addLayout(controls)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.log_view.setMinimumHeight(370)
        self.log_view.setPlaceholderText("操作、消息、模型调用与回复将按时间出现在这里。")
        layout.addWidget(self.log_view)
        bottom = QHBoxLayout()
        self.log_count = QLabel()
        bottom.addWidget(self.log_count)
        bottom.addStretch()
        bottom.addWidget(_button("刷新", lambda: self.refresh_logs(force=True)))
        bottom.addWidget(_button("复制当前视图", lambda: QApplication.clipboard().setText(self.log_view.toPlainText())))
        layout.addLayout(bottom)
        column.addWidget(card)
        column.addStretch()
        self._log_signature = None
        return page

    def _home_voice_page(self) -> QScrollArea:
        page, column = self._page("语音交互", "")
        link_card, link_layout = _card("同一个人，同一份记忆")
        self.voice_link_state = QLabel()
        self.voice_link_state.setWordWrap(True)
        link_layout.addWidget(self.voice_link_state)
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
        self.tts_server.setProcessEnvironment(_tts_process_environment(base_home))
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
        scrollbar = self.log_view.verticalScrollBar()
        at_end = scrollbar.value() >= scrollbar.maximum() - 3
        old_position = scrollbar.value()
        self.log_view.setPlainText("\n".join(lines))
        scrollbar.setValue(scrollbar.maximum() if at_end else min(old_position, scrollbar.maximum()))
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
        if index == 4 and self.home_mic.count() == 0:
            self.refresh_home_devices()
        for i, button in enumerate(self.nav):
            button.setProperty("selected", i == index)
            button.style().unpolish(button)
            button.style().polish(button)
        self.refresh_status()

    def provider_changed(self) -> None:
        provider = self.provider.currentData()
        if provider != self._last_provider:
            self._provider_values[self._last_provider] = (self.api_url.text(), self.model_id.currentText())
            url, model = self._provider_values.get(provider, (
                PROVIDER_BASE_URLS.get(provider, "http://localhost:11434/v1"),
                PROVIDER_DEFAULT_MODELS[provider],
            ))
            self.api_url.setText(url)
            self._set_model_choices(self._model_choices.get(provider, PROVIDER_SUGGESTED_MODELS[provider]), model)
            self.catalog_state.setText("可手动输入模型 ID；点击获取列表后可从下拉框选择。")
            self._last_provider = provider
        self.api_url.setReadOnly(provider in PROVIDER_BASE_URLS)
        self.key_state.setText("已保存" if load_key(provider) else "未保存")

    def _set_model_choices(self, choices: list[str], selected: str) -> None:
        self.model_id.clear()
        self.model_id.addItems(choices)
        self.model_id.setCurrentText(selected)

    def fetch_models(self) -> None:
        if self._model_worker is not None:
            return
        try:
            settings = LlmSettings(
                self.provider.currentData(), self.api_url.text(),
                self.model_id.currentText().strip() or "_",
                self.prompt.toPlainText(), self.memory_enabled.isChecked(),
            ).validated()
            key = self.api_key.text().strip() or load_key(settings.provider)
            if settings.provider not in {"custom", "hachimi"} and not key:
                raise ValueError("请先填写或保存当前供应商的 API Key")
            worker = ModelCatalogWorker(settings, key)
            self._model_worker = worker
            self.fetch_button.setEnabled(False)
            self.catalog_state.setText("正在从供应商获取模型列表…")
            worker.loaded.connect(lambda choices, p=settings.provider, u=settings.base_url: self._models_loaded(p, u, choices))
            worker.failed.connect(lambda message: self.catalog_state.setText(message))
            worker.finished.connect(self._models_finished)
            worker.start()
        except (OSError, ValueError) as error:
            self.catalog_state.setText(str(error))

    def _models_loaded(self, provider: str, url: str, choices: list[str]) -> None:
        self._model_choices[provider] = choices
        if self.provider.currentData() != provider or self.api_url.text().rstrip("/") != url:
            return
        selected = self.model_id.currentText()
        self._set_model_choices(choices, selected)
        note = "当前模型 ID 不在列表中；请检查后再使用。" if selected and selected not in choices else "列表可能含非对话模型。"
        if provider == "hachimi" and not (self.api_key.text().strip() or load_key("hachimi")):
            note += " 这是公开目录，实际权限以账号为准。"
        self.catalog_state.setText(f"已获取 {len(choices)} 个模型；{note}")
        self.model_id.showPopup()

    def _models_finished(self) -> None:
        worker = self._model_worker
        self._model_worker = None
        self.fetch_button.setEnabled(True)
        if worker is not None:
            worker.deleteLater()

    def save_model(self) -> bool:
        try:
            settings = LlmSettings(
                self.provider.currentData(), self.api_url.text(), self.model_id.currentText(),
                self.prompt.toPlainText(), self.memory_enabled.isChecked(),
            ).validated()
            if settings.memory_enabled and not (load_key("siliconflow") or
                                                (settings.provider == "siliconflow" and self.api_key.text().strip())):
                raise ValueError("启用长期记忆前，请先保存硅基流动 API Key")
            if self.api_key.text().strip():
                save_key(settings.provider, self.api_key.text())
                self.api_key.clear()
            save_settings(settings)
            self.key_state.setText("已保存" if load_key(settings.provider) else "未保存")
            self.model_feedback.setText("已保存。语音窗口下一轮读取新设置；QQ 桥接需重启。")
            self.statusBar().showMessage("模型设置已保存", 5000)
            self.refresh_status()
            return True
        except (OSError, ValueError) as error:
            self.model_feedback.setText(f"保存失败：{error}")
            return False

    def save_qq(self) -> bool:
        temporary = QQ_SETTINGS_FILE.with_suffix(".tmp")
        try:
            data = asdict(QqSettings(
                ws_url=self.ws_url.text().strip(),
                allowed_private_ids=frozenset(_ids(self.private_ids.text())),
                allowed_group_ids=frozenset(_ids(self.group_ids.text())),
                voice_private_ids=frozenset(_ids(self.voice_private_ids.text())),
                private_cooldown_seconds=self.private_cooldown.value(),
                private_max_reply_parts=self.private_reply_parts.value(),
                group_cooldown_seconds=self.group_cooldown.value(),
                private_max_replies_per_hour=self.private_hourly.value(),
                group_max_replies_per_hour=self.group_hourly.value(),
                quiet_seconds=self.quiet_seconds.value(),
            ))
            data["allowed_private_ids"] = sorted(data["allowed_private_ids"])
            data["allowed_group_ids"] = sorted(data["allowed_group_ids"])
            data["voice_private_ids"] = sorted(data["voice_private_ids"])
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

    def open_voice(self) -> None:
        voice = self._ensure_voice()
        voice.show()
        voice.raise_()
        voice.activateWindow()
        self.refresh_home_devices()

    def open_chat(self) -> None:
        if self.chat_window is None:
            from llm_gui import ChatWindow
            self.chat_window = ChatWindow()
        self.chat_window.show()
        self.chat_window.raise_()
        self.chat_window.activateWindow()

    def open_memory(self) -> None:
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
        self._auto_bridge_pending = False
        if self.bridge.state() != QProcess.ProcessState.NotRunning:
            self._stop(self.bridge)
            return
        if not self.save_qq():
            return
        self._start_bridge()

    def _start_bridge(self) -> None:
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
        environment = QProcessEnvironment.systemEnvironment()
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
        settings = load_settings()
        self.model_state.setText(f"模型：{settings.model}")
        self.api_state.setText("API Key：已保存" if load_key(settings.provider) else "API Key：未保存")
        self.memory_state.setText("长期记忆：已开启" if settings.memory_enabled else "长期记忆：已关闭")
        snow_online = _port_open(SNOWLUMA_WEBUI.toString())
        onebot_online = _port_open(self.ws_url.text() if hasattr(self, "ws_url") else QqSettings().ws_url)
        bridge_managed = self.bridge.state() != QProcess.ProcessState.NotRunning
        if self._auto_bridge_pending and onebot_online:
            self._auto_bridge_pending = False
            if not bridge_managed:
                QTimer.singleShot(0, self._start_bridge)
        self.snow_state.setText("SnowLuma WebUI：可连接" if snow_online else "SnowLuma WebUI：未连接")
        self.onebot_state.setText("OneBot 端口：可连接" if onebot_online else "OneBot 端口：未连接")
        self.bridge_state.setText("QQ 桥接：已连接" if self._bridge_connected else
                                  ("QQ 桥接：运行中，等待登录/连接" if bridge_managed else "QQ 桥接：未由控制台运行"))
        progress = "   ·   等待 OneBot 后自动连接" if self._auto_bridge_pending else ""
        self.service_state.setText(f"WebUI {'可连接' if snow_online else '未连接'}   ·   OneBot 端口 {'可连接' if onebot_online else '未连接'}{progress}")
        self.snow_button.setText("停止 SnowLuma" if self.snowluma.state() != QProcess.ProcessState.NotRunning else "启动 SnowLuma")
        self.bridge_button.setText("停止 QQ 桥接" if bridge_managed else "启动 QQ 桥接")

    def closeEvent(self, event) -> None:
        if not self._really_quit and self.tray is not None and self.tray.isVisible():
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self.tray.showMessage("庄方宜仍在运行", "控制台已收起到系统托盘；从托盘菜单可以重新打开或退出。")
                self._tray_hint_shown = True
            return
        self._closing = True
        self.timer.stop()
        self.log_timer.stop()
        for process in (self.bridge, self.snowluma, self.tts_server):
            if process.state() != QProcess.ProcessState.NotRunning:
                self._stop(process)
        self._log("控制台退出")
        super().closeEvent(event)


STYLE = """
QWidget#root, QWidget#content, QScrollArea, QStackedWidget#pages { background: #f4f6fa; color: #263247; }
QWidget { font-family: 'Microsoft YaHei UI'; font-size: 13px; }
QFrame#sidebar { background: #192338; color: #eef2fa; }
QFrame#sidebar QLabel { color: #c4cede; background: transparent; }
QFrame#sidebar QLabel#brand { color: white; font-size: 25px; font-weight: 700; }
QFrame#sidebar QLabel#subbrand { color: #8197b8; font-size: 10px; letter-spacing: 1px; }
QFrame#sidebar QLabel#sideNote { color: #8197b8; font-size: 11px; }
QFrame#sidebar QPushButton { background: transparent; color: #d3dbea; border: 0; border-radius: 9px; text-align: left; padding: 11px 13px; }
QFrame#sidebar QPushButton:hover, QFrame#sidebar QPushButton[selected="true"] { background: #2d3d5b; color: white; }
QLabel#pageTitle { font-size: 26px; font-weight: 700; color: #1e2a40; }
QLabel#cardTitle { font-size: 17px; font-weight: 700; color: #243149; }
QLabel#muted { color: #66758b; }
QFrame#card { background: white; border: 1px solid #e1e7f0; border-radius: 14px; }
QFrame#card QLabel, QFrame#card QCheckBox { background: transparent; }
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
