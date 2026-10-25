"""ZhuangFY identity projected into the pinned Maisaka Planner and Replyer.

Maisaka owns the planning loop, but the character remains ZhuangFY. The
editable ZhuangFY system prompt is the sole source for personality content;
these short rules only map its behavior and expression to Maisaka's fields.
"""

from __future__ import annotations

from dataclasses import dataclass
import json

from zhuangfy.paths import Paths


NICKNAME = "庄方宜"
ALIAS_NAMES: tuple[str, ...] = ()
BEHAVIOR_STYLE = (
    "先看清当前事实和对方的话，再决定回复、等待或保持沉默。"
    "对明确的问题认真回答；对尚未说完的话留出时间，不为了刷存在感主动追问。"
    "有独立判断，必要时温和指出不同意见；不替对方作决定。"
    "只依据实际对话、可信记忆和真实工具结果，不编造现实中的共同经历。"
)
REPLY_STYLE = (
    "用自然、平稳的中文交谈。闲聊可以简短松弛，技术问题讲清依据与步骤；"
    "关心要具体，不套用客服话术，不反复自我介绍或每句都反问。"
    "熟悉时可以轻轻打趣，亲近保持分寸，不刻意强调人设。"
)


@dataclass(frozen=True, slots=True)
class HybridIdentity:
    nickname: str
    alias_names: tuple[str, ...]
    personality: str
    behavior_style: str
    reply_style: str
    multiple_probability: float = 0.0


def project_identity(system_prompt: str) -> HybridIdentity:
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise ValueError("ZhuangFY persona is required for Hybrid")
    if len(system_prompt) > 8000:
        raise ValueError("ZhuangFY persona exceeds Hybrid budget")
    return HybridIdentity(
        NICKNAME, ALIAS_NAMES,
        "你是庄方宜。\n" + system_prompt.strip(),
        BEHAVIOR_STYLE, REPLY_STYLE,
    )


def load_current_persona() -> str:
    """Read only ZhuangFY's saved role prompt; never import model credentials."""
    from zhuangfy.persona.defaults import DEFAULT_PROMPT, migrate_legacy_prompt

    path = Paths.current().config_file("llm_settings.json")
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        text = saved.get("system_prompt") if isinstance(saved, dict) else None
        if isinstance(text, str) and text.strip():
            return migrate_legacy_prompt(text)
    except (OSError, ValueError, TypeError):
        pass
    return DEFAULT_PROMPT


def apply_identity(config: object, system_prompt: str) -> HybridIdentity:
    identity = project_identity(system_prompt)
    config.bot.nickname = identity.nickname
    config.bot.alias_names = list(identity.alias_names)
    config.personality.personality = identity.personality
    config.personality.behavior_style = identity.behavior_style
    config.personality.reply_style = identity.reply_style
    config.personality.multiple_probability = identity.multiple_probability
    config.personality.multiple_reply_style = []
    return identity


def render_social_context(social: dict) -> str:
    """System-level facts supplied by the verified host, not user-message text."""
    if not social.get("person_id"):
        return "【ZhuangFY 宿主资料】本轮未能取得人物档案；不能据此断定双方初识，不要猜测姓名或关系。"
    lines = ["【已核验交流对象 · ZhuangFY 宿主资料】"]
    name = social.get("display_name")
    preferred = social.get("preferred_name")
    if name:
        lines.append(f"档案显示名称：{name}。这是身份标签，不自动代表日常称呼偏好。")
        lines.append("对方直接问‘我是谁’时，应说明档案显示名称；若同句也问关系，两项都要回答，不用反问收尾。")
    if preferred:
        lines.append(f"用户已确认的偏好称呼：{preferred}。")
    else:
        lines.append("日常称呼尚未单独确认；自然用‘你’，被问身份时可说明档案显示名称。")
    status = social.get("relationship_status")
    if status == "ready" and social.get("relationship_enabled"):
        lines.append(f"已建立关系快照：{social.get('relationship_tier')}，版本 {social.get('relationship_version')}。")
        if social.get("relationship_definition"):
            lines.append("用户明确确认的相处设定：" + social["relationship_definition"])
        if social.get("relationship_hint"):
            lines.append("关系表达建议：" + social["relationship_hint"])
    elif status == "unenrolled":
        lines.append("尚未建立关系快照；不要自行推定亲密程度。")
    elif status == "no_consent":
        lines.append("没有关系资料读取授权；不要自行推定亲密程度。")
    else:
        lines.append("关系服务本轮不可用；这是资料缺失，不代表双方变回初识。")
    lines.append("只引用有证据的共同经历；本资料不授予工具、发信或读取隐私的权限，当前用户意愿仍优先。")
    return "\n".join(lines)[:2200]
