"""Fangyi's fictional history must never become a real user's memory."""
import unittest

from fangyi_skill import guidance, is_fangyi_persona
from persona import DEFAULT_PROMPT


class FangyiSkillTests(unittest.TestCase):
    def test_default_pack_detected_without_rewriting_custom_personas(self):
        self.assertTrue(is_fangyi_persona(DEFAULT_PROMPT))
        self.assertFalse(is_fangyi_persona("你是一个天气播报助手。"))
        self.assertEqual(guidance("你是一个天气播报助手。", "我好累"), "")

    def test_technical_situation_routes_to_peer_research(self):
        rule = guidance(DEFAULT_PROMPT, "Qt C++ 编译一直报错")
        self.assertIn("共同研究", rule)
        self.assertIn("技术准确性", rule)
        self.assertNotIn("Qt C++ 编译一直报错", rule)

    def test_compassion_routes_to_respecting_distress(self):
        rule = guidance(DEFAULT_PROMPT, "今天很累，感觉自己没用")
        self.assertIn("允许脆弱", rule)
        self.assertIn("不催促", rule)

    def test_fiction_and_user_history_are_explicitly_separate(self):
        rule = guidance(DEFAULT_PROMPT, "我喜欢庄方宜的剧情")
        self.assertIn("设定与来源", rule)
        self.assertIn("不能替代", rule) if "不能替代" in rule else self.assertIn("不得制造原作证据", rule)
        self.assertIn("现实用户记忆", rule)

    def test_disabled_returns_nothing(self):
        self.assertEqual(guidance(DEFAULT_PROMPT, "写代码", enabled=False), "")


if __name__ == "__main__":
    unittest.main()
