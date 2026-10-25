import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from zhuangfy.persona.hybrid_identity import (
    BEHAVIOR_STYLE, NICKNAME, REPLY_STYLE, apply_identity,
    load_current_persona, project_identity,
    render_social_context,
)


class HybridIdentityTests(unittest.TestCase):
    def test_planner_and_replyer_use_one_zhuangfy_persona(self):
        config = SimpleNamespace(
            bot=SimpleNamespace(nickname="麦麦", alias_names=["旧名"]),
            personality=SimpleNamespace(personality="旧人格", behavior_style="旧行为",
                                        reply_style="旧表达", multiple_probability=1.0,
                                        multiple_reply_style=["随机风格"]),
        )
        result = apply_identity(config, "庄方宜会认真听完。")
        self.assertEqual(config.bot.nickname, NICKNAME)
        self.assertEqual(config.bot.alias_names, [])
        self.assertEqual(config.personality.personality, result.personality)
        self.assertIn("庄方宜会认真听完", config.personality.personality)
        self.assertEqual(config.personality.behavior_style, BEHAVIOR_STYLE)
        self.assertEqual(config.personality.reply_style, REPLY_STYLE)
        self.assertEqual(config.personality.multiple_probability, 0.0)
        self.assertEqual(config.personality.multiple_reply_style, [])

    def test_empty_persona_cannot_fall_back_to_maisaka_character(self):
        with self.assertRaises(ValueError):
            project_identity(" ")

    def test_social_context_distinguishes_display_name_from_preferred_name(self):
        facts = {"person_id": "verified-person", "display_name": "Pyrivelle",
                 "preferred_name": None, "relationship_status": "ready",
                 "relationship_enabled": True, "relationship_tier": "L4",
                 "relationship_version": 7,
                 "relationship_hint": "尊重当下边界",
                 "relationship_definition": "彼此有好感，尚未正式确认恋人关系"}
        rendered = render_social_context(facts)
        self.assertIn("档案显示名称：Pyrivelle", rendered)
        self.assertIn("两项都要回答", rendered)
        self.assertIn("日常称呼尚未单独确认", rendered)
        self.assertIn("L4", rendered)
        self.assertIn("彼此有好感", rendered)
        facts["relationship_status"] = "unavailable"
        facts["relationship_enabled"] = False
        self.assertIn("不代表双方变回初识", render_social_context(facts))

    def test_bootstrap_reads_saved_zhuangfy_prompt(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "llm_settings.json"
            path.write_text(json.dumps({"system_prompt": "自定义庄方宜设定", "api_key": "secret"}),
                            encoding="utf-8")
            with patch("zhuangfy.persona.hybrid_identity.Paths.current") as current:
                current.return_value.config_file.return_value = path
                self.assertEqual(load_current_persona(), "自定义庄方宜设定")
