"""Turn-scoped Planner tool for reading the user's local character Skill."""
from __future__ import annotations

import asyncio
from pathlib import Path

from zhuangfy.persona.character_core_v2 import is_zhuangfangyi_prompt
from zhuangfy.persona.skill_reference import (
    DOCUMENTS, MAX_CONTEXT_CHARS, CharacterSkillRead,
    read_skill_document, skill_available, skill_context,
)
from .runtime import AuthorizedToolRegistry, ToolBinding


TOOL_NAME = "character_skill.read"


class CharacterSkillSession:
    """One conversation turn; model selects identifiers, host stores full results."""

    def __init__(self, *, root: Path | None = None) -> None:
        self.root = root
        self.reads: list[CharacterSkillRead] = []

    def context(self) -> str:
        return skill_context(tuple(self.reads))

    async def read(self, arguments: dict) -> str:
        document = arguments["document"]
        if any(item.document == document for item in self.reads):
            raise ValueError("Document already read in this turn")
        if len(self.reads) >= 2:
            raise ValueError("At most two Skill documents per turn")
        result = await asyncio.to_thread(read_skill_document, document, root=self.root)
        proposed = skill_context(tuple(self.reads + [result]))
        if not proposed or len(proposed) > MAX_CONTEXT_CHARS or result.context not in proposed:
            raise ValueError("Skill context budget exceeded")
        self.reads.append(result)
        return f"已读取 {result.source} 完整原文件（{len(result.content)} 字）；已附入下轮模型输入。"


def with_character_skill(base: AuthorizedToolRegistry, *, system_prompt: str,
                         enabled: bool, session: CharacterSkillSession) -> AuthorizedToolRegistry:
    """Expose exact document IDs only for an enabled Fangyi turn and clone."""
    if (not enabled or not is_zhuangfangyi_prompt(system_prompt)
            or not skill_available(root=session.root)):
        return base
    if TOOL_NAME in base._tools:
        raise ValueError("Character Skill tool already registered")
    registry = AuthorizedToolRegistry()
    if base._tools:
        registry.register_many(tuple(base._tools.values()))
    registry.register(ToolBinding(
        TOOL_NAME, session.read, frozenset({"private", "group"}),
        description=("只读本地庄方宜Skill原文件；设定或剧情问题且缺少依据时调用。"
                     "文档:wuling武陵,relationships共事者,trauma-duty旧事,"
                     "progress战后,zhuang-fangyi本人,silt-systems技术,hongshan-yan宏山。"),
        parameters={
            "type": "object",
            "properties": {"document": {"type": "string", "enum": list(DOCUMENTS)}},
            "required": ["document"], "additionalProperties": False,
        },
    ))
    return registry
