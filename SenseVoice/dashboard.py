"""Local control center for the existing voice, LLM, memory and QQ modules."""

from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox, QDoubleSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from llm import (
    DEFAULT_PROMPT, LlmSettings, PROVIDERS, PROVIDER_BASE_URLS,
    PROVIDER_DEFAULT_MODELS, PROVIDER_SUGGESTED_MODELS,
    load_key, load_settings, save_key, save_settings,
)
from model_catalog import ModelCatalogWorker
from persona_packs import BUILTIN_PACKS, matching_pack
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


class Dashboard(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Shimano · 控制台")
        self.resize(1120, 760)
        self.setMinimumSize(840, 590)
        self.voice_window = None
        self.chat_window = None
        self.memory_window = None
        self._model_worker = None
        self._auto_bridge_pending = False
        self._bridge_connected = False
        self._closing = False
        self.snowluma = QProcess(self)
        self.bridge = QProcess(self)
        self._managed = {self.snowluma: "SnowLuma", self.bridge: "QQ 桥接"}
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
        subbrand = QLabel("SHIMANO  /  CONTROL")
        subbrand.setObjectName("subbrand")
        side.addWidget(subbrand)
        side.addSpacing(36)
        self.nav = []
        for name in ("总览", "模型与人格", "QQ · SnowLuma"):
            button = _button(name, lambda _checked=False, i=len(self.nav): self.switch_page(i))
            button.setObjectName("nav")
            side.addWidget(button)
            self.nav.append(button)
        side.addSpacing(12)
        side.addWidget(QLabel("快捷入口"))
        side.addWidget(_button("打开语音窗口  ↗", self.open_voice))
        side.addWidget(_button("文字对话测试  ↗", self.open_chat))
        side.addWidget(_button("长期记忆管理  ↗", self.open_memory))
        side.addStretch()
        note = QLabel("设置保存在本机\n关闭控制台会停止由此启动的服务")
        note.setObjectName("sideNote")
        note.setWordWrap(True)
        side.addWidget(note)
        shell.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        shell.addWidget(self.pages, 1)
        self.pages.addWidget(self._overview())
        self.pages.addWidget(self._model_page())
        self.pages.addWidget(self._qq_page())
        self.switch_page(0)
        self.setStyleSheet(STYLE)
        self.statusBar().showMessage("控制台就绪")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start(3000)
        self.refresh_status()

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
        card, layout = _card("对话接口", "支持硅基流动、DeepSeek、Kimi、哈基米 API 及兼容接口。保存设置不会发送模型请求。")
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
        self.group_ids = _line("群聊白名单", ", ".join(map(str, sorted(saved.allowed_group_ids))))
        self.group_ids.setPlaceholderText("不填则关闭群聊")
        rule_form.addRow("群号", self.group_ids)
        self.private_cooldown = self._number(saved.private_cooldown_seconds, 0, 120, decimal=True)
        rule_form.addRow("私聊间隔 / 秒", self.private_cooldown)
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
                private_cooldown_seconds=self.private_cooldown.value(),
                group_cooldown_seconds=self.group_cooldown.value(),
                private_max_replies_per_hour=self.private_hourly.value(),
                group_max_replies_per_hour=self.group_hourly.value(),
                quiet_seconds=self.quiet_seconds.value(),
            ))
            data["allowed_private_ids"] = sorted(data["allowed_private_ids"])
            data["allowed_group_ids"] = sorted(data["allowed_group_ids"])
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
        if self.voice_window is None:
            from gui import MainWindow
            self.voice_window = MainWindow()
        self.voice_window.show()
        self.voice_window.raise_()
        self.voice_window.activateWindow()

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
        self.bridge.setProcessEnvironment(environment)
        self.bridge.setWorkingDirectory(str(ROOT))
        interpreter = Path(sys.executable)
        if interpreter.name.lower() == "pythonw.exe":
            interpreter = interpreter.with_name("python.exe")
        self.bridge.start(str(interpreter), [str(ROOT / "qq_bridge.py")])
        self._log("正在启动 QQ 桥接…")
        self.refresh_status()

    def _stop(self, process: QProcess) -> None:
        process.terminate()
        if not process.waitForFinished(1500):
            process.kill()
            process.waitForFinished(1500)
        self.refresh_status()

    def _log(self, message: str) -> None:
        self.log.appendPlainText(message)

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
                self._log(f"[{self._managed[process]}] {message}")

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
        self._closing = True
        self.timer.stop()
        for process in (self.bridge, self.snowluma):
            if process.state() != QProcess.ProcessState.NotRunning:
                self._stop(process)
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
    app.setApplicationName("Shimano")
    # Qt's offscreen plugin and some minimal Windows installations do not
    # discover the system's CJK fonts automatically.
    font_file = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"
    if font_file.is_file():
        QFontDatabase.addApplicationFont(str(font_file))
    window = Dashboard()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
