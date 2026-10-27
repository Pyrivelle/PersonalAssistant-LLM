"""The Planner must request an allowlisted document before it is read."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zhuangfy.agent_v2.character_skill_tool import (
    CharacterSkillSession, TOOL_NAME, with_character_skill,
)
from zhuangfy.agent_v2.planner import PlanDecision
from zhuangfy.agent_v2.runtime import AgentCycleEngine, AuthorizedToolRegistry
from zhuangfy.context_protocol import build_current_user_content
from zhuangfy.persona.skill_reference import read_skill_document


class CharacterSkillReferenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "SKILL.md").write_text(
            "---\nname: zhuang-fangyi-agent\n---\n# 角色\n", encoding="utf-8")
        base = self.root / "references" / "knowledge"
        base.mkdir(parents=True)
        (base / "wuling.md").write_text(
            "# 武陵\n## 基本定位\n武陵是一座城市。\n## 生活感\n这里有日常生活。",
            encoding="utf-8")
        (base / "relationships.md").write_text(
            "# 人物关系\n## 弭弗\n- 追击与护卫。\n## 葛一朴\n- 研究同僚。\n",
            encoding="utf-8")

    def test_exact_document_read_preserves_whole_file(self):
        result = read_skill_document("relationships", root=self.root)
        self.assertEqual(result.source, "references/knowledge/relationships.md")
        self.assertIn("## 葛一朴\n- 研究同僚。", result.context)
        with self.assertRaises(ValueError):
            read_skill_document("../../secrets", root=self.root)

    async def test_json_tool_request_then_replan_and_reply_evidence(self):
        session = CharacterSkillSession(root=self.root)
        registry = with_character_skill(
            AuthorizedToolRegistry(), system_prompt="你是庄方宜。",
            enabled=True, session=session)
        self.assertEqual(session.context(), "")
        catalog = registry.prompt_catalog(scope="private")
        self.assertIn("character_skill.read", catalog)
        self.assertIn("relationships", catalog)
        seen = []
        async def planner(receipts):
            seen.append((len(receipts), session.context()))
            if not receipts:
                return PlanDecision(action="tool", confidence=0.9,
                                    tool_name=TOOL_NAME,
                                    tool_arguments={"document": "relationships"})
            return PlanDecision(action="reply", confidence=0.9)
        result = await AgentCycleEngine(max_rounds=3, max_tools=1).run(
            planner, scope="private", registry=registry, allow_tools=True)
        self.assertEqual(result.action, "reply")
        self.assertEqual([receipt.success for receipt in result.receipts], [True])
        self.assertEqual(seen[0], (0, ""))
        self.assertIn("## 葛一朴", seen[1][1])
        self.assertIn("## 葛一朴", build_current_user_content(
            "你和谁共事？", skill_context=session.context()))

    async def test_disabled_or_wrong_persona_has_no_tool(self):
        for prompt, enabled in (("你是小明。", True), ("你是庄方宜。", False)):
            registry = with_character_skill(
                AuthorizedToolRegistry(), system_prompt=prompt,
                enabled=enabled, session=CharacterSkillSession(root=self.root))
            self.assertFalse(registry.available(TOOL_NAME, scope="private"))

    async def test_model_cannot_choose_unlisted_file(self):
        session = CharacterSkillSession(root=self.root)
        registry = with_character_skill(
            AuthorizedToolRegistry(), system_prompt="你是庄方宜。",
            enabled=True, session=session)
        receipt = await registry.invoke(TOOL_NAME, {"document": "../../secrets"},
                                        scope="private")
        self.assertFalse(receipt.success)
        self.assertEqual(session.context(), "")
