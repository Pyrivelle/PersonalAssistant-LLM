"""GUI editor for ZhuangFY's task-scoped LLM API candidates.

Route order is visual top-to-bottom. Plain JSON contains only endpoint/model
metadata. Route-specific API keys are written separately into .env.local.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from llm import (ROOT, PROVIDERS, PROVIDER_BASE_URLS, PROVIDER_DEFAULT_MODELS,
                 load_settings)
from model_routes import (
    MAX_ROUTES, ModelCandidate, ModelRoutes, StagePolicy, STAGES, STAGE_NAMES,
    load_route_key, load_routes, save_route_key, save_routes,
)
from turn_planner import PlannerSettings


class CandidateEditor(QFrame):
    def __init__(self, candidate: ModelCandidate, on_delete, on_move, parent=None):
        super().__init__(parent)
        self.route_id = candidate.id
        self.setFrameShape(QFrame.Shape.StyledPanel)
        outer = QVBoxLayout(self)
        header = QHBoxLayout()
        self.active = QCheckBox("启用 · " + candidate.id)
        self.active.setChecked(candidate.enabled)
        header.addWidget(self.active)
        header.addStretch(1)
        up = QPushButton("↑ 优先")
        up.clicked.connect(lambda: on_move(self, -1))
        down = QPushButton("↓ 次选")
        down.clicked.connect(lambda: on_move(self, 1))
        header.addWidget(up)
        header.addWidget(down)
        remove = QPushButton("移除候选")
        remove.clicked.connect(lambda: on_delete(self))
        header.addWidget(remove)
        outer.addLayout(header)
        form = QFormLayout()
        self.provider = QComboBox()
        for name, label in PROVIDERS.items():
            self.provider.addItem(label, name)
        self.provider.setCurrentIndex(self.provider.findData(candidate.provider))
        self.base_url = QLineEdit(candidate.base_url)
        self.model = QLineEdit(candidate.model)
        self.model.setPlaceholderText("模型 ID")
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("留空沿用已保存的候选密钥或供应商密钥")
        self.provider.currentIndexChanged.connect(self._provider_changed)
        form.addRow("供应商", self.provider)
        form.addRow("API Base URL", self.base_url)
        form.addRow("模型 ID", self.model)
        form.addRow("专用 API Key", self.key_edit)
        outer.addLayout(form)
        self.base_url.setReadOnly(candidate.provider in PROVIDER_BASE_URLS)
        self._update_secret_hint()

    def _update_secret_hint(self):
        try:
            has_key = bool(load_route_key(self.route_id, self.provider.currentData()))
        except (OSError, KeyError, ValueError):
            has_key = False
        self.key_edit.setPlaceholderText(
            "已有可用密钥 · 留空保留" if has_key else
            "未找到密钥 · 可填写，或使用本地无需密钥的模型服务"
        )

    def _provider_changed(self):
        provider = self.provider.currentData()
        self.base_url.setReadOnly(provider in PROVIDER_BASE_URLS)
        if provider in PROVIDER_BASE_URLS:
            self.base_url.setText(PROVIDER_BASE_URLS[provider])
        else:
            self.base_url.setText("http://127.0.0.1:11434/v1")
        self.model.setText(PROVIDER_DEFAULT_MODELS.get(provider, ""))
        self._update_secret_hint()

    def candidate(self) -> ModelCandidate:
        return ModelCandidate(
            self.route_id, self.provider.currentData(),
            self.base_url.text().strip(), self.model.text().strip(),
            self.active.isChecked(),
        ).validated()


class StageEditor(QWidget):
    def __init__(self, stage: str, policy: StagePolicy, parent=None):
        super().__init__(parent)
        self.stage = stage
        self.rows: list[CandidateEditor] = []
        outer = QVBoxLayout(self)
        note = QLabel(
            "上方候选优先；遇到可恢复故障先重试，仍失败再切换下一服务。"
            if stage != "vision" else
            "识图 API 候选预留配置：本版本尚未接入图片消息识别。"
        )
        note.setWordWrap(True)
        outer.addWidget(note)
        controls = QFormLayout()
        self.retries = QSpinBox()
        self.retries.setRange(0, 3)
        self.retries.setValue(policy.retries)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0, 10)
        self.delay.setSingleStep(0.2)
        self.delay.setSuffix(" 秒")
        self.delay.setValue(policy.retry_delay)
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(3, 120)
        self.timeout.setSuffix(" 秒")
        self.timeout.setValue(policy.timeout)
        self.total = QDoubleSpinBox()
        self.total.setRange(5, 240)
        self.total.setSuffix(" 秒")
        self.total.setValue(policy.total_timeout)
        controls.addRow("单个候选额外重试次数", self.retries)
        controls.addRow("首次重试间隔（指数退避）", self.delay)
        controls.addRow("单次请求超时", self.timeout)
        controls.addRow("阶段总等待上限", self.total)
        outer.addLayout(controls)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("＋ 添加备用 API")
        self.add_button.clicked.connect(self.add_candidate)
        buttons.addWidget(self.add_button)
        buttons.addStretch()
        outer.addLayout(buttons)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(250)
        area = QWidget()
        self.entries = QVBoxLayout(area)
        self.entries.addStretch(1)
        scroll.setWidget(area)
        outer.addWidget(scroll)
        for candidate in policy.candidates:
            self._append(candidate)
        self._update_add()

    def _append(self, candidate: ModelCandidate):
        row = CandidateEditor(candidate, self.remove_candidate, self.move_candidate, self)
        self.rows.append(row)
        self.entries.insertWidget(self.entries.count() - 1, row)

    def add_candidate(self):
        if len(self.rows) >= MAX_ROUTES:
            return
        existing = {row.route_id for row in self.rows}
        index = next(i for i in range(1, MAX_ROUTES + 1)
                     if f"{self.stage}_{i}" not in existing)
        default = load_settings()
        self._append(ModelCandidate(
            f"{self.stage}_{index}", default.provider, default.base_url, default.model))
        self._update_add()

    def move_candidate(self, row: CandidateEditor, direction: int):
        position = self.rows.index(row)
        target = position + direction
        if target < 0 or target >= len(self.rows):
            return
        self.rows.pop(position)
        self.rows.insert(target, row)
        self.entries.removeWidget(row)
        self.entries.insertWidget(target, row)

    def remove_candidate(self, row: CandidateEditor):
        self.rows.remove(row)
        self.entries.removeWidget(row)
        row.deleteLater()
        self._update_add()

    def _update_add(self):
        self.add_button.setEnabled(len(self.rows) < MAX_ROUTES)

    def policy(self) -> StagePolicy:
        return StagePolicy(
            candidates=tuple(row.candidate() for row in self.rows),
            retries=self.retries.value(),
            retry_delay=self.delay.value(),
            timeout=self.timeout.value(),
            total_timeout=self.total.value(),
        ).validated()


class ModelRoutesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ZhuangFY · 分阶段 API 路由与容灾")
        self.resize(760, 730)
        self.setMinimumSize(580, 530)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "不同任务可以分别设置多个 API。按列表优先级依次尝试；所有思考模型不可用时，"
            "仍会直接调用回复模型。没有配置候选时沿用旧版模型设置。"
            "注意：切换至其他供应商会将当前对话及相关记忆发送给那个供应商。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.planner_enabled = QCheckBox("启用思考阶段（Planner）；失败自动跳过")
        self.planner_enabled.setChecked(
            PlannerSettings.load(ROOT / "cognition_settings.json").enabled)
        layout.addWidget(self.planner_enabled)
        self.tabs = QTabWidget()
        config = load_routes()
        self.editors = {}
        for stage in STAGES:
            panel = StageEditor(stage, config.for_stage(stage))
            self.editors[stage] = panel
            self.tabs.addTab(panel, STAGE_NAMES[stage] + "模型")
        layout.addWidget(self.tabs)
        footer = QHBoxLayout()
        hint = QLabel("配置不含密钥；Key 仅写入本机 .env.local")
        hint.setWordWrap(True)
        footer.addWidget(hint, 1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存 API 路由")
        save.clicked.connect(self.save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        layout.addLayout(footer)

    def save(self):
        try:
            routes = ModelRoutes(**{
                name: self.editors[name].policy() for name in STAGES}).validated()
            # Check all constraints before writing anything to disk.
            for panel in self.editors.values():
                for row in panel.rows:
                    text = row.key_edit.text()
                    if "\n" in text or "\r" in text:
                        raise ValueError("API Key 不允许换行")
            for panel in self.editors.values():
                for row in panel.rows:
                    if row.key_edit.text().strip():
                        save_route_key(row.route_id, row.key_edit.text())
            save_routes(routes)
            # Preserve all older cognition flags and owner-memory permissions.
            filename = ROOT / "cognition_settings.json"
            data = json.loads(filename.read_text(encoding="utf-8")) if filename.is_file() else {}
            if not isinstance(data, dict):
                raise ValueError("原有认知设置不是对象")
            data["enabled"] = self.planner_enabled.isChecked()
            temporary = filename.with_suffix(".tmp")
            try:
                temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                os.replace(temporary, filename)
            finally:
                temporary.unlink(missing_ok=True)
            QMessageBox.information(self, "保存成功",
                "API 路由已保存。桌面下一条消息生效；QQ 桥接的思考开关需要重启桥接。")
            self.accept()
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            QMessageBox.warning(self, "保存失败", str(error))
