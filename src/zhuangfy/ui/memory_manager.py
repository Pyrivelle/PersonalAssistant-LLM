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
    QTabWidget, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QVBoxLayout, QWidget,
)

from zhuangfy.memory.client import MemoryChannel, MemoryClient
from zhuangfy.observability.activity_log import log_event


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
        self.setWindowTitle("ZhuangFY · 记忆管理与 Curator 审核")
        self.resize(1020, 780)
        self.client = MemoryClient.from_environment(
            enabled_var="ZHUANGFY_MEMORY_ADMIN_UI", admin=True)
        self.task = None
        self.pending = None
        self.people = []
        self.current = []
        self.jobs = []

        layout = QVBoxLayout(self)
        self.notice = QLabel("新 Memory Service · 管理员模式；与旧 Mem0 分离。")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)

        head = QHBoxLayout()
        self.person = QComboBox()
        self.person.currentIndexChanged.connect(self._person_changed)
        head.addWidget(QLabel("当前人物"))
        head.addWidget(self.person, 1)
        for text, callback in [
            ("刷新", self.refresh_people),
            ("创建人物", self.create_person),
            ("修改名称", self.rename_person),
            ("验证关联账号", self.link_identity),
            ("删除整个人物", self.erase_person),
        ]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            head.addWidget(button)
        layout.addLayout(head)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # Account page: stable channel identity, local transcript status and
        # separate cloud-Curator authorization. No transcript body is fetched.
        accounts_page = QWidget()
        accounts_layout = QVBoxLayout(accounts_page)
        self.account_hint = QLabel(
            "人物由首次 QQ 私聊自动建档；QQ 昵称仅供展示，同名不合并。"
            "本地聊天存储与付费长期记忆提炼分别管理。")
        self.account_hint.setWordWrap(True)
        accounts_layout.addWidget(self.account_hint)
        self.account_table = QTableWidget(0, 6)
        self.account_table.setHorizontalHeaderLabels(
            ["人物 / Person ID", "已关联的渠道账号", "本地记录",
             "Curator", "私聊条数", "长期记忆"])
        self.account_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.account_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.account_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.account_table.horizontalHeader().setStretchLastSection(True)
        self.account_table.setColumnWidth(0, 210)
        self.account_table.setColumnWidth(1, 315)
        self.account_table.cellClicked.connect(self.select_person_row)
        accounts_layout.addWidget(self.account_table, 1)
        self.person_summary = QLabel("请选择人物以查看账号与授权状态。")
        self.person_summary.setWordWrap(True)
        accounts_layout.addWidget(self.person_summary)
        account_actions = QHBoxLayout()
        for text, callback in [
            ("管理本地记录授权", self.manage_timeline),
            ("进入 Curator 审核", lambda: self.tabs.setCurrentWidget(curator_page)),
            ("刷新人物", self.refresh_people),
        ]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            account_actions.addWidget(button)
        accounts_layout.addLayout(account_actions)
        self.tabs.addTab(accounts_page, "账户与聊天记录")

        memories_page = QWidget()
        memories_layout = QVBoxLayout(memories_page)
        memories_layout.addWidget(QLabel("当前人物的有效长期记忆（人工编辑有独立审计）"))
        self.memories = QListWidget()
        self.memories.currentItemChanged.connect(self.show_selected_memory)
        memories_layout.addWidget(self.memories, 2)
        memories_layout.addWidget(QLabel("记忆内容（可人工更正）"))
        self.editor = QPlainTextEdit()
        self.editor.setMaximumHeight(105)
        memories_layout.addWidget(self.editor)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("更正原因，至少三个字符；人工更正不冒充原始对话")
        memories_layout.addWidget(self.reason)
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
        memories_layout.addLayout(controls)
        memories_layout.addWidget(QLabel("来源证据、人工更正记录与事件关联"))
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        memories_layout.addWidget(self.evidence, 1)
        self.tabs.addTab(memories_page, "长期记忆管理")

        # Curator page: person-scoped approval, job status and explicit work.
        curator_page = QWidget()
        curator_layout = QVBoxLayout(curator_page)
        intro = QLabel(
            "Curator 会使用已存储的聊天文本提炼长期记忆，可能调用外部模型并产生费用。"
            "每个人默认不批准；批准/撤销只影响该人物，不改变本地 Timeline 留存。")
        intro.setWordWrap(True)
        curator_layout.addWidget(intro)
        self.curator_status = QLabel("当前人物尚无审核信息。")
        self.curator_status.setWordWrap(True)
        curator_layout.addWidget(self.curator_status)
        approvals = QHBoxLayout()
        self.approve_curator_button = QPushButton("批准当前人物长期记忆提炼")
        self.approve_curator_button.clicked.connect(self.approve_curator)
        approvals.addWidget(self.approve_curator_button)
        self.revoke_curator_button = QPushButton("撤销当前人物提炼授权")
        self.revoke_curator_button.clicked.connect(self.revoke_curator)
        approvals.addWidget(self.revoke_curator_button)
        curator_layout.addLayout(approvals)
        curator_layout.addWidget(QLabel("该人物的提炼作业（待处理 / 执行中 / 完成 / 失败）"))
        self.job_list = QListWidget()
        curator_layout.addWidget(self.job_list, 1)
        self.reflection_status = QLabel("未手动触发整理")
        self.reflection_status.setWordWrap(True)
        curator_layout.addWidget(self.reflection_status)
        foot = QHBoxLayout()
        for text, callback in [
            ("刷新该人物任务", self.refresh_jobs),
            ("重试失败任务", self.retry_job),
            ("执行下一项已授权任务", self.reflect_one),
        ]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            foot.addWidget(button)
        curator_layout.addLayout(foot)
        curator_layout.addWidget(QLabel(
            "注意：执行下一项按全局队列领取已批准的任务，不保证是当前选中人物。"
            "未开启 Curator 模型时不会产生模型请求。"))
        self.tabs.addTab(curator_page, "Curator 审核与任务")

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
        if action == "reflect_one":
            self.reflection_status.setText("正在请求记忆服务整理下一批，等待任务与模型结果…")
            log_event("记忆整理", "开始", "手动整理请求已发送到本机 Memory Service")
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
        if self.task is not None and self.task.action == "reflect_one":
            self.reflection_status.setText("记忆服务请求失败；无法确认模型是否收到资料。")
            log_event("记忆整理", "请求失败", message)

    def _done(self, action, result):
        self.notice.setText(f"已完成：{action}")
        if action == "reflect_one":
            status = result.get("status", "unknown") if isinstance(result, dict) else "unknown"
            attempted = bool(result.get("model_attempted")) if isinstance(result, dict) else False
            if status == "idle":
                summary = "没有待整理任务；本次没有调用模型。"
            elif status == "done":
                summary = (f"整理成功；模型{'已调用' if attempted else '未调用（无可用资料）'}；"
                           f"新增 {result.get('created', 0)} 条记忆；"
                           f"索引失败 {result.get('index_failures', 0)} 条。")
            else:
                summary = (f"整理失败；模型{'已尝试调用' if attempted else '未调用'}；"
                           f"原因类型：{result.get('error_type', 'unknown')}。")
            self.reflection_status.setText(summary)
            log_event("记忆整理", status, summary)
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
            self._render_people()
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
                    f'{job["status"]} · 重试/尝试 {job["attempts"]} 次 · {job["job_id"][:8]}',
                    job["job_id"])
        elif action in {"correct_memory", "forget_memory", "forget_person",
                        "admin_create_person", "admin_rename_person", "admin_link", "admin_consent",
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

    def _person_changed(self, *_):
        self._update_person_status()
        self.refresh_memories()

    def _selected_person_info(self) -> dict | None:
        selected = self._person_id()
        return next((person for person in self.people
                     if person["person_id"] == selected), None)

    def select_person_row(self, row: int, _column: int):
        if 0 <= row < len(self.people):
            person_id = self.people[row]["person_id"]
            for index in range(self.person.count()):
                if self.person.itemData(index) == person_id:
                    self.person.setCurrentIndex(index)
                    break

    def _render_people(self):
        self.account_table.setRowCount(len(self.people))
        for row, person in enumerate(self.people):
            identities = person.get("identities", [])
            channels = ", ".join(
                f'{item["platform"]}:{item["channel_account_id"]}:{item["external_user_id"]}'
                for item in identities) or "尚未关联"
            transcript = ("已启用" if person.get("timeline_enabled")
                          else "已撤销" if person.get("timeline_revoked")
                          else "未启用")
            curator = "已批准" if person.get("curation_enabled") else "未批准"
            cols = [
                f'{person["display_name"]} · {person["person_id"][:8]}',
                channels,
                transcript,
                curator,
                str(person.get("message_count", 0)),
                str(person.get("memory_count", 0)),
            ]
            for index, value in enumerate(cols):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.account_table.setItem(row, index, item)
        self._update_person_status()

    def _update_person_status(self):
        info = self._selected_person_info()
        if info is None:
            self.person_summary.setText("尚未选择人物。")
            self.curator_status.setText("请选择一个人物审核长期记忆提炼。")
            self.approve_curator_button.setEnabled(False)
            self.revoke_curator_button.setEnabled(False)
            return
        channels = ", ".join(
            f'{x["platform"]}:{x["channel_account_id"]}:{x["external_user_id"]}'
            for x in info.get("identities", [])) or "暂无"
        timeline = ("已启用" if info.get("timeline_enabled")
                    else "已撤销（不会自动重新启用）" if info.get("timeline_revoked")
                    else "未启用")
        curation = "已批准" if info.get("curation_enabled") else "待审核"
        jobs = info.get("jobs", {})
        self.person_summary.setText(
            f'姓名：{info["display_name"]} | ID：{info["person_id"]}\n'
            f'渠道账号：{channels}\n'
            f'本地 Timeline：{timeline}，已收录私聊 {info.get("message_count", 0)} 条；'
            f'长期记忆 {info.get("memory_count", 0)} 条。')
        self.curator_status.setText(
            f'当前人物：{info["display_name"]} | Curator：{curation}\n'
            f'本地记录：{timeline} | '
            f'待处理 {jobs.get("pending", 0)}，执行中 {jobs.get("running", 0)}，'
            f'失败 {jobs.get("failed", 0)}，完成 {jobs.get("done", 0)}。')
        self.approve_curator_button.setEnabled(
            self.client is not None and info.get("timeline_enabled", False)
            and not info.get("curation_enabled", False))
        self.revoke_curator_button.setEnabled(
            self.client is not None and info.get("curation_enabled", False))

    def manage_timeline(self):
        person = self._selected_person_info()
        if not person:
            return
        if person.get("timeline_enabled"):
            if self._confirm(
                "关闭本地留存",
                "停止该人物的新 Timeline 写入？已有历史仍然保留。"
                "如需删除已有数据，请使用 /forget_me 或删除整个人物。"):
                self._start("admin_consent", person["person_id"], "timeline", False)
        elif self._confirm(
                "启用本地留存",
                "将明确启用该人物的新消息本地保存（若已执行遗忘，视为重新授权）。继续？"):
            self._start("admin_consent", person["person_id"], "timeline", True)

    def approve_curator(self):
        person = self._selected_person_info()
        if not person or not person.get("timeline_enabled"):
            return
        if self._confirm(
                "批准长期记忆提炼",
                f'确定批准 {person["display_name"]} 的 Curator？\n'
                "本地 Timeline 将可被记忆整理模型读取；如配置外部模型，"
                "后续任务可能产生 Token 费用。此操作不会批准其他人物。"):
            self._start("admin_consent", person["person_id"], "curation", True)

    def revoke_curator(self):
        person = self._selected_person_info()
        if person and self._confirm(
                "撤销长期记忆提炼",
                f'停止 {person["display_name"]} 的后续 Curator 提炼？'
                "已生成的长期记忆不会自动删除。"):
            self._start("admin_consent", person["person_id"], "curation", False)

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

    def rename_person(self):
        person = self._selected_person_info()
        if not person:
            return
        name, ok = QInputDialog.getText(
            self, "修改人物名称", "展示名称（Person ID 与关联账号不变）：",
            text=person["display_name"],
        )
        if ok and name.strip() and name.strip() != person["display_name"]:
            self._start("admin_rename_person", person["person_id"], name.strip())

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
