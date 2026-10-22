"""Current-launch conversation timeline built from structured activity events."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from activity_log import session_snapshot


CHANNELS = frozenset({"QQ", "文字", "语音"})
_TURN_PREFIX = re.compile(r"^turn:[a-zA-Z0-9_-]+\s*·\s*")


def _message_text(detail: str, channel: str) -> str:
    text = _TURN_PREFIX.sub("", detail)
    if channel == "QQ" and " · " in text:
        return text.split(" · ", 1)[1]
    return text


def project_turns(events: list[dict]) -> list[dict]:
    """Show only conversations, without folding unrelated background model work in."""
    turns: dict[str, dict] = {}
    for event in events:
        turn_id = str(event.get("turn_id", ""))
        channel = str(event.get("channel", ""))
        source = str(event.get("source", ""))
        phase = str(event.get("phase", ""))
        detail = str(event.get("detail", ""))
        if not turn_id or channel not in CHANNELS:
            continue
        if source == channel and phase == "消息":
            scope = detail.split(" · ", 1)[0] if channel == "QQ" else ""
            turns[turn_id] = {
                "id": turn_id, "channel": channel, "scope": scope,
                "time": str(event.get("time", ""))[11:19],
                "message": _message_text(detail, channel),
                "thinking": [], "reply": "", "reply_kind": "", "status": "处理中",
            }
            continue
        turn = turns.get(turn_id)
        if turn is None:
            continue
        if (source == "模型" and (phase == "规划结果" or
                (phase == "推理内容" and detail.split(" · ", 1)[0] in
                 {"思考", "回复", "识图"}))):
            label = "Planner 规划" if phase == "规划结果" else "模型推理"
            turn["thinking"].append((label, detail))
        elif source == channel and phase == "思考":
            turn["thinking"].append(("规划摘要", _message_text(detail, channel)))
        elif source == channel and phase == "识图":
            turn["thinking"].append(("图片理解", _message_text(detail, channel)))
        elif source == channel and phase in {"回复", "语音回复"}:
            part = _message_text(detail, channel)
            turn["reply"] = (turn["reply"] + "\n" + part).strip() if turn["reply"] else part
            turn["reply_kind"] = "语音回复" if phase == "语音回复" else "回复"
            turn["status"] = "已回复"
        elif source == channel and phase in {"失败", "模型全部失败"}:
            turn["status"] = "处理失败"
    return list(turns.values())


def _label(text: str, name: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class ChatStreamPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("streamPage")
        self._revision = -1
        self._expanded: set[str] = set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 26, 34, 26)
        layout.setSpacing(14)
        layout.addWidget(_label("聊天流", "pageTitle"))
        layout.addWidget(_label("本次启动收到的消息、思考与回复。点击思考卡片可展开完整内容。", "muted"))
        toolbar = QHBoxLayout()
        self.channel_filter = QComboBox()
        self.channel_filter.addItems(("全部入口", "QQ", "文字", "语音"))
        self.channel_filter.currentIndexChanged.connect(lambda: self.refresh(force=True))
        toolbar.addWidget(self.channel_filter)
        toolbar.addStretch()
        self.count = _label("")
        toolbar.addWidget(self.count)
        layout.addLayout(toolbar)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setObjectName("streamScroll")
        self.content = QWidget()
        self.content.setObjectName("streamContent")
        self.column = QVBoxLayout(self.content)
        self.column.setContentsMargins(0, 0, 8, 0)
        self.column.setSpacing(12)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)
        self.refresh(force=True)

    def _toggle(self, turn_id: str, button: QPushButton, full: QWidget, preview: QWidget) -> None:
        if turn_id in self._expanded:
            self._expanded.remove(turn_id)
        else:
            self._expanded.add(turn_id)
        expanded = turn_id in self._expanded
        full.setVisible(expanded)
        preview.setVisible(not expanded)
        button.setText("收起思考  ▴" if expanded else "展开思考  ▾")

    def _card(self, turn: dict) -> QFrame:
        card = QFrame()
        card.setObjectName("streamCard")
        body = QVBoxLayout(card)
        body.setContentsMargins(20, 17, 20, 17)
        body.setSpacing(11)
        title = f"{turn['channel']}  {turn['scope']}" if turn["scope"] else turn["channel"]
        body.addWidget(_label(f"{title}  ·  {turn['time']}", "streamMeta"))
        body.addWidget(_label(turn["message"], "streamIncoming"))
        thinking = turn["thinking"]
        if thinking:
            panel = QFrame()
            panel.setObjectName("streamThinking")
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(12, 10, 12, 10)
            panel_layout.setSpacing(7)
            row = QHBoxLayout()
            row.addWidget(_label(f"思考  ·  {len(thinking)} 项", "streamSection"))
            row.addStretch()
            toggle = QPushButton()
            toggle.setObjectName("streamToggle")
            row.addWidget(toggle)
            panel_layout.addLayout(row)
            content = "\n\n".join(f"{name}\n{text}" for name, text in thinking)
            preview = _label(content.replace("\n", " ")[:180] + ("…" if len(content) > 180 else ""),
                             "streamPreview")
            panel_layout.addWidget(preview)
            full = QPlainTextEdit()
            full.setObjectName("streamFull")
            full.setReadOnly(True)
            full.setPlainText(content)
            full.setFixedHeight(230)
            panel_layout.addWidget(full)
            expanded = turn["id"] in self._expanded
            full.setVisible(expanded)
            preview.setVisible(not expanded)
            toggle.setText("收起思考  ▴" if expanded else "展开思考  ▾")
            toggle.clicked.connect(lambda _checked=False, t=turn["id"], b=toggle,
                                   f=full, p=preview: self._toggle(t, b, f, p))
            body.addWidget(panel)
        if turn["reply"]:
            body.addWidget(_label(f"庄方宜 · {turn['reply_kind']}", "streamSection"))
            body.addWidget(_label(turn["reply"], "streamReply"))
        else:
            body.addWidget(_label(turn["status"], "streamPending"))
        return card

    def refresh(self, *, force: bool = False) -> None:
        if not force and not self.isVisible():
            return
        revision, events = session_snapshot()
        if revision == self._revision and not force:
            return
        self._revision = revision
        turns = project_turns(events)
        selected = self.channel_filter.currentText()
        if selected != "全部入口":
            turns = [turn for turn in turns if turn["channel"] == selected]
        turns = turns[-80:]
        self.count.setText(f"{len(turns)} 轮 · 本次启动")
        scrollbar = self.scroll.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 8
        old_position = scrollbar.value()
        while self.column.count():
            item = self.column.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if turns:
            for turn in turns:
                self.column.addWidget(self._card(turn))
        else:
            self.column.addWidget(_label("还没有对话。QQ、文字或语音消息会出现在这里。", "streamEmpty"))
        self.column.addStretch()
        scrollbar.setValue(scrollbar.maximum() if at_bottom else min(old_position, scrollbar.maximum()))
