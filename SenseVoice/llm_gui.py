"""Typed conversation exercise, kept separate from microphone capture for now."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from chat_worker import ChatWorker, MemoryWriteWorker, RemoteMemoryWriteWorker
from toggle_switch import ToggleSwitch
from zhuangfy.memory.routing import legacy_memory_allowed
from branding import APP_NAME, app_icon
from activity_log import log_event
from emotion import EmotionState
from dialogue_context import DialogueContext, DialogueStore
from turn_planner import PlannerSettings
from emotion import EmotionState
from llm import (ROOT, DEFAULT_PROMPT, LlmSettings, PROVIDERS, PROVIDER_BASE_URLS,
                 PROVIDER_DEFAULT_MODELS, PROVIDER_SUGGESTED_MODELS,
                 load_key, load_settings, save_key, save_settings)
from model_catalog import ModelCatalogWorker
from model_routes import can_attempt_stage
from persona_packs import BUILTIN_PACKS, matching_pack


class ChatWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} · LLM 对话测试")
        self.setWindowIcon(app_icon())
        self.resize(720, 820)
        self.setMinimumSize(560, 640)
        self.context_store = None
        try:
            config = PlannerSettings.load(ROOT / "cognition_settings.json")
            if config.persist_desktop_context:
                self.context_store = DialogueStore(ROOT / "conversation_data" / "desktop_context.sqlite3")
        except Exception as error:
            print(f"桌面上下文存储不可用：{type(error).__name__}")
        self.dialogue = self.context_store.load("desktop:text") if self.context_store else DialogueContext()
        self.history = self.dialogue.history
        self.emotion = self.dialogue.emotion
        self.worker: ChatWorker | None = None
        self.memory_workers: set[MemoryWriteWorker] = set()
        self.memory_window = None
        saved = load_settings()
        self._last_provider = saved.provider
        self._provider_values = {saved.provider: (saved.base_url, saved.model)}
        self._model_choices = {}
        self._model_worker = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        title = QLabel(f"{APP_NAME} · 文字对话测试")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QLabel("在这里调整庄方宜的人格设定，并用文字验证多轮对话。设置也会用于主语音窗口。"))

        form = QFormLayout()
        form.setSpacing(10)
        self.provider = QComboBox()
        for code, label in PROVIDERS.items():
            self.provider.addItem(label, code)
        self.provider.setCurrentIndex(self.provider.findData(saved.provider))
        self.provider.currentIndexChanged.connect(self.provider_changed)
        form.addRow("供应商", self.provider)

        self.url = QLineEdit(saved.base_url)
        self.url.setPlaceholderText("输入供应商提供的 API Base URL，例如 https://example.com/v1")
        form.addRow("API 地址", self.url)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setMaxVisibleItems(16)
        self.model.addItems(PROVIDER_SUGGESTED_MODELS[saved.provider])
        self.model.setCurrentText(saved.model)
        self.model.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.model.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        self.fetch_button = QPushButton("获取模型列表")
        self.fetch_button.clicked.connect(self.fetch_models)
        model_row.addWidget(self.fetch_button)
        form.addRow("模型 ID", model_row)
        self.catalog_status = QLabel("可手动输入，或从供应商获取模型列表")
        form.addRow("模型列表", self.catalog_status)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("输入后点击保存；已保存密钥不会回显")
        self.key_status = QLabel()
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.key_status)
        form.addRow("API Key", key_row)

        self.persona_pack = QComboBox()
        for pack in BUILTIN_PACKS:
            self.persona_pack.addItem(pack.name, pack)
        self.load_pack_button = QPushButton("载入人格包")
        self.load_pack_button.clicked.connect(self.load_persona_pack)
        pack_row = QHBoxLayout()
        pack_row.addWidget(self.persona_pack, 1)
        pack_row.addWidget(self.load_pack_button)
        form.addRow("人格包", pack_row)

        self.prompt = QPlainTextEdit(saved.system_prompt or DEFAULT_PROMPT)
        self.prompt.setMinimumHeight(190)
        form.addRow("人格设定", self.prompt)
        self.persona_state = QLabel()
        self.prompt.textChanged.connect(self.update_persona_state)
        form.addRow("当前提示词", self.persona_state)
        self.update_persona_state()
        self.memory_enabled = ToggleSwitch("启用长期记忆：对话文字会发送给硅基流动做记忆提取和 Embedding")
        self.memory_enabled.setChecked(saved.memory_enabled)
        if not legacy_memory_allowed("desktop"):
            self.memory_enabled.setChecked(False)
            self.memory_enabled.setEnabled(False)
            self.memory_enabled.setToolTip("新记忆服务已接管桌面对话；旧版记忆不会参与读取或写入。")
        form.addRow("长期记忆", self.memory_enabled)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.save_button = QPushButton("保存设置与密钥")
        self.save_button.clicked.connect(self.save)
        self.clear_button = QPushButton("清空当前对话")
        self.clear_button.clicked.connect(self.clear_history)
        self.memory_button = QPushButton("查看与管理记忆…")
        self.memory_button.clicked.connect(self.open_memory_manager)
        self.routes_button = QPushButton("模型管理…")
        self.routes_button.clicked.connect(self.open_model_routes)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.routes_button)
        buttons.addWidget(self.clear_button)
        buttons.addWidget(self.memory_button)
        buttons.addStretch()
        layout.addLayout(buttons)

        self.status = QLabel("就绪 · 对话历史仅保存在本次窗口中")
        self.status.setObjectName("status")
        layout.addWidget(self.status)
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText("模型回复会显示在这里")
        layout.addWidget(self.transcript, 1)
        entry = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入一句话，例如：小庄，你好")
        self.input.returnPressed.connect(self.send)
        self.send_button = QPushButton("发送")
        self.send_button.clicked.connect(self.send)
        entry.addWidget(self.input, 1)
        entry.addWidget(self.send_button)
        layout.addLayout(entry)

        self.setStyleSheet("""
            QWidget { color:#243041; font-size:14px; background:#f6f8fb; }
            QLabel#title { font-size:23px; font-weight:700; }
            QLabel#status { color:#617186; }
            QLineEdit, QComboBox, QPlainTextEdit { background:white; border:1px solid #cfd9e5; border-radius:7px; padding:7px; }
            QPushButton { background:white; border:1px solid #cbd6e2; border-radius:7px; padding:8px 13px; }
            QPushButton:hover { background:#edf4fb; }
            QPushButton:disabled { color:#9daab9; }
            QPushButton#send { background:#2468c5; color:white; border-color:#2468c5; }
        """)
        self.send_button.setObjectName("send")
        self.provider_changed()

    def load_persona_pack(self) -> None:
        pack = self.persona_pack.currentData()
        if pack is not None:
            self.prompt.setPlainText(pack.prompt())

    def update_persona_state(self) -> None:
        pack = matching_pack(self.prompt.toPlainText())
        self.persona_state.setText(pack.name if pack else "自定义；载入人格包后需保存")

    @Slot()
    def provider_changed(self) -> None:
        code = self.provider.currentData()
        if code != self._last_provider:
            self._provider_values[self._last_provider] = (self.url.text(), self.model.currentText())
            url, model = self._provider_values.get(code, (
                PROVIDER_BASE_URLS.get(code, "http://localhost:11434/v1"),
                PROVIDER_DEFAULT_MODELS[code],
            ))
            self.url.setText(url)
            self._set_model_choices(self._model_choices.get(code, PROVIDER_SUGGESTED_MODELS[code]), model)
            self.catalog_status.setText("可手动输入，或从供应商获取模型列表")
            self._last_provider = code
        self.url.setReadOnly(code in PROVIDER_BASE_URLS)
        self.key_status.setText("已保存" if load_key(code) else "未保存")

    def _set_model_choices(self, choices: list[str], selected: str) -> None:
        self.model.clear()
        self.model.addItems(choices)
        self.model.setCurrentText(selected)

    @Slot()
    def fetch_models(self) -> None:
        if self._model_worker is not None:
            return
        try:
            settings = LlmSettings(
                self.provider.currentData(), self.url.text(),
                self.model.currentText().strip() or "_", self.prompt.toPlainText(),
                self.memory_enabled.isChecked(),
            ).validated()
            key = self.key_edit.text().strip() or load_key(settings.provider)
            if settings.provider not in {"custom", "hachimi"} and not key:
                raise ValueError("请先填写或保存当前供应商的 API Key")
            worker = ModelCatalogWorker(settings, key)
            self._model_worker = worker
            self.fetch_button.setEnabled(False)
            self.catalog_status.setText("正在获取模型列表…")
            worker.loaded.connect(lambda choices, p=settings.provider, u=settings.base_url: self._models_loaded(p, u, choices))
            worker.failed.connect(lambda message: self.catalog_status.setText(message))
            worker.finished.connect(self._models_finished)
            worker.start()
        except (OSError, ValueError) as error:
            self.catalog_status.setText(str(error))

    def _models_loaded(self, provider: str, url: str, choices: list[str]) -> None:
        self._model_choices[provider] = choices
        if self.provider.currentData() != provider or self.url.text().rstrip("/") != url:
            return
        selected = self.model.currentText()
        self._set_model_choices(choices, selected)
        note = "当前模型 ID 不在列表中；请检查后再使用" if selected and selected not in choices else "列表可能含非对话模型"
        if provider == "hachimi" and not (self.key_edit.text().strip() or load_key("hachimi")):
            note += "；这是公开目录，实际权限以账号为准"
        self.catalog_status.setText(f"已获取 {len(choices)} 个模型；{note}")
        self.model.showPopup()

    def _models_finished(self) -> None:
        worker = self._model_worker
        self._model_worker = None
        self.fetch_button.setEnabled(True)
        if worker is not None:
            worker.deleteLater()

    def current_settings(self) -> LlmSettings:
        return LlmSettings(
            provider=self.provider.currentData(), base_url=self.url.text(),
            model=self.model.currentText(), system_prompt=self.prompt.toPlainText(),
            memory_enabled=self.memory_enabled.isChecked() and legacy_memory_allowed("desktop"),
        ).validated()

    @Slot()
    def save(self) -> bool:
        try:
            settings = self.current_settings()
            if settings.memory_enabled and not (load_key("siliconflow") or
                                                (settings.provider == "siliconflow" and self.key_edit.text().strip())):
                raise ValueError("启用长期记忆前，请先保存硅基流动 API Key")
            save_settings(settings)
            if self.key_edit.text().strip():
                save_key(settings.provider, self.key_edit.text())
                self.key_edit.clear()
            self.key_status.setText("已保存" if load_key(settings.provider) else "未保存")
            self.status.setText("设置已保存 · 密钥保存在本机 .env.local")
            return True
        except (OSError, ValueError) as error:
            self.status.setText(f"保存失败：{error}")
            return False

    @Slot()
    def clear_history(self) -> None:
        if self.worker is not None:
            return
        self.dialogue = DialogueContext()
        self.history = self.dialogue.history
        self.emotion = self.dialogue.emotion
        if self.context_store:
            try:
                self.context_store.delete("desktop:text")
            except Exception as error:
                print(f"桌面上下文清理失败：{type(error).__name__}")
        self.transcript.clear()
        self.status.setText("当前对话已清空")

    @Slot()
    def open_model_routes(self) -> None:
        from model_management_page import ModelManagementDialog
        try:
            ModelManagementDialog(self).exec()
        except (OSError, ValueError, TypeError) as error:
            self.catalog_status.setText(f"API 路由配置无效：{error}")

    def open_memory_manager(self) -> None:
        from memory_gui import MemoryDialog
        if self.memory_window is None:
            self.memory_window = MemoryDialog(self)
        self.memory_window.show()
        self.memory_window.raise_()
        self.memory_window.activateWindow()

    @Slot()
    def send(self) -> None:
        if self.worker is not None:
            return
        message = self.input.text().strip()
        if not message:
            self.status.setText("请先输入消息")
            return
        if not self.save():
            return
        settings = self.current_settings()
        key = load_key(settings.provider)
        try:
            if not can_attempt_stage("reply", settings, key):
                self.status.setText("回复阶段没有可用的 API 密钥或本地服务，请先配置模型路由")
                return
        except (OSError, ValueError, TypeError) as error:
            self.status.setText(f"模型路由配置有误：{error}")
            return
        self.worker = ChatWorker(settings, key, list(self.history), message, self,
                                 emotion=self.dialogue.emotion, context_notes=self.dialogue.prompt(), source="文字")
        self.worker.replied.connect(self.show_reply)
        self.worker.failed.connect(self.show_error)
        self.worker.memory_warning.connect(self.show_memory_warning)
        self.worker.finished.connect(self.worker_finished)
        self.input.setEnabled(False)
        self.send_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.load_pack_button.setEnabled(False)
        self.status.setText("正在等待模型回复…")
        self.worker.start()

    @Slot(str)
    def show_reply(self, reply: str) -> None:
        assert self.worker is not None
        message = self.worker.message
        if not self.worker.memory_command:
            self.dialogue.commit(message, reply, self.worker.emotion_candidate)
        else:
            self.dialogue.history.extend(({"role": "user", "content": message},
                                          {"role": "assistant", "content": reply}))
            self.dialogue.history = self.dialogue.history[-12:]
        self.history = self.dialogue.history
        self.emotion = self.dialogue.emotion
        if self.context_store:
            try:
                self.context_store.save("desktop:text", self.dialogue)
            except Exception as error:
                print(f"桌面上下文保存失败：{type(error).__name__}")
        self.transcript.appendPlainText(f"你：{message}\n庄方宜：{reply}\n")
        log_event("文字", "回复", reply)
        self.input.clear()
        self.status.setText("回复完成")
        if (not self.worker.memory_command and self.worker.remote_memory_ref is not None
                and self.worker.remote_client is not None):
            remote_writer = RemoteMemoryWriteWorker(
                self.worker.remote_client, self.worker.remote_identity,
                self.worker.remote_memory_ref, self.worker.turn_id, reply, self)
            self.memory_workers.add(remote_writer)
            remote_writer.failed.connect(self.show_memory_warning)
            remote_writer.finished.connect(lambda w=remote_writer: self.memory_write_finished(w))
            remote_writer.start()
        if (self.worker.settings.memory_enabled and not self.worker.memory_command
                and legacy_memory_allowed("desktop")):
            writer = MemoryWriteWorker(message, reply, self)
            self.memory_workers.add(writer)
            writer.failed.connect(self.show_memory_warning)
            writer.finished.connect(lambda w=writer: self.memory_write_finished(w))
            writer.start()

    @Slot(str)
    def show_memory_warning(self, warning: str) -> None:
        self.status.setText(warning)

    def memory_write_finished(self, worker: MemoryWriteWorker) -> None:
        self.memory_workers.discard(worker)
        worker.deleteLater()

    @Slot(str)
    def show_error(self, error: str) -> None:
        self.status.setText(f"请求失败：{error}")

    @Slot()
    def worker_finished(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        self.input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self.load_pack_button.setEnabled(True)
        self.input.setFocus()

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait()
        for worker in tuple(self.memory_workers):
            if worker.isRunning():
                worker.wait()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setWindowIcon(app_icon())
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = ChatWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
