"""Run a single model request without blocking the Qt event loop."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget

from llm import ROOT, LlmSettings
from model_routes import invoke_stage
from uuid import uuid4
from turn_planner import PlannerSettings, TurnPlan, plan_turn
from emotion import EmotionState
from fangyi_skill import guidance as fangyi_guidance
from activity_log import log_event


class ChatWorker(QThread):
    replied = Signal(str)
    failed = Signal(str)
    memory_warning = Signal(str)

    def __init__(self, settings: LlmSettings, key: str, history: list[dict[str, str]], message: str, parent: QWidget, emotion: EmotionState | None = None, context_notes: str = "", source: str = "文字"):
        super().__init__(parent)
        self.settings, self.key, self.history, self.message = settings, key, history, message
        self.memory_command = False
        self.emotion_candidate = emotion.observe(message) if emotion is not None else None
        self.context_notes = context_notes
        self.source = source
        self.turn_id = uuid4().hex[:12]

    def run(self) -> None:
        log_event(self.source, "消息", f"turn:{self.turn_id} · {self.message}")
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
                    log_event("记忆", "检索", f"{self.source} · 找到 {len(memories)} 条相关记忆")
                except Exception as error:
                    if self.memory_command:
                        raise
                    self.memory_warning.emit(f"记忆检索失败，本轮仍可对话：{error}")
                    log_event("记忆", "失败", f"{self.source} · 检索失败：{type(error).__name__}")
            context_notes = self.context_notes
            if self.source == "语音" and not self.memory_command:
                try:
                    from owner_memory import load_link, memory_snapshot
                    owner = load_link()
                    if owner is not None:
                        shared = memory_snapshot(owner)
                        if shared:
                            context_notes = shared + "\n" + context_notes
                        log_event("记忆", "检索", "语音 · 已读取关联 QQ 用户的个人档案" if shared else
                                  "语音 · 关联用户尚无已提炼档案")
                except Exception as error:
                    self.memory_warning.emit(f"共享记忆读取失败，本轮仍可对话：{error}")
                    log_event("记忆", "失败", f"语音共享记忆检索失败：{type(error).__name__}")
            plan = TurnPlan()
            planner_config = PlannerSettings()
            try:
                planner_config = PlannerSettings.load(ROOT / "cognition_settings.json")
                plan = plan_turn(self.settings, self.key, self.history, self.message, planner_config)
                log_event(self.source, "思考", f"语气：{plan.tone}；关注：{plan.attention or '当前话题'}；置信度：{plan.confidence:.2f}"
                          if planner_config.enabled else "可选思考阶段未启用，直接生成回复")
            except Exception as error:
                self.memory_warning.emit(f"可选思考阶段跳过：{type(error).__name__}")
                log_event(self.source, "思考", f"阶段跳过：{type(error).__name__}")
            emotion_prompt = self.emotion_candidate.to_prompt() if self.emotion_candidate else ""
            if plan.prompt():
                emotion_prompt += "\n" + plan.prompt()
            skill_hint = fangyi_guidance(self.settings.system_prompt, self.message,
                                         enabled=planner_config.fangyi_scene_skill)
            if skill_hint:
                emotion_prompt += "\n" + skill_hint
                log_event(self.source, "角色场景", skill_hint)
            reply = invoke_stage(
                "reply", self.settings, self.key, self.history, self.message,
                memories=memories, emotion_context=emotion_prompt,
                context_notes=context_notes, trace_id=self.turn_id)
            self.replied.emit(reply)
        except Exception as error:
            log_event(self.source, "失败", f"turn:{self.turn_id} · 对话失败：{type(error).__name__}: {error}")
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
            log_event("记忆", "写入", "桌面对话长期记忆更新完成")
        except Exception as error:
            log_event("记忆", "失败", f"桌面长期记忆写入失败：{type(error).__name__}")
            self.failed.emit(f"长期记忆保存失败：{error}")


class OwnerMemoryWriteWorker(QThread):
    failed = Signal(str)

    def __init__(self, link, user_text: str, assistant_text: str, settings: LlmSettings,
                 key: str, parent: QWidget):
        super().__init__(parent)
        self.link, self.user_text, self.assistant_text = link, user_text, assistant_text
        self.settings, self.key = settings, key

    def run(self) -> None:
        try:
            from owner_memory import load_link, record_voice_turn
            if load_link() != self.link:
                return
            count = record_voice_turn(self.link, self.user_text, self.assistant_text,
                                      self.settings, self.key)
            log_event("记忆", "写入", f"语音 · 共享个人记忆待提炼轮次：{count}")
        except Exception as error:
            log_event("记忆", "失败", f"语音共享记忆写入失败：{type(error).__name__}")
            self.failed.emit(f"共享个人记忆保存失败：{error}")
