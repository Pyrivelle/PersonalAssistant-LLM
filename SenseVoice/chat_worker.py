"""Run a single model request without blocking the Qt event loop."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget

from llm import LlmSettings, chat


class ChatWorker(QThread):
    replied = Signal(str)
    failed = Signal(str)
    memory_warning = Signal(str)

    def __init__(self, settings: LlmSettings, key: str, history: list[dict[str, str]], message: str, parent: QWidget):
        super().__init__(parent)
        self.settings, self.key, self.history, self.message = settings, key, history, message
        self.memory_command = False

    def run(self) -> None:
        try:
            memories = []
            from memory import memory_command
            command = memory_command(self.message)
            if command and not self.settings.memory_enabled:
                self.memory_command = True
                self.replied.emit("长期记忆尚未启用。请先在模型设置中开启，再告诉我要记住或遗忘什么。")
                return
            if self.settings.memory_enabled:
                try:
                    from memory import get_memory
                    if command:
                        self.memory_command = True
                        action, content = command
                        if action == "remember":
                            get_memory().remember(content)
                            self.replied.emit("好，我记住了。你可以在记忆管理中查看或更正。")
                        else:
                            count = get_memory().forget_matching(content)
                            self.replied.emit(f"已删除 {count} 条相关记忆。" if count else
                                              "没有找到能确认匹配的记忆；你可以打开记忆管理检查。")
                        return
                    memories = get_memory().recall(self.message)
                except Exception as error:
                    if self.memory_command:
                        raise
                    self.memory_warning.emit(f"记忆检索失败，本轮仍可对话：{error}")
            self.replied.emit(chat(self.settings, self.key, self.history, self.message, memories=memories))
        except Exception as error:
            self.failed.emit(str(error))


class MemoryWriteWorker(QThread):
    failed = Signal(str)

    def __init__(self, user_text: str, assistant_text: str, parent: QWidget):
        super().__init__(parent)
        self.user_text, self.assistant_text = user_text, assistant_text

    def run(self) -> None:
        try:
            from memory import get_memory
            get_memory().record_turn(self.user_text, self.assistant_text)
        except Exception as error:
            self.failed.emit(f"长期记忆保存失败：{error}")
