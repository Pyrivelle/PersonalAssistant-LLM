"""Provider → model → function allocation UI for ZhuangFY."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QDoubleSpinBox, QSpinBox, QTableWidget, QTableWidgetItem,
    QStyledItemDelegate, QStyle, QStyleOptionViewItem, QTabWidget, QVBoxLayout,
    QWidget,
)

from llm import (LlmSettings, PROVIDERS, PROVIDER_BASE_URLS,
                 PROVIDER_DEFAULT_MODELS, PROVIDER_SUGGESTED_MODELS, load_key)
from model_catalog import ModelCatalogWorker
from model_routes import load_route_key
from zhuangfy.llm.model_registry import (
    MAX_MODELS, MAX_PROVIDERS, ModelRegistry, ProviderConnection,
    RegisteredModel, load_provider_key, load_registry,
    save_assignments as persist_registry, save_provider_key,
)
from zhuangfy.llm.model_probe import describe_probe_error, probe_model
from zhuangfy.observability.activity_log import log_event

STAGE_LABELS = {"reply": "回复模型", "planner": "Planner 思考", "vision": "图片识别",
                "memory": "记忆整理"}
THINKING_LABELS = (("沿用模型默认", "auto"), ("启用", "enabled"), ("关闭", "disabled"))

THEME = """
    QWidget#modelManagement { background: #f4f6fa; color: #263247; }
    QDialog { background: #f4f6fa; color: #263247; }
    QTabWidget::pane { border: 1px solid #dce4f0; background: white; border-radius: 10px; }
    QTabBar::tab { background: #e9eef6; color: #536784; padding: 9px 24px;
                   border: 1px solid #dce4f0; }
    QTabBar::tab:selected { background: white; color: #3d5cc4;
                            border-bottom: 2px solid #5269c8; }
    QListWidget, QTableWidget { background: white; border: 1px solid #dce4f0;
                                border-radius: 7px; alternate-background-color: #f7f9fc; }
    QListWidget::item { padding: 8px; }
    QListWidget::item:selected, QTableWidget::item:selected { background: #e1e9fa; color: #263d76; }
    QPushButton { background: #edf1f8; color: #30405d; border: 1px solid #dce4ef;
                  border-radius: 8px; padding: 6px 13px; }
    QPushButton:hover { background: #e2e9f6; }
    QPushButton[primary="true"] { background: #5269c8; color: white; border-color: #5269c8; }
    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox { background: #fbfcfe;
        border: 1px solid #cdd7e5; border-radius: 7px; padding: 5px; }
    QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled,
    QDoubleSpinBox:disabled, QCheckBox:disabled { color: #8795aa; border-color: #dce4ef; }
"""


def _button(label: str, callback, *, primary: bool = False) -> QPushButton:
    button = QPushButton(label)
    button.setProperty("primary", primary)
    button.clicked.connect(callback)
    return button


class ProviderCountDelegate(QStyledItemDelegate):
    """Keep the provider's model count aligned to the list's right edge."""

    def paint(self, painter, option, index) -> None:
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        styled.text = ""
        style = styled.widget.style() if styled.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, styled.widget)
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(QColor("#263d76") if selected else
                       option.palette.color(QPalette.ColorRole.Text))
        painter.drawText(option.rect.adjusted(12, 0, -42, 0),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         str(index.data(Qt.ItemDataRole.DisplayRole)))
        painter.drawText(option.rect.adjusted(0, 0, -12, 0),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         str(index.data(Qt.ItemDataRole.UserRole + 1)))
        painter.restore()


class ModelProbeWorker(QThread):
    checked = Signal(str, bool, str)

    def __init__(self, connection: ProviderConnection, model: RegisteredModel):
        # Keep the request alive if the settings page closes mid-test.
        super().__init__(QApplication.instance())
        self.connection = connection
        self.model = model

    def run(self) -> None:
        try:
            result = probe_model(self.connection, self.model)
            answer = result.answer.replace("\r", " ").replace("\n", " ").strip()
            detail = f"连接正常 · {result.elapsed_seconds:.1f} 秒 · 返回：{answer[:120]}"
            self.checked.emit(self.model.id, True, detail)
        except (OSError, ValueError, RuntimeError) as error:
            self.checked.emit(self.model.id, False, describe_probe_error(error))


class ProviderDialog(QDialog):
    def __init__(self, connection: ProviderConnection | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑模型供应商" if connection else "添加模型供应商")
        self.setStyleSheet(THEME)
        self.resize(580, 280)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox()
        for kind, label in PROVIDERS.items():
            self.kind.addItem(label, kind)
        if connection:
            self.kind.setCurrentIndex(max(0, self.kind.findData(connection.kind)))
        self.name = QLineEdit(connection.name if connection else "")
        self.base_url = QLineEdit(connection.base_url if connection else "")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("已有密钥可留空保留；新密钥只存本机")
        self.kind.currentIndexChanged.connect(self._template_changed)
        form.addRow("供应商模板", self.kind)
        form.addRow("名称", self.name)
        form.addRow("API Base URL", self.base_url)
        form.addRow("API Key", self.key)
        layout.addLayout(form)
        note = QLabel("同一供应商连接下的模型共享 API 地址与密钥；密钥不会写进模型目录。")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.base_url.setReadOnly(self.kind.currentData() in PROVIDER_BASE_URLS)
        if not connection:
            self._template_changed()

    def _template_changed(self, _index: int = -1) -> None:
        kind = self.kind.currentData()
        self.base_url.setReadOnly(kind in PROVIDER_BASE_URLS)
        if kind in PROVIDER_BASE_URLS:
            self.base_url.setText(PROVIDER_BASE_URLS[kind])
        elif not self.base_url.text().strip():
            self.base_url.setText("http://127.0.0.1:11434/v1")
        if not self.name.text().strip() or self.name.text() in PROVIDERS.values():
            self.name.setText(PROVIDERS.get(kind, "自定义供应商"))

    def values(self, identifier: str) -> tuple[ProviderConnection, str]:
        return (ProviderConnection(identifier, self.name.text().strip(),
                                   self.kind.currentData(), self.base_url.text().strip()).validated(),
                self.key.text().strip())

    def accept(self) -> None:
        try:
            self.values("provider_preview")
            if "\r" in self.key.text() or "\n" in self.key.text():
                raise ValueError("API Key 不能包含换行")
        except ValueError as error:
            QMessageBox.warning(self, "供应商配置无效", str(error))
            return
        super().accept()


class ModelDialog(QDialog):
    def __init__(self, registry: ModelRegistry, model: RegisteredModel | None = None,
                 selected_provider: str = "", parent=None):
        super().__init__(parent)
        self.registry = registry
        self.original_model = model
        self.worker = None
        self.setWindowTitle("编辑模型" if model else "添加模型")
        self.setStyleSheet(THEME)
        self.resize(760, 610)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(model.name if model else "")
        self.identifier = QLineEdit(model.id if model else "")
        self.identifier.setPlaceholderText("例如 kimi_k26 或 deepseek_v4_pro")
        self.identifier.setReadOnly(model is not None)
        self.provider = QComboBox()
        for connection in registry.providers:
            self.provider.addItem(connection.name, connection.id)
        chosen = model.provider_id if model else selected_provider
        self.provider.setCurrentIndex(max(0, self.provider.findData(chosen)))
        self.model_id = QComboBox()
        self.model_id.setEditable(True)
        self.model_id.setMaxVisibleItems(20)
        self.model_id.setCurrentText(model.model_id if model else "")
        self.fetch_button = _button("获取模型列表", self.fetch_models)
        remote_row = QHBoxLayout()
        remote_row.addWidget(self.model_id, 1)
        remote_row.addWidget(self.fetch_button)
        self.enabled = QCheckBox("启用模型")
        self.enabled.setChecked(model.enabled if model else True)
        form.addRow("模型名称", self.name)
        form.addRow("模型标识符", self.identifier)
        form.addRow("供应商", self.provider)
        form.addRow("API 模型 ID", remote_row)
        form.addRow("状态", self.enabled)
        layout.addLayout(form)
        self.fetch_state = QLabel("模型标识符在 ZhuangFY 内唯一；API 模型 ID 是供应商接口实际使用的名称。")
        self.fetch_state.setWordWrap(True)
        layout.addWidget(self.fetch_state)
        layout.addWidget(QLabel("参考价格（暂不可编辑）"))
        prices = QHBoxLayout()
        for caption in ("输入 ¥/M", "输出 ¥/M", "缓存 ¥/M"):
            column = QVBoxLayout()
            field = QDoubleSpinBox()
            field.setRange(0, 100000)
            field.setDecimals(2)
            field.setEnabled(False)
            field.setFixedWidth(110)
            column.addWidget(QLabel(caption))
            column.addWidget(field)
            prices.addLayout(column)
        prices.addStretch()
        layout.addLayout(prices)
        layout.addWidget(QLabel("模型能力与参数"))
        options = QFormLayout()
        self.thinking = QComboBox()
        for label, value in THINKING_LABELS:
            self.thinking.addItem(label, value)
        self.thinking.setCurrentIndex(max(0, self.thinking.findData(
            model.thinking if model else "auto")))
        options.addRow("思考模式", self.thinking)
        self.web_search = QCheckBox("启用联网搜索（DeepSeek Responses API）")
        self.web_search.setEnabled(False)
        self.web_search.setToolTip("当前请求仍使用 chat/completions；接入 Responses API 后开放。")
        options.addRow("联网搜索", self.web_search)
        self.reasoning_effort = QComboBox()
        self.reasoning_effort.addItems(("高（默认）", "低", "最大"))
        self.reasoning_effort.setEnabled(False)
        options.addRow("思考力度", self.reasoning_effort)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0, 2)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(1.0)
        self.temperature.setEnabled(False)
        options.addRow("自定义模型温度", self.temperature)
        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(1, 393216)
        self.max_tokens.setValue(8192)
        self.max_tokens.setEnabled(False)
        options.addRow("自定义最大 Token", self.max_tokens)
        layout.addLayout(options)
        pending = QLabel("温度、最大 Token 与思考力度尚未接入当前请求链路，暂不能修改。")
        pending.setWordWrap(True)
        layout.addWidget(pending)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self._provider_changed()
        if not model:
            self.model_id.setCurrentText(PROVIDER_DEFAULT_MODELS.get(self._connection().kind, ""))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _connection(self) -> ProviderConnection:
        return next(p for p in self.registry.providers if p.id == self.provider.currentData())

    def _provider_changed(self, _index: int = -1) -> None:
        connection = self._connection()
        value = self.model_id.currentText().strip()
        choices = PROVIDER_SUGGESTED_MODELS[connection.kind]
        self.model_id.clear()
        self.model_id.addItems(choices)
        self.model_id.setCurrentText(value or PROVIDER_DEFAULT_MODELS.get(connection.kind, ""))
        thinking_supported = connection.kind in {"deepseek", "siliconflow"}
        self.thinking.setEnabled(thinking_supported)
        self.thinking.setToolTip("通过当前 chat/completions 接口设置" if thinking_supported
                                else "当前供应商尚未接入手动思考模式")
        if not thinking_supported:
            self.thinking.setCurrentIndex(0)

    def fetch_models(self) -> None:
        if self.worker is not None:
            return
        try:
            provider = self._connection()
            settings = LlmSettings(provider.kind, provider.base_url,
                                   self.model_id.currentText().strip() or "_" ).validated()
            key = (load_route_key(self.original_model.id, provider.kind, provider.base_url)
                   if self.original_model else "")
            key = (key or load_provider_key(provider.id, provider.kind, provider.base_url)
                   or load_key(provider.kind))
            worker = ModelCatalogWorker(settings, key)
            self.worker = worker
            self.fetch_button.setEnabled(False)
            self.fetch_state.setText("正在获取模型列表…")
            worker.loaded.connect(self._models_loaded)
            worker.failed.connect(self.fetch_state.setText)
            worker.finished.connect(self._models_finished)
            worker.start()
        except (OSError, ValueError) as error:
            self.fetch_state.setText(str(error))

    def _models_loaded(self, choices: list[str]) -> None:
        selected = self.model_id.currentText()
        self.model_id.clear()
        self.model_id.addItems(choices)
        self.model_id.setCurrentText(selected)
        self.fetch_state.setText(f"获取到 {len(choices)} 个模型；仍可手动填写 API 模型 ID。")
        self.model_id.showPopup()

    def _models_finished(self) -> None:
        worker = self.worker
        self.worker = None
        self.fetch_button.setEnabled(True)
        if worker is not None:
            worker.deleteLater()

    def values(self) -> RegisteredModel:
        return RegisteredModel(self.identifier.text().strip(), self.name.text().strip(),
                               self.provider.currentData(), self.model_id.currentText().strip(),
                               self.enabled.isChecked(), self.thinking.currentData()).validated()

    def accept(self) -> None:
        try:
            model = self.values()
            if self.original_model is None and any(m.id == model.id for m in self.registry.models):
                raise ValueError("模型标识符已存在，请换一个唯一名称")
            connection = self._connection()
            LlmSettings(connection.kind, connection.base_url, model.model_id).validated()
        except ValueError as error:
            QMessageBox.warning(self, "模型配置无效", str(error))
            return
        super().accept()


class ModelManagementPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("modelManagement")
        self.registry = load_registry()
        self._probe_worker: ModelProbeWorker | None = None
        outer = QVBoxLayout(self)
        title = QLabel("模型管理")
        title.setObjectName("pageTitle")
        outer.addWidget(title)
        intro = QLabel("先添加模型供应商，再登记模型标识符，最后把模型分配给各项功能。")
        intro.setObjectName("muted")
        outer.addWidget(intro)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._models_tab(), "模型设置")
        self.tabs.addTab(self._assignment_tab(), "功能分配")
        outer.addWidget(self.tabs, 1)
        self.feedback = QLabel("当前配置已从本机模型路由载入；编辑后才会保存。")
        self.feedback.setObjectName("muted")
        outer.addWidget(self.feedback)
        self.setStyleSheet(THEME)
        self.refresh()

    def _models_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        left = QVBoxLayout()
        left.addWidget(QLabel("模型厂商"))
        self.providers = QListWidget()
        self.providers.setItemDelegate(ProviderCountDelegate(self.providers))
        self.providers.currentRowChanged.connect(self.refresh_models)
        left.addWidget(self.providers, 1)
        left.addWidget(_button("＋ 添加厂商", self.add_provider, primary=True))
        left.addWidget(_button("编辑厂商", self.edit_provider))
        left.addWidget(_button("移除厂商", self.remove_provider))
        layout.addLayout(left, 1)
        right = QVBoxLayout()
        actions = QHBoxLayout()
        actions.addWidget(QLabel("已登记模型"))
        actions.addStretch()
        actions.addWidget(_button("＋ 添加模型", self.add_model, primary=True))
        right.addLayout(actions)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("使用", "模型名称", "模型标识符", "供应商", "操作"))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 50)
        self.table.setColumnWidth(4, 178)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_model())
        right.addWidget(self.table, 1)
        layout.addLayout(right, 3)
        return tab

    def _assignment_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        self.stages = QListWidget()
        for stage in STAGE_LABELS:
            self.stages.addItem(STAGE_LABELS[stage])
        self.stages.currentRowChanged.connect(self.refresh_assignment)
        layout.addWidget(self.stages, 1)
        right = QVBoxLayout()
        self.stage_title = QLabel()
        self.stage_title.setObjectName("cardTitle")
        right.addWidget(self.stage_title)
        self.stage_note = QLabel("候选按顺序尝试。记忆整理模型在下一次启动记忆服务后生效。")
        self.stage_note.setObjectName("muted")
        right.addWidget(self.stage_note)
        form = QFormLayout()
        self.assignment_boxes = []
        self.assignment_labels = []
        for index in range(5):
            box = QComboBox()
            self.assignment_boxes.append(box)
            label = QLabel("首选模型" if index == 0 else f"备用模型 {index}")
            self.assignment_labels.append(label)
            form.addRow(label, box)
        right.addLayout(form)
        options = QHBoxLayout()
        self.stage_temperature = QDoubleSpinBox()
        self.stage_temperature.setRange(0, 2)
        self.stage_temperature.setValue(1.0)
        self.stage_temperature.setEnabled(False)
        options.addWidget(QLabel("温度"))
        options.addWidget(self.stage_temperature)
        self.stage_max_tokens = QSpinBox()
        self.stage_max_tokens.setRange(1, 393216)
        self.stage_max_tokens.setValue(8192)
        self.stage_max_tokens.setEnabled(False)
        options.addWidget(QLabel("最大 Token"))
        options.addWidget(self.stage_max_tokens)
        self.stage_strategy = QComboBox()
        self.stage_strategy.addItem("按候选顺序")
        self.stage_strategy.setEnabled(False)
        options.addWidget(QLabel("选择策略"))
        options.addWidget(self.stage_strategy)
        right.addLayout(options)
        note = QLabel("这些参数尚未接入分阶段调用，当前只保存上方的模型分配。")
        note.setObjectName("muted")
        right.addWidget(note)
        right.addWidget(_button("保存功能分配", self.save_function_assignment, primary=True))
        right.addStretch(1)
        layout.addLayout(right, 3)
        return tab

    def refresh(self) -> None:
        selected = (self.providers.currentItem().data(Qt.ItemDataRole.UserRole)
                    if self.providers.currentItem() else "")
        self.providers.blockSignals(True)
        self.providers.clear()
        self._add_provider_item("全部", len(self.registry.models), "")
        for connection in self.registry.providers:
            count = sum(model.provider_id == connection.id for model in self.registry.models)
            self._add_provider_item(connection.name, count, connection.id)
        chosen = next((i for i in range(self.providers.count())
                       if self.providers.item(i).data(Qt.ItemDataRole.UserRole) == selected), 0)
        self.providers.setCurrentRow(chosen)
        self.providers.blockSignals(False)
        self.refresh_models()
        if self.stages.currentRow() < 0:
            self.stages.setCurrentRow(0)
        else:
            self.refresh_assignment()

    def _add_provider_item(self, name: str, count: int, identifier: str) -> None:
        item = QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole, identifier)
        item.setData(Qt.ItemDataRole.UserRole + 1, count)
        item.setSizeHint(QSize(0, 36))
        self.providers.addItem(item)

    def refresh_models(self, _index: int = -1) -> None:
        item = self.providers.currentItem()
        selected = item.data(Qt.ItemDataRole.UserRole) if item else ""
        visible = [m for m in self.registry.models if not selected or m.provider_id == selected]
        names = {p.id: p.name for p in self.registry.providers}
        self.table.setRowCount(len(visible))
        for row, model in enumerate(visible):
            self.table.setRowHeight(row, 49)
            for col, text in enumerate(("●" if model.enabled else "○", model.name,
                                        model.id, names[model.provider_id])):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.ItemDataRole.UserRole, model.id)
                if col == 1:
                    cell.setToolTip(f"API 模型 ID：{model.model_id}\n思考：{model.thinking}")
                self.table.setItem(row, col, cell)
            controls = QWidget()
            controls_layout = QHBoxLayout(controls)
            controls_layout.setContentsMargins(2, 1, 2, 1)
            controls_layout.setSpacing(3)
            test_button = QPushButton("测试")
            test_button.setEnabled(self._probe_worker is None)
            test_button.setToolTip("向此模型发送一条内置短请求，验证密钥、模型 ID 与回复接口")
            test_button.clicked.connect(
                lambda _checked=False, identifier=model.id: self.test_model(identifier))
            controls_layout.addWidget(test_button)
            controls_layout.addWidget(_button(
                "编辑", lambda _checked=False, current=row: self._row_action(current, self.edit_model)))
            controls_layout.addWidget(_button(
                "删除", lambda _checked=False, current=row: self._row_action(current, self.remove_model)))
            self.table.setCellWidget(row, 4, controls)

    def test_model(self, identifier: str) -> None:
        if self._probe_worker is not None:
            return
        model = next((item for item in self.registry.models if item.id == identifier), None)
        if model is None:
            return
        connection = next((item for item in self.registry.providers
                           if item.id == model.provider_id), None)
        if connection is None:
            return
        worker = ModelProbeWorker(connection, model)
        self._probe_worker = worker
        self.feedback.setText(f"正在测试 {model.name} 的连接与回复…")
        log_event("模型测试", "开始", f"{connection.name} · {model.name}")
        self.refresh_models()
        worker.checked.connect(self._probe_checked)
        worker.finished.connect(self._probe_finished)
        worker.start()

    def _probe_checked(self, identifier: str, success: bool, detail: str) -> None:
        model = next((item for item in self.registry.models if item.id == identifier), None)
        label = model.name if model else identifier
        status = "成功" if success else "失败"
        self.feedback.setText(f"{label} · {status}：{detail}")
        log_event("模型测试", status, f"{label} · {detail}")

    def _probe_finished(self) -> None:
        worker = self._probe_worker
        self._probe_worker = None
        self.refresh_models()
        if worker is not None:
            worker.deleteLater()

    def _row_action(self, row: int, callback) -> None:
        self.table.selectRow(row)
        callback()

    def _selected_provider(self) -> ProviderConnection | None:
        item = self.providers.currentItem()
        name = item.data(Qt.ItemDataRole.UserRole) if item else ""
        return next((p for p in self.registry.providers if p.id == name), None)

    def _selected_model(self) -> RegisteredModel | None:
        row = self.table.currentRow()
        if row < 0 or self.table.item(row, 0) is None:
            return None
        identifier = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        return next((m for m in self.registry.models if m.id == identifier), None)

    def _save(self, value: ModelRegistry, key: tuple[ProviderConnection, str] | None = None) -> bool:
        try:
            value.validated()
            if key and key[1]:
                save_provider_key(key[0].id, key[1], key[0].kind, key[0].base_url)
            persist_registry(value)
        except (OSError, ValueError, TypeError, KeyError) as error:
            QMessageBox.warning(self, "保存失败", str(error))
            return False
        self.registry = value
        self.refresh()
        self.feedback.setText("模型管理已保存；QQ 桥接读取新路由需重启连接，记忆服务需重新启动。")
        return True

    def add_provider(self) -> None:
        if len(self.registry.providers) >= MAX_PROVIDERS:
            QMessageBox.information(self, "供应商数量", "最多添加 12 个供应商连接。")
            return
        dialog = ProviderDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        used = {p.id for p in self.registry.providers}
        identifier = next(f"provider_{i}" for i in range(1, 100) if f"provider_{i}" not in used)
        try:
            connection, key = dialog.values(identifier)
            self._save(replace(self.registry, providers=self.registry.providers + (connection,)),
                       (connection, key))
        except ValueError as error:
            QMessageBox.warning(self, "供应商配置无效", str(error))

    def edit_provider(self) -> None:
        current = self._selected_provider()
        if current is None:
            return
        dialog = ProviderDialog(current, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            changed, key = dialog.values(current.id)
            updated = tuple(changed if p.id == current.id else p for p in self.registry.providers)
            self._save(replace(self.registry, providers=updated), (changed, key))
        except ValueError as error:
            QMessageBox.warning(self, "供应商配置无效", str(error))

    def remove_provider(self) -> None:
        current = self._selected_provider()
        if current is None:
            return
        if any(m.provider_id == current.id for m in self.registry.models):
            QMessageBox.information(self, "无法移除", "请先移除这个供应商下的模型。")
            return
        self._save(replace(self.registry, providers=tuple(p for p in self.registry.providers
                                                          if p.id != current.id)))

    def add_model(self) -> None:
        if not self.registry.providers:
            QMessageBox.information(self, "先添加供应商", "请先建立一个模型供应商连接。")
            return
        if len(self.registry.models) >= MAX_MODELS:
            QMessageBox.information(self, "模型数量", "最多登记 40 个模型。")
            return
        current = self._selected_provider()
        dialog = ModelDialog(self.registry, selected_provider=current.id if current else "", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            model = dialog.values()
            self._save(replace(self.registry, models=self.registry.models + (model,)))
        except ValueError as error:
            QMessageBox.warning(self, "模型配置无效", str(error))

    def edit_model(self) -> None:
        current = self._selected_model()
        if current is None:
            return
        dialog = ModelDialog(self.registry, current, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            model = dialog.values()
            self._save(replace(self.registry, models=tuple(model if m.id == current.id else m
                                                           for m in self.registry.models)))
        except ValueError as error:
            QMessageBox.warning(self, "模型配置无效", str(error))

    def remove_model(self) -> None:
        current = self._selected_model()
        if current is None:
            return
        assigned = any(current.id in getattr(self.registry, stage) for stage in
                       ("planner", "reply", "vision")) or current.id == self.registry.memory
        if assigned:
            QMessageBox.information(self, "模型正在使用", "请先在功能分配中移除这个模型。")
            return
        self._save(replace(self.registry, models=tuple(m for m in self.registry.models
                                                       if m.id != current.id)))

    def refresh_assignment(self, index: int = -1) -> None:
        index = self.stages.currentRow()
        if index < 0:
            return
        stage = tuple(STAGE_LABELS)[index]
        self.stage_title.setText(STAGE_LABELS[stage])
        selected = (self.registry.memory,) if stage == "memory" and self.registry.memory else (
            getattr(self.registry, stage) if stage != "memory" else ())
        providers = {p.id: p.name for p in self.registry.providers}
        for position, box in enumerate(self.assignment_boxes):
            box.blockSignals(True)
            box.clear()
            box.addItem("不使用", "")
            for model in self.registry.models:
                if model.enabled:
                    box.addItem(f"{model.name} · {model.id} · {providers[model.provider_id]}", model.id)
            box.setCurrentIndex(max(0, box.findData(selected[position] if position < len(selected) else "")))
            box.setVisible(stage != "memory" or position == 0)
            self.assignment_labels[position].setVisible(stage != "memory" or position == 0)
            box.blockSignals(False)

    def save_function_assignment(self) -> None:
        index = self.stages.currentRow()
        if index < 0:
            return
        stage = tuple(STAGE_LABELS)[index]
        selected = tuple(box.currentData() for position, box in enumerate(self.assignment_boxes)
                         if (stage != "memory" or position == 0) and box.currentData())
        if stage == "memory":
            if not selected:
                QMessageBox.warning(self, "记忆整理模型", "请选择一个模型；关闭记忆整理请在记忆中心操作。")
                return
            if selected:
                model = next(m for m in self.registry.models if m.id == selected[0])
                connection = next(p for p in self.registry.providers if p.id == model.provider_id)
                key = (load_route_key(model.id, connection.kind, connection.base_url)
                       or load_provider_key(connection.id, connection.kind, connection.base_url)
                       or load_key(connection.kind))
                if len(key) < 8:
                    QMessageBox.warning(self, "记忆模型缺少密钥", "请先给该模型的供应商保存 API Key。")
                    return
            value = replace(self.registry, memory=selected[0] if selected else "")
        else:
            value = replace(self.registry, **{stage: selected})
        self._save(value)


class ModelManagementDialog(QDialog):
    """Same manager for the standalone text-test window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ZhuangFY · 模型管理")
        self.resize(1100, 720)
        layout = QVBoxLayout(self)
        layout.addWidget(ModelManagementPage(self))
