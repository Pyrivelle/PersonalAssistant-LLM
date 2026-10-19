import unittest

from wake import ConversationGate, DEFAULT_ALIASES, DEFAULT_NAME, parse_aliases


class ConversationGateTests(unittest.TestCase):
    def setUp(self):
        self.gate = ConversationGate(DEFAULT_NAME, parse_aliases(DEFAULT_ALIASES), 30)

    def test_wake_corrects_alias_and_accepts_followup(self):
        self.assertEqual(self.gate.process("今天天气怎么样", now=100).event, "ignored")
        self.assertEqual(self.gate.process("庄方怡，今天天气怎么样？", now=101).text, "庄方宜，今天天气怎么样？")
        self.assertEqual(self.gate.process("接着说", now=125).event, "accepted")
        self.assertEqual(self.gate.remaining(now=154), 1)
        self.assertEqual(self.gate.process("再说一句", now=155).event, "ignored")

    def test_name_alone_wakes_and_expires(self):
        self.assertEqual(self.gate.process("庄天师！", now=10).event, "woke")
        self.assertEqual(self.gate.process("帮我记一下", now=39).event, "accepted")
        self.assertEqual(self.gate.remaining(now=69), 0)
        self.assertEqual(self.gate.process("帮我记一下", now=70).event, "ignored")

    def test_does_not_wake_for_substrings_or_place_name(self):
        for phrase in ("我认识小庄", "小庄园在这里", "今天很安静"):
            self.assertEqual(self.gate.process(phrase, now=10).event, "ignored")

    def test_alias_settings_are_explicit(self):
        self.assertEqual(parse_aliases("庄天师，小庄;庄天师\n庄方怡"), ("庄天师", "小庄", "庄方怡"))
        self.assertEqual(self.gate.process("小张", now=10).event, "ignored")

    def test_each_name_wakes_and_normalizes(self):
        for name in ("庄方宜", "庄天师", "小庄", "庄芳怡", "庄方一", "庄天使"):
            with self.subTest(name=name):
                self.gate.reset()
                result = self.gate.process(name + "，你好", now=10)
                self.assertEqual(result.event, "woke")
                self.assertEqual(result.text, "庄方宜，你好")


if __name__ == "__main__":
    unittest.main()
