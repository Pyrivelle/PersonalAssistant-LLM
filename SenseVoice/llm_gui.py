"""Typed conversation exercise, kept separate from microphone capture for now."""

from __future__ import annotations

import sys

from PySide6.QtCore import Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from chat_worker import ChatWorker, MemoryWriteWorker
from llm import DEFAULT_MODEL, DEFAULT_PROMPT, LlmSettings, PROVIDERS, SILICONFLOW_URL, load_key, load_settings, save_key, save_settings


class ChatWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Shimano · LLM 对话测试")
        self.resize(720, 820)
        self.setMinimumSize(560, 640)
        self.history: list[dict[str, str]] = []
        self.worker: ChatWorker | None = None
        self.memory_workers: set[MemoryWriteWorker] = set()
        self.memory_window = None
        saved = load_settings()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        title = QLabel("Shimano · 文字对话测试")
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
        form.addRow("API 地址", self.url)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItems([DEFAULT_MODEL, "Pro/zai-org/GLM-5.1"])
        self.model.setCurrentText(saved.model)
        form.addRow("模型 ID", self.model)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("输入后点击保存；已保存密钥不会回显")
        self.key_status = QLabel()
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.key_status)
        form.addRow("API Key", key_row)

        self.prompt = QPlainTextEdit(saved.system_prompt or DEFAULT_PROMPT)
        self.prompt.setMinimumHeight(190)
        form.addRow("人格设定", self.prompt)
        self.memory_enabled = QCheckBox("启用长期记忆：对话文字会发送给硅基流动做记忆提取和 Embedding")
        self.memory_enabled.setChecked(saved.memory_enabled)
        form.addRow("长期记忆", self.memory_enabled)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.save_button = QPushButton("保存设置与密钥")
        self.save_button.clicked.connect(self.save)
        self.reset_prompt_button = QPushButton("恢复默认人格")
        self.reset_prompt_button.clicked.connect(lambda: self.prompt.setPlainText(DEFAULT_PROMPT))
        self.clear_button = QPushButton("清空当前对话")
        self.clear_button.clicked.connect(self.clear_history)
        self.memory_button = QPushButton("查看与管理记忆…")
        self.memory_button.clicked.connect(self.open_memory_manager)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.reset_prompt_button)
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

    @Slot()
    def provider_changed(self) -> None:
        code = self.provider.currentData()
        self.url.setReadOnly(code == "siliconflow")
        if code == "siliconflow":
            self.url.setText(SILICONFLOW_URL)
        elif self.url.text() == SILICONFLOW_URL:
            self.url.setText("http://localhost:11434/v1")
        self.key_status.setText("已保存" if load_key(code) else "未保存")

    def current_settings(self) -> LlmSettings:
        return LlmSettings(
            provider=self.provider.currentData(), base_url=self.url.text(),
            model=self.model.currentText(), system_prompt=self.prompt.toPlainText(),
            memory_enabled=self.memory_enabled.isChecked(),
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
        self.history.clear()
        self.transcript.clear()
        self.status.setText("当前对话已清空")

    @Slot()
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
        if settings.provider == "siliconflow" and not key:
            self.status.setText("请先输入并保存硅基流动 API Key")
            return
        self.worker = ChatWorker(settings, key, list(self.history), message, self)
        self.worker.replied.connect(self.show_reply)
        self.worker.failed.connect(self.show_error)
        self.worker.memory_warning.connect(self.show_memory_warning)
        self.worker.finished.connect(self.worker_finished)
        self.input.setEnabled(False)
        self.send_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.reset_prompt_button.setEnabled(False)
        self.status.setText("正在等待模型回复…")
        self.worker.start()

    @Slot(str)
    def show_reply(self, reply: str) -> None:
        assert self.worker is not None
        message = self.worker.message
        self.history.extend(({"role": "user", "content": message}, {"role": "assistant", "content": reply}))
        self.history = self.history[-12:]
        self.transcript.appendPlainText(f"你：{message}\n庄方宜：{reply}\n")
        self.input.clear()
        self.status.setText("回复完成")
        if self.worker.settings.memory_enabled and not self.worker.memory_command:
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
        self.reset_prompt_button.setEnabled(True)
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
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = ChatWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
