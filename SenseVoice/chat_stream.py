"""Current-launch conversation timeline built from structured activity events."""

from __future__ import annotations

import re
import json
import os
import time

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from activity_log import chat_snapshot
from branding import ICON_PATH
from llm import load_settings
from owner_memory import current_bot_id, load_link
from persona_packs import matching_pack
from zhuangfy.ui.follow_scroll import FollowScroll
from zhuangfy.memory.client import MemoryClient


CHANNELS = frozenset({"QQ", "文字", "语音"})
_TURN_PREFIX = re.compile(r"^turn:[a-zA-Z0-9_-]+\s*·\s*")
_ACTION = re.compile(r"行为：(reply|wait|stop|tool)\b")
_ACTION_LABELS = {"reply": "立即回复", "wait": "等待补充", "stop": "本轮不回复", "tool": "调用工具"}


def _message_text(detail: str, channel: str) -> str:
    text = _TURN_PREFIX.sub("", detail)
    if channel == "QQ" and " · " in text:
        return text.split(" · ", 1)[1]
    return text


def project_turns(events: list[dict]) -> list[dict]:
    """Show only conversations, without folding unrelated background model work in."""
    turns: dict[str, dict] = {}
    confirmed_turns = {str(event.get("turn_id", "")) for event in events
                       if event.get("source") == "模型" and event.get("phase") == "规划确认"}
    vision_content_turns = {str(event.get("turn_id", "")) for event in events
                            if event.get("source") == event.get("channel")
                            and event.get("phase") == "识图内容"}
    delivery_tracked_turns = {str(event.get("turn_id", "")) for event in events
                              if event.get("source") == "QQ" and event.get("phase") in
                              {"投递开始", "已投递", "未完全投递", "投递状态不明", "投递受限"}}
    last_by_scope: dict[tuple[str, str], str] = {}
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
            message = _message_text(detail, channel)
            scope_key = (channel, scope)
            previous = turns.get(last_by_scope.get(scope_key, ""))
            round_base = (previous["round_count"] if previous is not None
                          and previous["status"] in {"等待补充", "定时等待"}
                          and message.startswith(previous["message"]) else 0)
            turns[turn_id] = {
                "id": turn_id, "channel": channel, "scope": scope,
                "time": str(event.get("time", ""))[11:19],
                "message": message,
                "thinking": [], "reply": "", "reply_parts": [],
                "reply_kind": "", "status": "处理中",
                "round_count": round_base, "planner_open": False, "decision": "",
            }
            last_by_scope[scope_key] = turn_id
            continue
        turn = turns.get(turn_id)
        if turn is None:
            continue
        if source == "模型" and phase == "系统提示词":
            try:
                prompt_event = json.loads(detail)
            except (ValueError, TypeError):
                continue
            if not isinstance(prompt_event, dict):
                continue
            stage = str(prompt_event.get("stage") or "未记录阶段")
            if turn["status"] == "已回复" and stage == "思考":
                continue
            model = str(prompt_event.get("model") or "未记录模型")[:90]
            count = prompt_event.get("system_count", 0)
            chars = prompt_event.get("system_chars", 0)
            digest = str(prompt_event.get("system_sha256") or "")
            role_prompt = prompt_event.get("role_prompt")
            body = f"实际 system 消息：{count} 条 · {chars} 字\nSHA-256：{digest}"
            if isinstance(role_prompt, str) and role_prompt:
                body += "\n\n本次注入的庄方宜角色提示词：\n" + role_prompt
                body += "\n\n其他系统约束可能包含私人上下文，此处不展开。"
            else:
                body += "\n\n本次未识别到内置庄方宜角色包；可能使用了独立或自定义系统提示词。"
            turn["thinking"].append((f"系统提示词 · {stage} · {model}", body))
            continue
        if source == "模型" and phase == "输入原句":
            try:
                model_input = json.loads(detail)
            except (ValueError, TypeError):
                continue
            if not isinstance(model_input, dict) or not isinstance(model_input.get("text"), str):
                continue
            stage = str(model_input.get("stage") or "未记录阶段")
            if turn["status"] == "已回复" and stage == "思考":
                continue
            model = str(model_input.get("model") or "未记录模型")[:90]
            body = "原句：\n" + model_input["text"]
            if model_input.get("truncated"):
                body += "\n[原句较长，此处仅显示前段]"
            body += f"\n\n历史消息：{model_input.get('history_count', 0)} 条（正文未复制到此项）"
            if model_input.get("has_image"):
                body += "\n图片：已附加（原始数据未复制到此项）"
            turn["thinking"].append((f"模型输入 · {stage} · {model}", body))
            continue
        if source == "模型" and phase == "规划尝试" and turn_id in confirmed_turns:
            # A failed candidate or formatting repair must not lend its
            # reasoning to the model that eventually supplied the answer.
            pending_label = f"第{turn['round_count'] + 1}轮思考"
            turn["thinking"] = [(label, body) for label, body in turn["thinking"]
                                if not label.startswith(pending_label)]
            continue
        if source == "模型" and phase == "规划确认":
            if turn["status"] == "已回复":
                continue
            try:
                result = json.loads(detail)
            except (ValueError, TypeError):
                continue
            if not isinstance(result, dict) or not isinstance(result.get("answer"), str):
                continue
            turn["round_count"] += 1
            model = str(result.get("model") or "未记录模型")[:90]
            turn["thinking"].append((f"第{turn['round_count']}轮回答 · {model}",
                                     result["answer"]))
            continue
        if (source == "模型" and (phase == "规划结果" or
                (phase == "推理内容" and detail.split(" · ", 1)[0] in
                 {"思考", "回复", "识图"}))):
            stage = detail.split(" · ", 1)[0]
            if turn["status"] == "已回复" and (phase == "规划结果" or stage == "思考"):
                # Old bridges may attach post-delivery R1 review to the chat
                # turn; it is not another conversational Planner round.
                continue
            if phase == "规划结果" and turn_id in confirmed_turns:
                # Provider output may be rejected and retried. Only the
                # validated Planner result becomes this round's answer.
                continue
            if phase == "规划结果" or stage == "思考":
                if turn_id in confirmed_turns:
                    number = turn["round_count"] + 1
                elif not turn["planner_open"]:
                    turn["round_count"] += 1
                    turn["planner_open"] = True
                    number = turn["round_count"]
                else:
                    number = turn["round_count"]
                label = (f"第{number}轮思考 · "
                         + ("Planner 规划" if phase == "规划结果" else "模型推理"))
                if phase == "规划结果":
                    turn["planner_open"] = False
            else:
                label = "回复阶段推理" if stage == "回复" else "识图阶段推理"
            turn["thinking"].append((label, detail))
        elif source == channel and phase == "思考V2":
            match = _ACTION.search(detail)
            if match:
                decision = _ACTION_LABELS[match.group(1)]
                turn["decision"] = decision
                turn["thinking"].append((f"第{turn['round_count'] or 1}轮结论 · {decision}",
                                         _message_text(detail, channel)))
            else:
                turn["thinking"].append(("规划摘要", _message_text(detail, channel)))
        elif source == channel and phase == "等待V2":
            delayed = "用户指定延迟" in detail
            wait_label = "定时等待" if delayed else "等待补充"
            turn["status"] = wait_label
            turn["decision"] = wait_label
            if delayed:
                for index in range(len(turn["thinking"]) - 1, -1, -1):
                    label, body = turn["thinking"][index]
                    if label.endswith("结论 · 等待补充"):
                        turn["thinking"][index] = (label.removesuffix("等待补充") + wait_label, body)
                        break
            turn["thinking"].append(("等待计时", _message_text(detail, channel)))
        elif source == channel and phase == "定时已创建":
            turn["status"] = "定时已创建"
            turn["decision"] = "已创建定时任务"
            turn["thinking"].append(("定时任务", _message_text(detail, channel)))
        elif source == channel and phase in {"定时生成开始", "定时已生成"}:
            turn["status"] = "到期生成中" if phase == "定时生成开始" else "待投递"
            turn["thinking"].append(("到期生成", _message_text(detail, channel)))
        elif source == channel and phase == "定时生成失败":
            turn["status"] = "生成失败，等待重试"
            turn["thinking"].append(("到期生成", _message_text(detail, channel)))
        elif source == channel and phase == "定时任务失败":
            turn["status"] = "定时任务失败"
            turn["thinking"].append(("到期生成", _message_text(detail, channel)))
        elif source == channel and phase in {"定时创建失败", "定时投递状态不明"}:
            turn["status"] = "定时任务失败" if phase == "定时创建失败" else "投递状态不明"
            turn["thinking"].append(("定时任务", _message_text(detail, channel)))
        elif source == channel and phase in {"提醒已送达", "定时回复已送达"}:
            part = _message_text(detail, channel)
            turn["reply_parts"].append(part)
            turn["reply"] = (turn["reply"] + "\n" + part).strip() if turn["reply"] else part
            turn["status"] = phase
            turn["thinking"].append(("定时任务", "QQ 已确认投递"))
        elif source == channel and phase == "静默V2":
            turn["status"] = "本轮不回复"
            turn["decision"] = "本轮不回复"
        elif source == channel and phase == "思考":
            turn["thinking"].append(("规划摘要", _message_text(detail, channel)))
        elif source == channel and phase == "识图":
            if turn_id not in vision_content_turns:
                turn["thinking"].append(("图片理解（旧记录仅摘要）", _message_text(detail, channel)))
        elif source == channel and phase == "识图内容":
            try:
                content = json.loads(detail)
            except (ValueError, TypeError):
                content = None
            if isinstance(content, dict) and isinstance(content.get("description"), str):
                body = "来源：" + str(content.get("source") or "模型") + "\n" + content["description"]
                if content.get("context_note"):
                    body += "\n\n此前用户对同图的解释（仅供参考）：\n" + str(content["context_note"])
                turn["thinking"].append(("图片理解", body))
        elif source == "Agent" and phase in {"工具开始", "工具结束"}:
            turn["thinking"].append(("工具执行", _message_text(detail, channel)))
        elif source == channel and phase == "搜索结果":
            turn["thinking"].append(("联网搜索结果", _message_text(detail, channel)))
        elif source == channel and phase == "投递开始":
            turn["status"] = "投递中"
        elif source == channel and phase in {"投递状态不明", "投递受限", "未完全投递", "补发未完成"}:
            if phase == "投递状态不明" or "确认状态不明" in detail:
                turn["status"] = "投递状态不明"
            elif phase == "投递受限" or "发送被限制" in detail:
                turn["status"] = "投递受限"
            elif turn["status"] not in {"投递状态不明", "投递受限"}:
                turn["status"] = "部分投递"
            turn["thinking"].append(("投递状态", _message_text(detail, channel)))
        elif source == channel and phase == "已投递":
            turn["status"] = "已回复"
        elif source == channel and phase == "取消补发":
            turn["status"] = "已取消补发"
            turn["thinking"].append(("投递状态", _message_text(detail, channel)))
        elif source == channel and phase == "控制完成":
            turn["status"] = "指令已处理"
            turn["thinking"].append(("控制指令", detail))
        elif source == channel and phase in {"回复", "语音回复"}:
            part = _message_text(detail, channel)
            turn["reply_parts"].append(part)
            turn["reply"] = (turn["reply"] + "\n" + part).strip() if turn["reply"] else part
            turn["reply_kind"] = "语音回复" if phase == "语音回复" else "回复"
            turn["status"] = ("定时已创建" if turn["decision"] == "已创建定时任务"
                              else "已确认片段" if channel == "QQ" and turn_id in delivery_tracked_turns
                              else "已回复")
            if not turn["decision"]:
                turn["decision"] = "立即回复"
        elif source == channel and phase in {"失败", "模型全部失败"}:
            turn["status"] = "处理失败"
            turn["thinking"].append(("处理失败", _message_text(detail, channel)))
    return list(turns.values())


def _label(text: str, name: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def conversation_id(turn: dict, owner_qq: int | None = None) -> str:
    """Group by interlocutor; text, voice and the linked owner's QQ share a room."""
    if turn["channel"] != "QQ":
        return "owner"
    kind = turn["scope"].partition(":")[0]
    parts = turn["scope"].split(":")
    if kind == "group" and len(parts) == 3:
        return "group:" + parts[1]
    if kind == "private" and len(parts) == 3:
        return "owner" if owner_qq is not None and parts[2] == str(owner_qq) else "person:" + parts[2]
    return "other:" + turn["scope"]


def qq_display_names(people: list[dict], bot_id: int | None) -> dict[str, str]:
    """Use only identities verified for the currently connected QQ bot."""
    if bot_id is None:
        return {}
    names = {}
    for person in people:
        name = str(person.get("display_name", "")).strip()
        if not name:
            continue
        for identity in person.get("identities", []):
            if (identity.get("platform") == "qq" and
                    identity.get("channel_account_id") == str(bot_id)):
                names[str(identity.get("external_user_id", ""))] = name
    return names


def conversation_title(key: str, names: dict[str, str] | None = None) -> str:
    if key == "owner":
        return "与你的对话"
    if key.startswith("person:"):
        user_id = key.partition(":")[2]
        name = (names or {}).get(user_id, "").strip()
        return f"与{name}的对话" if name else "与用户 " + user_id + " 的对话"
    if key.startswith("group:"):
        return "群聊 " + key.partition(":")[2]
    return "其他对话"


class PersonLabelsTask(QThread):
    loaded = Signal(object)

    def run(self) -> None:
        try:
            client = MemoryClient.from_environment(
                enabled_var="ZHUANGFY_MEMORY_ADMIN_UI", admin=True)
            if client is not None:
                self.loaded.emit(client.list_people())
        except (OSError, ValueError, RuntimeError):
            # Names are optional; the chat stream remains usable offline.
            pass


def _thought_body(detail: str) -> str:
    return detail.split("\n", 1)[1] if "\n" in detail and " · " in detail.split("\n", 1)[0] else detail


def thought_sections(thinking: list[tuple[str, str]]) -> list[tuple[str, str, str, str]]:
    """Coalesce Planner reasoning, decision JSON and host action by round."""
    sections: list[dict] = []
    for label, detail in thinking:
        match = re.match(r"第(\d+)轮(?:思考|回答|结论)", label)
        key = "planner:" + match.group(1) if match else "stage:" + str(len(sections))
        if not sections or sections[-1]["key"] != key:
            sections.append({"key": key, "title": ("Planner · 第" + match.group(1) + "轮")
                             if match else label, "items": []})
        if match and "轮回答 · " in label:
            sections[-1]["title"] += " · " + label.split("轮回答 · ", 1)[1]
        sections[-1]["items"].append((label, _thought_body(detail)))
    result = []
    for section in sections:
        items = section["items"]
        preview = ""
        for label, body in reversed(items):
            if "规划结果" in label:
                try:
                    plan = json.loads(body)
                    preview = str(plan.get("reply_act") or plan.get("social_intent") or plan.get("action") or "")
                except (ValueError, TypeError, AttributeError):
                    pass
            if preview:
                break
        if not preview:
            preview = items[-1][1].replace("\n", " ")
        if section["key"].startswith("planner:"):
            reasoning = [body for label, body in items if "轮思考" in label and "模型推理" in label]
            answers = [body for label, body in items if "轮回答" in label or "Planner 规划" in label]
            decisions = [body for label, body in items if "轮结论" in label]
            full = ("思考：\n" + ("\n".join(reasoning) if reasoning else "模型未返回可展示的独立推理")
                    + "\n\n回答：\n" + ("\n".join(answers) if answers else "尚无有效规划结果"))
            if decisions:
                full += "\n\n宿主执行：\n" + "\n".join(decisions)
        else:
            full = "\n\n".join(f"{label}\n{body}" for label, body in items)
        result.append((section["key"], section["title"], preview[:150], full))
    return result


class ChatStreamPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("streamPage")
        self._revision = -1
        self._expanded: set[str] = set()
        self._selected = "owner"
        self._rendered_ids: list[str] = []
        self._turn_widgets: dict[str, QWidget] = {}
        self._signatures: dict[str, tuple] = {}
        self._conversation_keys: list[str] = []
        self._name_by_qq: dict[str, str] = {}
        self._labels_task: PersonLabelsTask | None = None
        self._labels_due = 0.0
        self.role_name = self._active_role_name()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        rail = QFrame()
        rail.setObjectName("streamRail")
        rail.setFixedWidth(226)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(17, 23, 17, 16)
        rail_layout.setSpacing(11)
        rail_layout.addWidget(_label("聊天流", "streamRailTitle"))
        rail_layout.addWidget(_label("角色", "streamRailCaption"))
        role = QFrame()
        role.setObjectName("streamRole")
        role_row = QHBoxLayout(role)
        role_row.setContentsMargins(10, 10, 10, 10)
        avatar = _label("", "streamAvatar")
        portrait = QPixmap(str(ICON_PATH))
        if not portrait.isNull():
            avatar.setPixmap(portrait.scaled(38, 38, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
        role_row.addWidget(avatar)
        name_column = QVBoxLayout()
        name_column.setSpacing(2)
        name_column.addWidget(_label(self.role_name, "streamRoleName"))
        name_column.addWidget(_label("当前角色", "streamRoleHint"))
        role_row.addLayout(name_column)
        rail_layout.addWidget(role)
        rail_layout.addWidget(_label("会话", "streamRailCaption"))
        self.search = QLineEdit()
        self.search.setObjectName("streamSearch")
        self.search.setPlaceholderText("搜索当前会话…")
        self.search.textChanged.connect(lambda _text: self.refresh(force=True))
        rail_layout.addWidget(self.search)
        self.conversations = QListWidget()
        self.conversations.setObjectName("streamConversations")
        self.conversations.currentItemChanged.connect(self._select_conversation)
        rail_layout.addWidget(self.conversations, 1)
        rail_layout.addWidget(_label("最近的对话保存在本机", "streamRoleHint"))
        layout.addWidget(rail)

        main = QWidget()
        main.setObjectName("streamMain")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(23, 20, 23, 12)
        main_layout.setSpacing(12)
        heading = QHBoxLayout()
        title_column = QVBoxLayout()
        title_column.setSpacing(2)
        self.conversation_title = _label("与你的对话", "streamMainTitle")
        title_column.addWidget(self.conversation_title)
        self.count = _label("", "streamRoleHint")
        title_column.addWidget(self.count)
        heading.addLayout(title_column)
        heading.addStretch()
        bottom_button = QPushButton("跳到最新")
        bottom_button.setObjectName("streamJump")
        bottom_button.clicked.connect(self.jump_to_latest)
        heading.addWidget(bottom_button)
        main_layout.addLayout(heading)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setObjectName("streamScroll")
        self.content = QWidget()
        self.content.setObjectName("streamContent")
        self.column = QVBoxLayout(self.content)
        self.column.setContentsMargins(0, 2, 7, 8)
        self.column.setSpacing(8)
        self.column.addStretch()
        self.scroll.setWidget(self.content)
        self.scroll_follow = FollowScroll(self.scroll.verticalScrollBar())
        main_layout.addWidget(self.scroll, 1)
        layout.addWidget(main, 1)
        self.refresh(force=True)

    @staticmethod
    def _active_role_name() -> str:
        try:
            pack = matching_pack(load_settings().system_prompt)
            return pack.name.split(" · ", 1)[0] if pack else "庄方宜"
        except (OSError, ValueError):
            return "庄方宜"

    def _select_conversation(self, current: QListWidgetItem | None,
                             _previous: QListWidgetItem | None) -> None:
        if current is not None:
            self._selected = current.data(Qt.ItemDataRole.UserRole)
            self.scroll_follow.jump_to_latest()
            self.refresh(force=True)

    def _refresh_names(self) -> None:
        if (os.environ.get("ZHUANGFY_MEMORY_ADMIN_UI") != "1"
                or not os.environ.get("ZHUANGFY_MEMORY_ADMIN_TOKEN")
                or time.monotonic() < self._labels_due
                or self._labels_task is not None):
            return
        self._labels_due = time.monotonic() + 30
        task = PersonLabelsTask(self)
        self._labels_task = task
        task.loaded.connect(self._names_loaded)
        task.finished.connect(lambda: setattr(self, "_labels_task", None))
        task.finished.connect(task.deleteLater)
        task.start()

    def _names_loaded(self, people: list[dict]) -> None:
        bot_id = current_bot_id()
        names = qq_display_names(people, bot_id)
        if names != self._name_by_qq:
            self._name_by_qq = names
            self.refresh(force=True)

    def jump_to_latest(self) -> None:
        self.scroll_follow.jump_to_latest()

    def _toggle(self, key: str, button: QPushButton, full: QWidget, preview: QWidget) -> None:
        if key in self._expanded:
            self._expanded.remove(key)
        else:
            self._expanded.add(key)
        expanded = key in self._expanded
        full.setVisible(expanded)
        preview.setVisible(not expanded)
        button.setText("收起  ▴" if expanded else "展开  ▾")

    def _speaker(self, name: str, time: str, *, assistant: bool) -> QWidget:
        row = QWidget()
        row.setObjectName("streamSpeaker")
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        icon = _label("", "streamSpeakerAvatar")
        if assistant:
            portrait = QPixmap(str(ICON_PATH))
            if not portrait.isNull():
                icon.setPixmap(portrait.scaled(27, 27, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
        else:
            icon.setText("你")
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        line.addWidget(icon)
        line.addWidget(_label(name, "streamSpeakerName"))
        line.addWidget(_label(time, "streamRoleHint"))
        line.addStretch()
        return row

    def _thinking_card(self, turn_id: str, section: tuple[str, str, str, str]) -> QWidget:
        section_key, title, summary, content = section
        card = QFrame()
        card.setObjectName("streamThinking")
        body = QVBoxLayout(card)
        body.setContentsMargins(13, 10, 13, 10)
        body.setSpacing(6)
        header = QHBoxLayout()
        planner_model = ""
        heading = title
        if section_key.startswith("planner:") and title.count(" · ") >= 2:
            heading, planner_model = title.rsplit(" · ", 1)
        heading_label = _label("◈  " + heading, "streamSection")
        heading_label.setWordWrap(False)
        header.addWidget(heading_label)
        header.addStretch()
        toggle = QPushButton()
        toggle.setObjectName("streamToggle")
        header.addWidget(toggle)
        body.addLayout(header)
        if planner_model:
            model_label = _label("模型：" + planner_model, "streamModel")
            model_label.setWordWrap(False)
            model_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            model_label.setToolTip(planner_model)
            body.addWidget(model_label)
        preview = _label(summary, "streamPreview")
        body.addWidget(preview)
        full = QPlainTextEdit()
        full.setObjectName("streamFull")
        full.setReadOnly(True)
        full.setPlainText(content)
        full.setFixedHeight(190)
        body.addWidget(full)
        key = turn_id + ":" + section_key
        expanded = key in self._expanded
        full.setVisible(expanded)
        preview.setVisible(not expanded)
        toggle.setText("收起  ▴" if expanded else "展开  ▾")
        toggle.clicked.connect(lambda _checked=False, k=key, b=toggle,
                               f=full, p=preview: self._toggle(k, b, f, p))
        return card

    def _card(self, turn: dict) -> QFrame:
        card = QFrame()
        card.setObjectName("streamTurn")
        body = QVBoxLayout(card)
        body.setContentsMargins(9, 14, 9, 18)
        body.setSpacing(9)
        body.addWidget(self._speaker("你", turn["time"], assistant=False))
        incoming = QFrame()
        incoming.setObjectName("streamUserBubble")
        incoming_layout = QVBoxLayout(incoming)
        incoming_layout.setContentsMargins(13, 10, 13, 10)
        incoming_layout.addWidget(_label(turn["message"], "streamIncoming"))
        body.addWidget(incoming)
        for section in thought_sections(turn["thinking"]):
            body.addWidget(self._thinking_card(turn["id"], section))
        if turn["decision"] and not any(label.startswith("第") and "轮结论" in label
                                        for label, _ in turn["thinking"]):
            body.addWidget(_label("结论 · " + turn["decision"], "streamDecision"))
        if turn["reply_parts"]:
            body.addWidget(self._speaker(self.role_name, turn["time"], assistant=True))
            for part in turn["reply_parts"]:
                reply = QFrame()
                reply.setObjectName("streamAssistantBubble")
                reply_layout = QVBoxLayout(reply)
                reply_layout.setContentsMargins(13, 10, 13, 10)
                reply_layout.addWidget(_label(part, "streamReply"))
                body.addWidget(reply)
            if turn["status"] != "已回复":
                body.addWidget(_label(turn["status"], "streamPending"))
        else:
            body.addWidget(_label(turn["status"], "streamPending"))
        return card

    def refresh(self, *, force: bool = False) -> None:
        if not force and not self.isVisible():
            return
        self._refresh_names()
        revision, events = chat_snapshot()
        if revision == self._revision and not force:
            return
        self._revision = revision
        turns = project_turns(events)
        link = load_link(validate=False)
        owner_qq = link.qq_id if link else None
        groups: dict[str, list[dict]] = {"owner": []}
        for turn in turns:
            groups.setdefault(conversation_id(turn, owner_qq), []).append(turn)
        keys = ["owner"] + sorted((key for key in groups if key != "owner"),
                                   key=lambda key: turns.index(groups[key][-1]), reverse=True)
        if keys != self._conversation_keys or self.conversations.count() != len(keys):
            self._conversation_keys = keys
            self.conversations.blockSignals(True)
            self.conversations.clear()
            for key in keys:
                item = QListWidgetItem(conversation_title(key, self._name_by_qq))
                item.setData(Qt.ItemDataRole.UserRole, key)
                self.conversations.addItem(item)
            self.conversations.blockSignals(False)
        else:
            for index, key in enumerate(keys):
                title = conversation_title(key, self._name_by_qq)
                if self.conversations.item(index).text() != title:
                    self.conversations.item(index).setText(title)
        if self._selected not in groups:
            self._selected = "owner"
        for index in range(self.conversations.count()):
            if self.conversations.item(index).data(Qt.ItemDataRole.UserRole) == self._selected:
                if self.conversations.currentRow() != index:
                    self.conversations.blockSignals(True)
                    self.conversations.setCurrentRow(index)
                    self.conversations.blockSignals(False)
                break
        selected_turns = groups[self._selected]
        query = self.search.text().strip().casefold()
        if query:
            selected_turns = [turn for turn in selected_turns
                              if query in (turn["message"] + "\n" + turn["reply"] + "\n"
                                           + "\n".join(detail for _, detail in turn["thinking"])).casefold()]
        selected_turns = selected_turns[-80:]
        self.conversation_title.setText(conversation_title(self._selected, self._name_by_qq))
        self.count.setText(f"{self.role_name} · {len(selected_turns)} 轮 · 本机记录")
        ids = [turn["id"] for turn in selected_turns]
        self.scroll_follow.begin_update()
        if ids != self._rendered_ids:
            while self.column.count() > 1:
                item = self.column.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            self._rendered_ids = ids
            self._turn_widgets.clear()
            self._signatures.clear()
            if not selected_turns:
                self.column.insertWidget(0, _label("这条会话在本次启动后还没有消息。", "streamEmpty"))
        for index, turn in enumerate(selected_turns):
            signature = (turn["message"], tuple(turn["thinking"]), tuple(turn["reply_parts"]),
                         turn["status"], turn["decision"])
            if self._signatures.get(turn["id"]) == signature:
                continue
            card = self._card(turn)
            old = self._turn_widgets.get(turn["id"])
            if old is not None:
                self.column.removeWidget(old)
                old.deleteLater()
            self.column.insertWidget(index, card)
            self._turn_widgets[turn["id"]] = card
            self._signatures[turn["id"]] = signature
        self.scroll_follow.end_update()
