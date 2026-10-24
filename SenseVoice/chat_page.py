"""Two-pane dashboard chat using the existing conversation and memory workers."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt, Signal
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from activity_log import log_event
from chat_sessions import ChatSessions
from chat_worker import ChatWorker, MemoryWriteWorker, RemoteMemoryWriteWorker
from zhuangfy.memory.routing import legacy_memory_allowed
from llm import load_key, load_settings
from model_routes import can_attempt_stage


class ChatComposer(QPlainTextEdit):
    submitted = Signal()

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ChatPage(QWidget):
    open_models = Signal()
    open_voice = Signal()

    def __init__(self, parent=None, store: ChatSessions | None = None):
        super().__init__(parent)
        self.store = store or ChatSessions()
        self.chat_id = None
        self.dialogue = None
        self.worker: ChatWorker | None = None
        self.memory_workers: set[MemoryWriteWorker | RemoteMemoryWriteWorker] = set()
        self._pending_message = ""
        self._pending_image: bytes | None = None
        self._attachment: bytes | None = None
        self.setObjectName("chatPage")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        left = QFrame()
        left.setObjectName("chatRail")
        left.setFixedWidth(252)
        rail = QVBoxLayout(left)
        rail.setContentsMargins(16, 25, 16, 18)
        rail.setSpacing(12)
        heading = QLabel("对话")
        heading.setObjectName("chatRailTitle")
        rail.addWidget(heading)
        new_button = QPushButton("＋  新对话")
        new_button.setProperty("primary", True)
        new_button.clicked.connect(self.new_chat)
        rail.addWidget(new_button)
        self.chat_list = QListWidget()
        self.chat_list.setObjectName("chatList")
        self.chat_list.currentItemChanged.connect(self._selected)
        rail.addWidget(self.chat_list, 1)
        self.delete_button = QPushButton("删除当前对话")
        self.delete_button.clicked.connect(self.delete_chat)
        rail.addWidget(self.delete_button)
        outer.addWidget(left)

        right = QWidget()
        right.setObjectName("chatContent")
        area = QVBoxLayout(right)
        area.setContentsMargins(30, 24, 30, 24)
        area.setSpacing(14)
        header = QHBoxLayout()
        title_col = QVBoxLayout()
        self.chat_title = QLabel("庄方宜")
        self.chat_title.setObjectName("pageTitle")
        title_col.addWidget(self.chat_title)
        self.chat_subtitle = QLabel("本机文字对话 · 与语音和 QQ 使用同一套模型与角色设置")
        self.chat_subtitle.setObjectName("muted")
        title_col.addWidget(self.chat_subtitle)
        header.addLayout(title_col, 1)
        models_button = QPushButton("模型管理")
        models_button.clicked.connect(self.open_models.emit)
        header.addWidget(models_button)
        voice_button = QPushButton("语音交互 ↗")
        voice_button.clicked.connect(self.open_voice.emit)
        header.addWidget(voice_button)
        area.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("chatScroll")
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.message_host = QWidget()
        self.message_host.setObjectName("chatMessages")
        self.message_layout = QVBoxLayout(self.message_host)
        self.message_layout.setContentsMargins(12, 18, 12, 18)
        self.message_layout.setSpacing(14)
        self.message_layout.addStretch()
        self.scroll.setWidget(self.message_host)
        area.addWidget(self.scroll, 1)

        self.status = QLabel("就绪 · 对话记录保存在本机")
        self.status.setObjectName("muted")
        area.addWidget(self.status)
        composer_box = QFrame()
        composer_box.setObjectName("composerBox")
        composer = QVBoxLayout(composer_box)
        composer.setContentsMargins(12, 12, 12, 10)
        attachment_row = QHBoxLayout()
        self.attachment_thumb = QLabel()
        self.attachment_thumb.hide()
        attachment_row.addWidget(self.attachment_thumb)
        self.attachment_label = QLabel("")
        self.attachment_label.setObjectName("muted")
        self.attachment_label.hide()
        attachment_row.addWidget(self.attachment_label)
        self.remove_image_button = QPushButton("移除图片")
        self.remove_image_button.clicked.connect(self.remove_image)
        self.remove_image_button.hide()
        attachment_row.addWidget(self.remove_image_button)
        attachment_row.addStretch()
        composer.addLayout(attachment_row)
        self.input = ChatComposer()
        self.input.setPlaceholderText("发消息给庄方宜……")
        self.input.setFixedHeight(76)
        self.input.submitted.connect(self.send)
        composer.addWidget(self.input)
        bottom = QHBoxLayout()
        hint = QLabel("Ctrl + Enter 发送")
        hint.setObjectName("muted")
        bottom.addWidget(hint)
        bottom.addStretch()
        self.attach_button = QPushButton("添加图片")
        self.attach_button.clicked.connect(self.choose_image)
        bottom.addWidget(self.attach_button)
        self.send_button = QPushButton("发送消息  ↗")
        self.send_button.setProperty("primary", True)
        self.send_button.clicked.connect(self.send)
        bottom.addWidget(self.send_button)
        composer.addLayout(bottom)
        area.addWidget(composer_box)
        outer.addWidget(right, 1)

        self._reload_list()
        if not self.chat_list.count():
            self.new_chat()
        else:
            self._load_chat(self.chat_list.item(0).data(Qt.ItemDataRole.UserRole))

    def _reload_list(self, selected: str | None = None) -> None:
        self.chat_list.blockSignals(True)
        self.chat_list.clear()
        chosen = 0
        for index, chat in enumerate(self.store.list_chats()):
            item = QListWidgetItem(chat["title"])
            item.setData(Qt.ItemDataRole.UserRole, chat["id"])
            self.chat_list.addItem(item)
            if chat["id"] == selected:
                chosen = index
        if self.chat_list.count():
            self.chat_list.setCurrentRow(chosen)
        self.chat_list.blockSignals(False)

    def new_chat(self) -> None:
        if self.worker is not None:
            return
        chat_id = self.store.create()
        self._reload_list(chat_id)
        self._load_chat(chat_id)
        self.input.setFocus()

    def delete_chat(self) -> None:
        if self.worker is not None or self.chat_id is None:
            return
        answer = QMessageBox.question(
            self, "删除本机对话", "删除当前聊天记录？新记忆服务中的时间线和长期记忆需在记忆中心单独管理。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.delete(self.chat_id)
        self._reload_list()
        if self.chat_list.count():
            self._load_chat(self.chat_list.currentItem().data(Qt.ItemDataRole.UserRole))
        else:
            self.new_chat()

    def _selected(self, current, _previous) -> None:
        if current is not None and self.worker is None:
            self._load_chat(current.data(Qt.ItemDataRole.UserRole))

    def _load_chat(self, chat_id: str) -> None:
        title, self.dialogue, messages = self.store.load(chat_id)
        self.chat_id = chat_id
        self.chat_title.setText(title)
        while self.message_layout.count():
            item = self.message_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.message_layout.addStretch()
        if not messages:
            welcome = QLabel("想聊些什么？我在听。")
            welcome.setObjectName("chatWelcome")
            welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.message_layout.insertWidget(0, welcome)
        for message in messages:
            self._add_message(message["role"], message["content"], message.get("image"))
        self._scroll_end()

    def _add_message(self, role: str, content: str, image: bytes | None = None) -> None:
        if self.message_layout.count() and (welcome := self.message_layout.itemAt(0).widget()) is not None:
            if welcome.objectName() == "chatWelcome":
                self.message_layout.takeAt(0)
                welcome.deleteLater()
        row = QFrame()
        row.setObjectName("chatRow")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        bubble = QFrame()
        bubble.setObjectName("chatBubble")
        bubble.setProperty("speaker", role)
        bubble.setMaximumWidth(580)
        block = QVBoxLayout(bubble)
        block.setContentsMargins(15, 11, 15, 12)
        name = QLabel("你" if role == "user" else "庄方宜")
        name.setObjectName("bubbleName")
        block.addWidget(name)
        if image:
            preview = QLabel()
            pixmap = QPixmap()
            if pixmap.loadFromData(image):
                preview.setPixmap(pixmap.scaled(
                    360, 260, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
                block.addWidget(preview)
        label = QLabel(content)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        block.addWidget(label)
        if role == "user":
            line.addStretch(1)
            line.addWidget(bubble)
        else:
            line.addWidget(bubble)
            line.addStretch(1)
        self.message_layout.insertWidget(max(0, self.message_layout.count() - 1), row)
        self._scroll_end()

    def _scroll_end(self) -> None:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()))

    def choose_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.webp)")
        if not path:
            return
        try:
            if Path(path).stat().st_size > 12_000_000:
                raise ValueError("图片文件超过 12 MB")
            reader = QImageReader(path)
            size = reader.size()
            if not size.isValid() or size.width() * size.height() > 25_000_000:
                raise ValueError("图片尺寸无效或超过 2500 万像素")
            if max(size.width(), size.height()) > 2048:
                reader.setScaledSize(size.scaled(
                    2048, 2048, Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
            if image.isNull():
                raise ValueError("无法读取图片")
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not image.save(buffer, "JPEG", 88):
                raise ValueError("无法处理图片")
            encoded = bytes(buffer.data())
            if len(encoded) > 2_500_000:
                raise ValueError("处理后的图片仍超过 2.5 MB，请选更小的图片")
            self._attachment = encoded
            pixmap = QPixmap()
            pixmap.loadFromData(encoded)
            self.attachment_thumb.setPixmap(pixmap.scaled(
                72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            self.attachment_thumb.show()
            self.attachment_label.setText(f"已添加图片：{Path(path).name}")
            self.attachment_label.show()
            self.remove_image_button.show()
            self.status.setText("图片将在发送时交给识图模型分析")
        except (OSError, ValueError) as error:
            self.status.setText(str(error))

    def remove_image(self) -> None:
        self._attachment = None
        self.attachment_thumb.hide()
        self.attachment_label.hide()
        self.remove_image_button.hide()

    def send(self) -> None:
        if self.worker is not None or self.chat_id is None:
            return
        message = self.input.toPlainText().strip()
        image = self._attachment
        if not message and image is not None:
            message = "请看看这张图片。"
        if not message:
            self.status.setText("请先输入消息")
            return
        settings = load_settings()
        key = load_key(settings.provider)
        try:
            if not can_attempt_stage("reply", settings, key):
                raise ValueError("回复阶段没有可用模型；请先到模型管理配置 API")
            if image is not None and not can_attempt_stage("vision", settings, key):
                raise ValueError("识图阶段没有可用模型；请先到模型管理配置识图 API")
        except (OSError, ValueError, TypeError) as error:
            self.status.setText(str(error))
            return
        self._pending_message = message
        self._pending_image = image
        self.worker = ChatWorker(
            settings, key, list(self.dialogue.history), message, self,
            emotion=self.dialogue.emotion, context_notes=self.dialogue.prompt(),
            source="文字", conversation_id=f"desktop:chat:{self.chat_id}", image=image)
        self.worker.replied.connect(self._reply)
        self.worker.failed.connect(self._error)
        self.worker.memory_warning.connect(self._warning)
        self.worker.phase_changed.connect(self.status.setText)
        self.worker.finished.connect(self._finished)
        self.store.append(self.chat_id, "user", message, image=image)
        self._add_message("user", message, image)
        self._reload_list(self.chat_id)
        self.chat_title.setText(self.store.load(self.chat_id)[0])
        self.input.clear()
        self.remove_image()
        self._set_busy(True)
        self.status.setText("庄方宜正在思考……")
        self.worker.start()

    def _reply(self, reply: str) -> None:
        worker = self.worker
        if worker is None or self.chat_id is None:
            return
        self._pending_image = None
        if not worker.memory_command:
            self.dialogue.commit(self._pending_message, reply, worker.emotion_candidate)
        else:
            self.dialogue.history.extend(({"role": "user", "content": self._pending_message},
                                          {"role": "assistant", "content": reply}))
            self.dialogue.history = self.dialogue.history[-12:]
        self.store.append(self.chat_id, "assistant", reply)
        self.store.save_context(self.chat_id, self.dialogue)
        self._add_message("assistant", reply)
        self._reload_list(self.chat_id)
        self.status.setText("回复完成")
        log_event("文字", "回复", reply, turn_id=worker.turn_id, channel="文字")
        if not worker.memory_command and worker.remote_memory_ref is not None and worker.remote_client is not None:
            writer = RemoteMemoryWriteWorker(
                worker.remote_client, worker.remote_identity, worker.remote_memory_ref,
                worker.turn_id, reply, self)
            self._start_memory_writer(writer)
        if (worker.settings.memory_enabled and not worker.memory_command
                and legacy_memory_allowed("desktop")):
            self._start_memory_writer(MemoryWriteWorker(self._pending_message, reply, self))

    def _start_memory_writer(self, writer) -> None:
        self.memory_workers.add(writer)
        writer.failed.connect(self._warning)
        writer.finished.connect(lambda w=writer: self._memory_finished(w))
        writer.start()

    def _memory_finished(self, worker) -> None:
        self.memory_workers.discard(worker)
        worker.deleteLater()

    def _warning(self, message: str) -> None:
        self.status.setText(message)

    def _error(self, message: str) -> None:
        self.status.setText(f"请求失败：{message}")
        self.input.setPlainText(self._pending_message)
        if self._pending_image is not None:
            self._attachment = self._pending_image
            pixmap = QPixmap()
            pixmap.loadFromData(self._pending_image)
            self.attachment_thumb.setPixmap(pixmap.scaled(
                72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            self.attachment_thumb.show()
            self.attachment_label.setText("图片已保留，可重试")
            self.attachment_label.show()
            self.remove_image_button.show()

    def _finished(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        self._set_busy(False)
        self.input.setFocus()

    def _set_busy(self, busy: bool) -> None:
        self.input.setEnabled(not busy)
        self.attach_button.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.chat_list.setEnabled(not busy)
        self.delete_button.setEnabled(not busy)

    def shutdown(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait()
        for worker in tuple(self.memory_workers):
            if worker.isRunning():
                worker.wait()
