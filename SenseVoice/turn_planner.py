"""Optional short planning call before the normal persona reply.

Only emits a compact decision and response guidance, never private chain of
thought. Inbound chat is always answered; silence is reserved for future
unsolicited events and requires an explicit caller opt-in.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PLANNER_PROMPT = """你是虚拟陪伴者的对话决策模块，不进行角色扮演。
只输出一个 JSON 对象，字段为 should_reply(boolean)、tone(string)、attention(string)、confidence(number)、delivery(string)。
根据最近消息判断本轮表达风格以及应当关注的用户明确说过的事情。
只允许 tone 为：自然、认真、关切、轻松、欣喜、简洁。
attention 应是简短、谨慎、与当前对话相关的提醒，最长 60 字，不能编造经历或确定用户心理。
confidence 介于 0 和 1。should_reply 仅对非定向的系统主动事件有意义。
delivery 只能是 text 或 voice。只有用户明确想听语音，或短小的陪伴式回应更适合听觉表达时才选 voice；代码、链接、步骤和较长内容选 text。拿不准时选 text。
不要输出推理步骤、Markdown、额外字段或用户个人资料。"""
ALLOWED_TONES = frozenset({"自然", "认真", "关切", "轻松", "欣喜", "简洁"})


@dataclass(frozen=True)
class PlannerSettings:
    enabled: bool = False
    model: str = ""
    persist_private_ids: frozenset[int] = frozenset()
    persist_desktop_context: bool = False
    memory_private_ids: frozenset[int] = frozenset()
    fangyi_scene_skill: bool = True

    @classmethod
    def load(cls, path: Path) -> "PlannerSettings":
        if not path.is_file():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("enabled", False), bool):
            raise ValueError("思考模型设置无效")
        model = raw.get("model", "")
        if not isinstance(model, str) or len(model) > 128:
            raise ValueError("思考模型 ID 无效")
        ids = raw.get("persist_private_ids", [])
        if not isinstance(ids, list) or any(type(item) is not int or item <= 0 for item in ids):
            raise ValueError("持久化私聊账号必须是正整数数组")
        desktop = raw.get("persist_desktop_context", False)
        if type(desktop) is not bool:
            raise ValueError("桌面上下文持久化开关必须是布尔值")
        memory_ids = raw.get("memory_private_ids", [])
        if (not isinstance(memory_ids, list) or
                any(type(item) is not int or item <= 0 for item in memory_ids)):
            raise ValueError("成员长期记忆账号必须是正整数数组")
        skill = raw.get("fangyi_scene_skill", True)
        if type(skill) is not bool:
            raise ValueError("庄方宜场景 Skill 开关必须是布尔值")
        return cls(raw.get("enabled", False), model.strip(), frozenset(ids), desktop,
                   frozenset(memory_ids), skill)


@dataclass(frozen=True)
class TurnPlan:
    should_reply: bool = True
    tone: str = "自然"
    attention: str = ""
    confidence: float = 0.0
    delivery: str = "text"

    def prompt(self) -> str:
        if self.confidence < 0.5:
            return ""
        return ("表达策略建议（属于不确定的模型判断，以本轮明确意图为准）："
                f"语气：{self.tone}；关注：{self.attention or '当前话题'}。"
                "不要输出这些标签，也不要编造用户的情绪和共同经历。")


def parse_plan(text: str, *, direct_message: bool = True) -> TurnPlan:
    content = text.strip()
    if content.startswith(chr(96) * 3):
        content = content.split("\n", 1)[-1].rsplit(chr(96) * 3, 1)[0].strip()
    try:
        data = json.loads(content)
        if not isinstance(data, dict):
            return TurnPlan()
        tone = data.get("tone", "自然")
        attention = data.get("attention", "")
        confidence = data.get("confidence", 0)
        reply = data.get("should_reply", True)
        delivery = data.get("delivery", "text")
        if tone not in ALLOWED_TONES or not isinstance(attention, str):
            return TurnPlan()
        if type(confidence) not in {float, int} or not 0 <= confidence <= 1:
            return TurnPlan()
        if type(reply) is not bool:
            return TurnPlan()
        if delivery not in {"text", "voice"}:
            delivery = "text"
        return TurnPlan(True if direct_message else reply, tone,
                        attention.replace("\n", " ")[:60], float(confidence), delivery)
    except (ValueError, TypeError):
        return TurnPlan()


def plan_turn(settings, api_key: str, history: list[dict[str, str]],
              user_text: str, config: PlannerSettings, *, direct_message: bool = True,
              allow_voice: bool = False) -> TurnPlan:
    """Second model call is strictly opt-in; exceptions are for callers to catch."""
    if not config.enabled:
        return TurnPlan()
    from dataclasses import replace
    from llm import chat
    prompt = PLANNER_PROMPT if allow_voice else PLANNER_PROMPT + "\n本轮只能回复文字，delivery 必须为 text。"
    planner_settings = replace(settings, model=config.model or settings.model,
                               system_prompt=prompt, memory_enabled=False)
    reply = chat(planner_settings, api_key, history[-8:], user_text[:1500], timeout=15, stage="思考")
    return parse_plan(reply, direct_message=direct_message)
