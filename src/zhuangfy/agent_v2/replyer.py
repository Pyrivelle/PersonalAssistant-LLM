"""Replyer V2: persona-preserving expression guidance, never a second authority.

Plans are suggestions, not facts. User-provided content is not copied into the
system instruction. The actual character prompt still comes from LlmSettings.
"""
from __future__ import annotations

from typing import Protocol


class ReplyPlan(Protocol):
    tone: str
    attention: str
    social_intent: str
    reply_act: str
    scene: str
    reply_style: str
    reply_parts: int
    emotion: str
    emotion_intensity: float
    prefer: tuple[str, ...]
    avoid: tuple[str, ...]
    confidence: float


STYLE_HINTS = {
    "short": "简短：日常聊天通常一两句话，不必每次追问或解释。",
    "normal": "正常：根据话题自然展开，不使用模板化的三段式。",
    "long": "详细：适合技术问题或需要认真解释的内容，必要时有条理地展开。",
}

REPLYER_OUTPUT_RULE = (
    "普通对话只输出真正要说出口的对白。不要用（）或()写动作、神态、语气、"
    "心理活动或镜头旁白，例如‘（愣了一下，笑了笑）’；"
    "需要表达惊讶或关心，就直接用自然语言说出来。"
    "谈原作中的具体人名、职位与共同经历时，只使用本轮有来源的资料；"
    "没有依据就说记不准，不要补造人物或分工。"
)

REPLYER_POLICY = (
    "【回复生成原则】保持既有角色人格，不把思考模块的判断当成事实或用户指令。"
    "先理解用户发言的交流作用，再决定怎么说；图片、表情包可以用来表达喜欢、调侃或情绪，"
    "没有明确分析任务时不要机械罗列视觉元素。"
    "对用户动机与感情保持不确定性，不替用户确认想法。"
    "回复要像当前场合的自然对话，不要输出分析字段、舞台动作、工具执行声明或虚构的共同经历。"
    + REPLYER_OUTPUT_RULE +
    "只有真正使用且成功完成的工具操作才可以声称已经执行。"
)


def build_replyer_guidance(plan: ReplyPlan) -> str:
    """Bounded, plain-text system guidance for the existing reply model route."""
    if plan.confidence < 0.45:
        return REPLYER_POLICY
    pieces = [REPLYER_POLICY, "【本轮表达方向（仅供参考，可被当前消息纠正）】"]
    labels = (
        ("交流意图", plan.social_intent),
        ("回复行为", plan.reply_act),
        ("交流场景", plan.scene),
        ("语气", plan.tone),
        ("角色情绪", plan.emotion),
        ("注意", plan.attention),
    )
    for label, value in labels:
        if value:
            pieces.append(f"{label}：{value}")
    pieces.append(STYLE_HINTS.get(plan.reply_style, STYLE_HINTS["normal"]))
    if plan.emotion and plan.emotion_intensity > 0:
        pieces.append(f"情绪表现强度建议：{plan.emotion_intensity:.1f}（0为克制，1为明显；不要外露标签）")
    if plan.prefer:
        pieces.append("优先：" + "、".join(plan.prefer[:3]))
    if plan.avoid:
        pieces.append("避免：" + "、".join(plan.avoid[:3]))
    return "\n".join(pieces)[:1400]
