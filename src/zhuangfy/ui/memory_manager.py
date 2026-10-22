"""M5 PySide6 administrator interface for the NEW curated memory store.

The old Mem0 manager remains unchanged. This dialog requires the separate
admin bearer token AND ZHUANGFY_MEMORY_ADMIN_UI=1; it never reads the SQLite
file directly or silently merges QQ/desktop identities.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from zhuangfy.memory.client import MemoryChannel, MemoryClient


class AdminTask(QThread):
    completed = Signal(str, object)
    failed = Signal(str)

    def __init__(self, client: MemoryClient, action: str, args: tuple, parent: QWidget):
        super().__init__(parent)
        self.client = client
        self.action = action
        self.args = args

    def run(self):
        try:
            result = getattr(self.client, self.action)(*self.args)
            self.completed.emit(self.action, result)
        except Exception as exc:
            # No raw request, tokens or private content in the error UI.
            self.failed.emit(f"{self.action} 失败：{type(exc).__name__}")


class CuratedMemoryDialog(QDialog):
    """Auditable memories, source quotes, manual correction, erasure and jobs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ZhuangFY · 新记忆库管理（实验）")
        self.resize(850, 730)
        self.client = MemoryClient.from_environment(
            enabled_var="ZHUANGFY_MEMORY_ADMIN_UI", admin=True)
        self.task = None
        self.pending = None
        self.people = []
        self.current = []
        self.jobs = []

        layout = QVBoxLayout(self)
        self.notice = QLabel("新 Memory Service · 管理员模式。与旧 Mem0 存储独立。")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)

        head = QHBoxLayout()
        self.person = QComboBox()
        self.person.currentIndexChanged.connect(self.refresh_memories)
        head.addWidget(self.person, 1)
        for text, callback in [
            ("刷新人物", self.refresh_people),
            ("创建人物", self.create_person),
            ("验证关联账号", self.link_identity),
            ("授权管理", self.manage_consent),
            ("彻底删除人物", self.erase_person),
        ]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            head.addWidget(button)
        layout.addLayout(head)

        self.memories = QListWidget()
        self.memories.currentItemChanged.connect(self.show_selected_memory)
        layout.addWidget(QLabel("当前人物的有效记忆（人工编辑会明确标记）"))
        layout.addWidget(self.memories, 2)

        layout.addWidget(QLabel("记忆内容（可人工更正）"))
        self.editor = QPlainTextEdit()
        self.editor.setMaximumHeight(95)
        layout.addWidget(self.editor)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("更正原因，至少三个字符；人工更正不会伪造历史对话证据")
        layout.addWidget(self.reason)

        controls = QHBoxLayout()
        for text, callback in [
            ("保存人工更正", self.correct_memory),
            ("遗忘所选记忆", self.forget_memory),
            ("关联两条事件", self.link_episodes),
            ("同步向量索引", self.sync_index),
        ]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)

        layout.addWidget(QLabel("来源证据、人工更正记录与事件关联"))
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        layout.addWidget(self.evidence, 2)

        foot = QHBoxLayout()
        foot.addWidget(QLabel("后台反思作业"))
        self.job_list = QListWidget()
        self.job_list.setMaximumHeight(90)
        layout.addWidget(self.job_list)
        for text, callback in [
            ("刷新任务", self.refresh_jobs),
            ("重试失败任务", self.retry_job),
            ("手动整理下一批", self.reflect_one),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(callback)
            foot.addWidget(btn)
        layout.addLayout(foot)

        if self.client is None:
            self.notice.setText("未启用新记忆管理：设置 ZHUANGFY_MEMORY_ADMIN_UI=1 和管理员令牌后重启界面。")
        else:
            self.refresh_people()

    def _start(self, action: str, *args):
        if self.client is None:
            return
        if self.task is not None:
            self.pending = (action, args)
            return
        self.notice.setText(f"处理中：{action}")
        worker = AdminTask(self.client, action, args, self)
        self.task = worker
        worker.completed.connect(self._done)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        worker.start()

    def _finished(self):
        worker = self.task
        self.task = None
        if worker:
            worker.deleteLater()
        if self.pending:
            action, args = self.pending
            self.pending = None
            self._start(action, *args)

    def _failed(self, message):
        self.notice.setText(message)

    def _done(self, action, result):
        self.notice.setText(f"已完成：{action}")
        if action == "list_people":
            selected = self.person.currentData()
            self.people = result
            self.person.blockSignals(True)
            self.person.clear()
            for person in result:
                self.person.addItem(f'{person["display_name"]} · {person["person_id"][:8]}',
                                    person["person_id"])
            if selected:
                for i in range(self.person.count()):
                    if self.person.itemData(i) == selected:
                        self.person.setCurrentIndex(i)
                        break
            self.person.blockSignals(False)
            self.refresh_memories()
        elif action == "list_memories":
            self.current = result
            self.memories.clear()
            for item in result:
                label = f'[{item["kind"]}/{item["event_state"]}] {item["content"][:100]}'
                row = self._new_list_item(self.memories, label, item["memory_id"])
            self.refresh_jobs()
        elif action == "get_evidence":
            self.evidence.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        elif action == "list_jobs":
            self.jobs = result
            self.job_list.clear()
            for job in result:
                self._new_list_item(
                    self.job_list,
                    f'{job["status"]} · {job["attempts"]} 次 · {job["job_id"][:8]}',
                    job["job_id"])
        elif action in {"correct_memory", "forget_memory", "forget_person",
                        "admin_create_person", "admin_link", "admin_consent",
                        "link_episodes", "sync_index", "retry_job", "reflect_one"}:
            self.refresh_people()

    @staticmethod
    def _new_list_item(widget: QListWidget, text: str, identifier: str):
        from PySide6.QtWidgets import QListWidgetItem
        row = QListWidgetItem(text)
        row.setData(Qt.ItemDataRole.UserRole, identifier)
        widget.addItem(row)
        return row

    def _person_id(self) -> str | None:
        return self.person.currentData()

    def _memory_id(self) -> str | None:
        item = self.memories.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh_people(self):
        self._start("list_people")

    def refresh_memories(self, *_):
        person_id = self._person_id()
        if person_id:
            self._start("list_memories", person_id)
        else:
            self.memories.clear()
            self.evidence.clear()
            self.job_list.clear()

    def refresh_jobs(self):
        if self._person_id():
            self._start("list_jobs", self._person_id())

    def show_selected_memory(self, current, previous):
        if current is None:
            return
        mid = self._memory_id()
        data = next((m for m in self.current if m["memory_id"] == mid), None)
        if data:
            self.editor.setPlainText(data["content"])
            self._start("get_evidence", self._person_id(), mid)

    def _confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def create_person(self):
        name, ok = QInputDialog.getText(self, "创建人物", "展示名称（不用于身份自动合并）：")
        if ok:
            self._start("admin_create_person", name)

    def link_identity(self):
        person_id = self._person_id()
        if not person_id:
            return
        values = []
        for title, default in [("平台", "qq"), ("机器人/设备账号", ""), ("平台用户ID", "")]:
            v, ok = QInputDialog.getText(self, "关联已核实账号", title, text=default)
            if not ok or not v.strip():
                return
            values.append(v.strip())
        if not self._confirm("确认账号归属",
            "请确认已在可信渠道核实账号拥有者，并且这个账号确实属于当前人物。不能只凭相同昵称合并。"):
            return
        reference, ok = QInputDialog.getText(self, "核验记录", "人工审核记录编号（不填验证码或密码）：")
        if ok and reference.strip():
            self._start("admin_link", person_id, MemoryChannel(*values), reference)

    def manage_consent(self):
        person_id = self._person_id()
        if not person_id:
            return
        purpose, ok = QInputDialog.getItem(
            self, "选择授权项", "记忆用途：", ["timeline", "curation"], 0, False)
        if not ok:
            return
        decision = QMessageBox.question(
            self, "授权或撤销", f"是否授权当前人物的 {purpose}？\n"
            "选“是”授权；选“否”撤销该授权。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if decision == QMessageBox.StandardButton.Cancel:
            return
        self._start("admin_consent", person_id, purpose,
                    decision == QMessageBox.StandardButton.Yes)

    def correct_memory(self):
        if self._person_id() and self._memory_id():
            self._start("correct_memory", self._person_id(), self._memory_id(),
                        self.editor.toPlainText(), self.reason.text())

    def forget_memory(self):
        mid = self._memory_id()
        if mid and self._confirm("删除记忆", "永久删除该记忆及其较旧版本？原始 Timeline 另行管理。"):
            self._start("forget_memory", self._person_id(), mid)

    def erase_person(self):
        person = self._person_id()
        if person and self._confirm(
                "危险：删除人物", "永久删除此人物在新记忆库中的账号映射、Timeline 与记忆？旧 Mem0、QQ 数据和备份不受此操作影响。"):
            self._start("forget_person", person)

    def link_episodes(self):
        if not self._person_id() or not self._memory_id():
            return
        target, ok = QInputDialog.getText(self, "事件关联", "目标 episodic memory_id：")
        if not ok or not target.strip():
            return
        relation, ok = QInputDialog.getItem(self, "关系类型", "关系：",
                                             ["related_to", "follow_up", "resolved_by"], 0, False)
        if ok:
            self._start("link_episodes", self._person_id(), self._memory_id(),
                        target.strip(), relation)

    def sync_index(self):
        self._start("sync_index")

    def retry_job(self):
        item = self.job_list.currentItem()
        if item and self._person_id():
            self._start("retry_job", self._person_id(), item.data(Qt.ItemDataRole.UserRole))

    def reflect_one(self):
        if self._confirm("开始整理", "将向已配置的 Curator 模型发送已获授权、经过凭据过滤的私聊资料。继续？"):
            self._start("reflect_one")

    def closeEvent(self, event):
        if self.task and self.task.isRunning():
            self.notice.setText("后台操作进行中，请操作完成后再关闭窗口。")
            event.ignore()
            return
        super().closeEvent(event)
