"""Example trusted ZhuangFY skill. Enabled explicitly in skills_settings.json."""
from __future__ import annotations

from zhuangfy.agent_v2.runtime import ToolBinding


async def _length(args: dict) -> str:
    text = args["text"]
    return f"字符总数：{len(text)}；非空白字符数：{sum(not c.isspace() for c in text)}"


def setup(context):
    if context.skill_id != "text_stats":
        raise ValueError("unexpected skill identity")
    return [
        ToolBinding(
            name="text_stats.length", handler=_length,
            scopes=frozenset({"private", "group"}), mutating=False,
            description="统计明确给出的文字中的字符数量与非空白字符数。不得读取聊天之外的数据。",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 2000}},
                "required": ["text"], "additionalProperties": False,
            },
        )
    ]
