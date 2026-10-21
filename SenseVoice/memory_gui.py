"""Inspect and correct long-term memories without blocking the Qt event loop."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from branding import app_icon
from memory import get_memory


class MemoryTask(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, action: str, args: tuple, parent: QDialog):
        super().__init__(parent)
        self.action, self.args = action, args

    def run(self) -> None:
        try:
            result = getattr(get_memory(), self.action)(*self.args)
            self.done.emit(result)
        except Exception as error:
            self.failed.emit(str(error))


class MemoryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("庄方宜的长期记忆")
        self.setWindowIcon(app_icon())
        self.resize(650, 570)
        self.task: MemoryTask | None = None
        self._refresh_after_task = False

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("记忆保存在本机；新增或修改会向硅基流动发送文字以生成 Embedding。"))
        self.memories = QListWidget()
        self.memories.currentItemChanged.connect(self.select_memory)
        layout.addWidget(self.memories, 1)
        layout.addWidget(QLabel("选中记忆后可更正内容，或在下方输入一条新记忆："))
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("例如：用户希望被称为……")
        self.editor.setMaximumHeight(100)
        layout.addWidget(self.editor)

        row = QHBoxLayout()
        self.refresh_button = QPushButton("刷新")
        self.add_button = QPushButton("记住新内容")
        self.update_button = QPushButton("更正选中记忆")
        self.delete_button = QPushButton("删除选中记忆")
        self.clear_button = QPushButton("遗忘全部")
        for button in (self.refresh_button, self.add_button, self.update_button,
                       self.delete_button, self.clear_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.status = QLabel("就绪")
        layout.addWidget(self.status)
        self.refresh_button.clicked.connect(self.refresh)
        self.add_button.clicked.connect(self.add_memory)
        self.update_button.clicked.connect(self.update_memory)
        self.delete_button.clicked.connect(self.delete_memory)
        self.clear_button.clicked.connect(self.clear_memories)
        QTimer.singleShot(0, self.refresh)

    def _selected_id(self) -> str | None:
        item = self.memories.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def select_memory(self, item, _previous) -> None:
        if item:
            self.editor.setPlainText(str(item.data(Qt.ItemDataRole.UserRole + 1)))

    def _start(self, action: str, *args) -> None:
        if self.task is not None:
            return
        self._refresh_after_task = action != "list_all"
        self.task = MemoryTask(action, args, self)
        self.task.done.connect(self._done)
        self.task.failed.connect(self._failed)
        self.task.finished.connect(self._finished)
        for button in (self.refresh_button, self.add_button, self.update_button,
                       self.delete_button, self.clear_button):
            button.setEnabled(False)
        self.status.setText("正在处理记忆…")
        self.task.start()

    def _done(self, result) -> None:
        if self.task is not None and self.task.action == "list_all":
            self.memories.clear()
            for memory in result:
                content = str(memory.get("memory", ""))
                item = QListWidgetItem(content)
                item.setData(Qt.ItemDataRole.UserRole, memory.get("id"))
                item.setData(Qt.ItemDataRole.UserRole + 1, content)
                self.memories.addItem(item)
            self.status.setText(f"已加载 {self.memories.count()} 条记忆")
        else:
            self.editor.clear()
            self.status.setText("操作完成")

    def _failed(self, error: str) -> None:
        self._refresh_after_task = False
        self.status.setText(f"记忆操作失败：{error}")

    def _finished(self) -> None:
        task = self.task
        self.task = None
        if task is not None:
            task.deleteLater()
        for button in (self.refresh_button, self.add_button, self.update_button,
                       self.delete_button, self.clear_button):
            button.setEnabled(True)
        if self._refresh_after_task:
            self._refresh_after_task = False
            QTimer.singleShot(0, self.refresh)

    def refresh(self) -> None:
        self._start("list_all")

    def add_memory(self) -> None:
        if self.editor.toPlainText().strip():
            self._start("remember", self.editor.toPlainText())

    def update_memory(self) -> None:
        if self._selected_id() and self.editor.toPlainText().strip():
            self._start("update", self._selected_id(), self.editor.toPlainText())

    def delete_memory(self) -> None:
        if self._selected_id():
            self._start("forget", self._selected_id())

    def clear_memories(self) -> None:
        if QMessageBox.question(self, "遗忘全部", "确定删除所有长期记忆吗？") == QMessageBox.StandardButton.Yes:
            self._start("forget_all")

    def closeEvent(self, event) -> None:
        if self.task is not None and self.task.isRunning():
            self.task.wait()
        super().closeEvent(event)
