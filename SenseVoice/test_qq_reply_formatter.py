"""QQ reply presentation tests: no stage narration, no accidental facts loss."""
import unittest

from qq_reply_formatter import clean_reply, format_qq_reply, split_private_reply


class QqReplyFormatterTests(unittest.TestCase):
    def test_real_stage_direction_example(self):
        raw = "（语气平稳，带点询问）  \n你自己在写哪些方向的东西？如果知道类型，或许能帮你想几条找人的路。"
        self.assertEqual(clean_reply(raw),
                         "你自己在写哪些方向的东西？如果知道类型，或许能帮你想几条找人的路。")

    def test_removes_several_leading_meta_cues_without_rewriting_reply(self):
        self.assertEqual(clean_reply(
            "庄方宜： *（轻笑）* （语气温和）\n嗯，终于解决了。"),
            "嗯，终于解决了。")

    def test_parentheses_inside_reply_and_facts_survive(self):
        raw = "（Qt 6.5 环境）这里先用 QNetworkRequest。\n另外，朋友说（语气平稳）很别扭。"
        self.assertEqual(clean_reply(raw),
                         "（Qt 6.5 环境）这里先用 QNetworkRequest。另外，朋友说（语气平稳）很别扭。")

    def test_markdown_spaces_and_two_natural_bubbles(self):
        original = (
            "不一定只是交际圈的问题，也可能和现阶段的时间有关。  \n"
            "白天上班，晚上还要备考，能匀给开发的精力本来就不多。"
            "这种节奏下，遇到刚好方向相同、步调也合拍的人确实需要一些运气。\n\n"
            "倒是有个思路：如果你愿意把自己写的小工具或库放在 GitHub 上，"
            "让项目本身替你“说话”，有时候同好会顺着作品找过来。"
            "你在代码里留下的取舍和思考，要比一次闲聊更能吸引对路的人。"
        )
        logical, parts = format_qq_reply(original, private=True)
        self.assertEqual(len(parts), 2)
        self.assertNotIn("  \n", logical)
        self.assertIn("GitHub", parts[-1])
        self.assertEqual(logical, "\n\n".join(parts))

    def test_short_reply_stays_one_and_group_always_one(self):
        self.assertEqual(format_qq_reply("嗯，回来啦。", private=True)[1],
                         ["嗯，回来啦。"])
        raw = "第一段。" * 30 + "\n\n" + "第二段。" * 30
        self.assertEqual(len(format_qq_reply(raw, private=False)[1]), 1)

    def test_private_split_can_be_disabled_by_setting(self):
        raw = ("事情需要慢慢推进，也可以先从一件小事开始。" * 4
               + "\n\n" + "这是第二个自然段，你不必一次完成所有目标。" * 4)
        logical, parts = format_qq_reply(raw, private=True, max_parts=1)
        self.assertEqual(parts, [logical])
        self.assertEqual(len(format_qq_reply(raw, private=True, max_parts=3)[1]), 2)

    def test_many_paragraphs_are_bounded(self):
        text = "\n\n".join(f"第{i}段：" + "这件事可以慢慢来。" * 12 for i in range(8))
        cleaned, parts = format_qq_reply(text, private=True)
        self.assertLessEqual(len(parts), 3)
        self.assertTrue(all(part for part in parts))
        self.assertLessEqual(len(cleaned), 1200)

    def test_code_fence_remains_intact(self):
        code = chr(96) * 3 + "cpp\nint main() {\n    return 0;\n}\n" + chr(96) * 3
        original = "我建议这样写：\n\n" + code + "\n\n这个方案可用。"
        logical, parts = format_qq_reply(original, private=True)
        self.assertIn("    return 0;", logical)
        self.assertTrue(any(code in chunk for chunk in parts))
        self.assertIn("这个方案可用。", logical)

    def test_still_keeps_non_stage_opening_quote(self):
        self.assertEqual(clean_reply("（这是一个比喻）我觉得很贴切。"),
                         "（这是一个比喻）我觉得很贴切。")


if __name__ == "__main__":
    unittest.main()
