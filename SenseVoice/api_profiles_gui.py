"""Numbered, reusable model APIs and stage assignments."""
from __future__ import annotations

import json
import os

from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from zhuangfy.llm.api_profiles import ApiProfile, ApiProfiles, MAX_PROFILES, load_profiles, save_profiles
from model_routes import STAGES, STAGE_NAMES, load_route_key, load_routes, save_route_key, save_routes
from model_routes_gui import CandidateEditor
from turn_planner import PlannerSettings
from toggle_switch import ToggleSwitch
from llm import ROOT, load_settings


class ApiProfilesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ZhuangFY · API 编号与阶段分配")
        self.resize(810, 780)
        layout = QVBoxLayout(self)
        note = QLabel("先填写 API 一号、二号等供应商与模型，再将编号分配给思考、回复、识图和记忆整理。"
                      "同一 API 可供多个阶段使用；候选从左到右依次尝试。密钥仅保存在本机。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.planner_enabled = ToggleSwitch("启用思考阶段；失败自动跳过")
        self.planner_enabled.setChecked(PlannerSettings.load(ROOT / "cognition_settings.json").enabled)
        layout.addWidget(self.planner_enabled)
        self.rows: list[CandidateEditor] = []
        self.legacy_ids: dict[str, str] = {}
        self.registry = load_profiles()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        area = QWidget()
        self.entries = QVBoxLayout(area)
        self.entries.addStretch()
        scroll.setWidget(area)
        layout.addWidget(scroll, 1)
        for profile in self.registry.profiles:
            self._append(profile)
        add = QPushButton("＋ 添加 API 编号")
        add.clicked.connect(self._add)
        layout.addWidget(add)
        self.add_button = add
        self.assignments: dict[str, list[QComboBox]] = {}
        form = QFormLayout()
        for stage in STAGES:
            row = QHBoxLayout()
            boxes = [QComboBox() for _ in range(5)]
            for box in boxes:
                row.addWidget(box)
            self.assignments[stage] = boxes
            form.addRow(STAGE_NAMES[stage] + "候选（优先 → 备用）", row)
        self.memory = QComboBox()
        form.addRow("记忆整理模型", self.memory)
        layout.addLayout(form)
        self._refresh_assignments(self.registry)
        footer = QHBoxLayout()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存 API 编号和分配")
        save.clicked.connect(self.save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        layout.addLayout(footer)

    def _append(self, profile: ApiProfile) -> None:
        row = CandidateEditor(profile.candidate(), self._remove, lambda *_: None, self)
        row.active.setText("API " + profile.id.removeprefix("api_"))
        row.legacy_id = profile.legacy_id
        self.rows.append(row)
        self.entries.insertWidget(self.entries.count() - 1, row)

    def _add(self) -> None:
        if len(self.rows) >= MAX_PROFILES:
            return
        used = {row.route_id for row in self.rows}
        number = next(n for n in range(1, MAX_PROFILES + 1) if f"api_{n}" not in used)
        setting = load_settings()
        self._append(ApiProfile(f"api_{number}", setting.provider, setting.base_url, setting.model))
        self._refresh_assignments()

    def _remove(self, row: CandidateEditor) -> None:
        self.rows.remove(row)
        self.entries.removeWidget(row)
        row.deleteLater()
        self._refresh_assignments()

    def _refresh_assignments(self, initial: ApiProfiles | None = None) -> None:
        choices = [("不使用", "")] + [("API " + r.route_id[4:], r.route_id) for r in self.rows]
        for stage, boxes in self.assignments.items():
            for index, box in enumerate(boxes):
                old = (getattr(initial, stage)[index] if initial and
                       index < len(getattr(initial, stage)) else box.currentData())
                box.clear()
                for label, key in choices:
                    box.addItem(label, key)
                box.setCurrentIndex(max(0, box.findData(old)))
        old = initial.memory if initial else self.memory.currentData()
        self.memory.clear()
        for label, key in choices:
            self.memory.addItem(label, key)
        self.memory.setCurrentIndex(max(0, self.memory.findData(old)))
        if hasattr(self, "add_button"):
            self.add_button.setEnabled(len(self.rows) < MAX_PROFILES)

    def save(self) -> None:
        try:
            profiles = tuple(ApiProfile(row.route_id, row.provider.currentData(),
                                        row.base_url.text().strip(), row.model.currentText().strip(),
                                        row.active.isChecked(), row.legacy_id)
                             for row in self.rows)
            assignments = {stage: tuple(box.currentData() for box in boxes if box.currentData())
                           for stage, boxes in self.assignments.items()}
            registry = ApiProfiles(profiles, **assignments, memory=self.memory.currentData() or "").validated()
            routes = registry.materialize(load_routes())
            keys = {}
            for row in self.rows:
                value = row.key_edit.text().strip()
                if "\r" in value or "\n" in value:
                    raise ValueError("API Key 不允许换行")
                profile = next(p for p in profiles if p.id == row.route_id)
                if not value and profile.legacy_id:
                    value = load_route_key(profile.legacy_id, profile.provider, profile.base_url)
                if value:
                    keys[profile.id] = value
            memory = next((p for p in profiles if p.id == registry.memory), None)
            if memory and not (keys.get(memory.id) or load_route_key(memory.id, memory.provider, memory.base_url)):
                raise ValueError("记忆整理模型需要已保存的 API Key")
            runtime_file = ROOT.parent / "data" / "memory_runtime.json"
            runtime = json.loads(runtime_file.read_text(encoding="utf-8")) if runtime_file.is_file() else {}
            if memory:
                runtime.update(profile_id=memory.id, provider=memory.provider,
                               base_url=memory.base_url, model=memory.model)
            cognition_file = ROOT / "cognition_settings.json"
            cognition = json.loads(cognition_file.read_text(encoding="utf-8")) if cognition_file.is_file() else {}
            cognition["enabled"] = self.planner_enabled.isChecked()
            for profile in profiles:
                if profile.id in keys:
                    save_route_key(profile.id, keys[profile.id], profile.provider, profile.base_url)
            save_routes(routes)
            save_profiles(registry)
            if memory and runtime:
                self._save_json(runtime_file, runtime)
            self._save_json(cognition_file, cognition)
            QMessageBox.information(self, "已保存", "API 编号与分配已保存。记忆服务切换将在下次启动服务时生效。")
            self.accept()
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            QMessageBox.warning(self, "保存失败", str(error))

    @staticmethod
    def _save_json(path, value):
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
