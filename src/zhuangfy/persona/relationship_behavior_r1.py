"""R1-A relationship-aware behavior cues; no relationship inference or write access.

This layer guides *decisions and expression* using only an explicitly supplied
relationship tier. It has no authority to read memories, change permissions,
act on tools, write relationship scores, or send messages.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from .character_core_v2 import is_zhuangfangyi_prompt

Tier = Literal["unassigned", "L0", "L1", "L2", "L3"]
Channel = Literal["chat", "qq_private", "qq_group", "initiative"]
STYLE = Literal["conversation", "technical_concise", "technical_detail", "creative"]

_TECH = re.compile(
    r"\b(?:qt|osg|c\+\+|python|dll|api|bug|grpc|dds|llm|shader|fps|gpu)\b|"
    r"代码|接口|编译|技术|架构|崩溃|性能|模型调用|算法|插件|测试|部署|怎么排查|如何实现",
    re.I)
_DETAIL = re.compile(
    r"详细(解释|分析|讲解|说明|对比)|一步一步|逐步|完整(教程|方案|实现)|展开(讲|分析)|"
    r"列出(所有|全部)|深入(分析|讨论)|给出(具体|完整)代码|示例代码", re.I)
_CREATIVE = re.compile(
    r"演绎(一段|一下|场景|对白)|角色扮演|写(一段|个)(剧情|小说|故事|剧本)|"
    r"用(旁白|剧本|小说)形式|加入(动作|心理)描写|描写(动作|神态)", re.I)
_NEGATE_CREATIVE = re.compile(
    r"不要.{0,12}(演绎|剧本|旁白|动作描写|角色扮演)|"
    r"不想.{0,12}(演绎|剧本|旁白)|"
    r"别.{0,12}(演绎|剧本|旁白)", re.I)

_CORE = (
    "【R1-A · 事实与表达行为约定（不是权限系统）】"
    "仅把本轮可确认的用户原话、可信上下文/记忆及真实工具回执作为现实事实证据。"
    "计划、推测、角色想象、虚拟故事和行动请求都不是实际发生的事实；"
    "没有回执就不说‘已经发送、查过、设置、记得某次共同经历’。"
    "不知道时自然说不知道，或明确以假设表达，不补具体日期、地点、往事或结果。"
    "对方在分享生活时首先接续生活，而不是强行引向待办与技术工作。"
    "禁止把 Planner 的 tool 意图或建议当作已完成动作；"
    "是否调用 Skill、是否发送、是否沉默由原有宿主与工具回执决定，不在这里增减权限。"
    "并非遇到隐私或工具话题就刻板拒绝：先分清讨论/设想与实际执行。"
)
_TIER = {
    "unassigned": "没有已验证的关系等级：保持自然熟悉但不预设亲密、不凭空提共同旧事。",
    "L0": "初识：友好自在，不突然宣称亲密或旧日共同经历。",
    "L1": "熟悉：可以轻松与适度关切，不预设私密约定。",
    "L2": "信任：可更直接关心、表达观点；记忆引用仍需实际证据。",
    "L3": "亲近：在对方明确欢迎的前提下可温柔、带一点私心与松弛，不自称独占、不可虚构共同经历。",
}
_CHANNEL = {
    "chat": "当前为单人实时聊天，除用户另有要求外，回答应像正在交谈。",
    "qq_private": "当前为 QQ 私聊，少用结构性套话；每次聊天不必以追问收尾。",
    "qq_group": "当前为公共群聊：表达公开场合的自然距离，不泄漏任何未提供给当前场合的私人事实。",
    "initiative": "当前为已由宿主批准的主动交流候选：本层只决定表达，不宣布已投递，也不扩大联系权限。",
}


@dataclass(frozen=True, slots=True)
class R1Behavior:
    mode: STYLE
    tier: Tier
    guidance: str


def behavior_r1(system_prompt: str, user_text: str, *,
                channel: Channel = "chat", tier: Tier = "unassigned",
                enabled: bool = True) -> R1Behavior:
    """Build host-authored guidance, never echo arbitrary inbound instructions."""
    if not enabled or not is_zhuangfangyi_prompt(system_prompt):
        return R1Behavior("conversation", "unassigned", "")
    if channel not in _CHANNEL or tier not in _TIER:
        raise ValueError("Invalid R1 channel/tier")
    utterance = user_text[:1600] if isinstance(user_text, str) else ""
    if _CREATIVE.search(utterance) and not _NEGATE_CREATIVE.search(utterance):
        mode: STYLE = "creative"
        expression = (
            "对方明确请求创作或演绎，此轮允许文学性旁白和虚构情节；"
            "标清是在创作/设想，不能把虚构的场面当作现实已经发生的事。"
        )
    elif _TECH.search(utterance):
        if _DETAIL.search(utterance):
            mode = "technical_detail"
            expression = (
                "对方明确要求深入技术说明：先给核心判断，再按问题复杂度有序展开原理、步骤及验证办法。"
                "不为凑篇幅重复套话；不是所有技术问题都应简短。"
            )
        else:
            mode = "technical_concise"
            expression = (
                "技术话题但没有明确要求长教程：先给直接结论，再给关键依据或至多几个优先检查点；"
                "不要默认展开完整教程或大量列表。"
                "有必要澄清一个缺失的关键条件才发问；不要每段技术答复末尾惯性加‘你觉得呢’。"
            )
    else:
        mode = "conversation"
        expression = (
            "普通聊天先回应具体内容，通常一到三句即可，必要时自然延展。"
            "没有新信息需要确认就不必用反问句结尾，别把每次回应变成开放式客服追问。"
        )
    if mode != "creative":
        expression += (
            "这是实际沟通，不写括号动作、镜头、内心独白、剧本提示或模型思考标签；"
            "不要为了显得生动而声称自己看到窗外、敲键盘、刚完成实验或其他并未发生的行为。"
        )
    # Actual relation tiers are supplied only by trusted host policy. No
    # user-text keyword can raise a tier or grant access.
    guidance = "\n".join((
        _CORE,
        "【关系语气参考】" + _TIER[tier],
        "【渠道与真实行动】" + _CHANNEL[channel],
        "【本轮表达策略】" + expression,
    ))
    return R1Behavior(mode, tier, guidance[:1550])


# Conservative display-only cleanup. Never rewrite actual claims or fabricate
# tool receipts. Original model text is retained in E1 captures for regression.
_LEADING_THINK_END = re.compile(r"^\s*</think>\s*", re.I)
_LEADING_STAGE = re.compile(
    r"^\s*[（(](?:看着|看向|望向|轻声|低声|柔声|微笑|轻笑|叹息|"
    r"靠在|坐在|起身|抬头|低头|她(?:看|想|沉默|遵守|点头)|"
    r"他(?:看|想|沉默|点头))[^（）()\n]{0,110}[）)]\s*"
)


def tidy_r1_reply(system_prompt: str, user_text: str, response: str, *,
                  channel: Channel = "chat", enabled: bool = True) -> str:
    """Remove only unambiguous *leading* theatrical cues in real chat.

    Keep parenthetical technical data and all creative-mode narrative intact.
    A result that contains only stage cues becomes empty; caller may suppress
    sending rather than claim it was a real action.
    """
    if not isinstance(response, str):
        raise ValueError("R1 reply must be text")
    if not enabled:
        return response
    cue = behavior_r1(system_prompt, user_text, channel=channel)
    if not cue.guidance or cue.mode == "creative":
        return response
    cleaned = response
    for _ in range(4):
        before = cleaned
        cleaned = _LEADING_THINK_END.sub("", cleaned, count=1)
        cleaned = _LEADING_STAGE.sub("", cleaned, count=1)
        if cleaned == before:
            break
    return cleaned.strip() if cleaned != response else response
