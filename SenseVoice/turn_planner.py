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
    agent_v2_enabled: bool = False
    agent_v2_allow_silence_private: bool = False
    agent_v2_allow_silence_group: bool = False
    agent_v2_wait_max_seconds: int = 8
    agent_v2_tools_enabled: bool = False
    agent_v2_max_rounds: int = 4
    agent_v2_max_tools: int = 2
    character_core_v2_enabled: bool = False  # opt-in while V1 remains available

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
        v2_enabled = raw.get("agent_v2_enabled", False)
        silent_private = raw.get("agent_v2_allow_silence_private", False)
        silent_group = raw.get("agent_v2_allow_silence_group", False)
        wait_max = raw.get("agent_v2_wait_max_seconds", 8)
        tools_enabled = raw.get("agent_v2_tools_enabled", False)
        max_rounds = raw.get("agent_v2_max_rounds", 4)
        max_tools = raw.get("agent_v2_max_tools", 2)
        core_v2 = raw.get("character_core_v2_enabled", False)
        if type(core_v2) is not bool:
            raise ValueError("CCV2 开关必须是布尔值")
        if any(type(item) is not bool for item in (v2_enabled, silent_private, silent_group, tools_enabled)):
            raise ValueError("Agent V2 开关必须是布尔值")
        if type(wait_max) is not int or not 1 <= wait_max <= 12:
            raise ValueError("Agent V2 等待上限必须是 1–12 秒")
        if type(max_rounds) is not int or not 1 <= max_rounds <= 8:
            raise ValueError("agent_v2_max_rounds 必须在 1–8")
        if type(max_tools) is not int or not 0 <= max_tools <= 6:
            raise ValueError("agent_v2_max_tools 必须在 0–6")
        return cls(raw.get("enabled", False), model.strip(), frozenset(ids), desktop,
                   frozenset(memory_ids), skill, v2_enabled, silent_private,
                   silent_group, wait_max, tools_enabled, max_rounds, max_tools,
                   core_v2)


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


def _valid_planner_output(text: str) -> bool:
    """Reject malformed responses so the next model may be tried."""
    source = text.strip()
    if source.startswith(chr(96) * 3):
        source = source.split("\n", 1)[-1].rsplit(chr(96) * 3, 1)[0].strip()
    try:
        data = json.loads(source)
        return (isinstance(data, dict) and type(data.get("should_reply")) is bool
                and data.get("tone") in ALLOWED_TONES
                and isinstance(data.get("attention"), str)
                and type(data.get("confidence")) in (int, float)
                and 0 <= data["confidence"] <= 1
                and data.get("delivery", "text") in {"text", "voice"})
    except (ValueError, TypeError, KeyError):
        return False


def plan_turn(settings, api_key: str, history: list[dict[str, str]],
              user_text: str, config: PlannerSettings, *, direct_message: bool = True,
              allow_voice: bool = False) -> TurnPlan:
    """Planner may fail or return invalid JSON; callers safely fall back."""
    if not config.enabled:
        return TurnPlan()
    from model_routes import invoke_stage
    prompt = PLANNER_PROMPT if allow_voice else PLANNER_PROMPT + "\n本轮只能回复文字，delivery 必须为 text。"
    reply = invoke_stage(
        "planner", settings, api_key, history[-8:], user_text[:1500],
        system_prompt=prompt, legacy_model=config.model,
        response_validator=_valid_planner_output)
    result = parse_plan(reply, direct_message=direct_message)
    if not allow_voice and result.delivery == "voice":
        from dataclasses import replace
        result = replace(result, delivery="text")
    return result
