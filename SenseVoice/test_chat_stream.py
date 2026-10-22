"""The stream keeps interleaved chat turns distinct and hides background work."""

import unittest

from chat_stream import project_turns


def event(turn, channel, source, phase, detail):
    return {"turn_id": turn, "channel": channel, "source": source,
            "phase": phase, "detail": detail, "time": "2026-10-22T18:48:48+08:00"}


class ChatStreamTests(unittest.TestCase):
    def test_interleaved_channels_keep_their_own_reasoning_and_reply(self):
        turns = project_turns([
            event("qq1", "QQ", "QQ", "消息", "private:0:964692788 · 晚上好"),
            event("pc1", "文字", "文字", "消息", "turn:pc1 · 你在吗"),
            event("qq1", "QQ", "模型", "推理内容", "回复 · 1/1\n先回一句晚安"),
            event("pc1", "文字", "模型", "规划结果", '{"tone":"自然"}'),
            event("qq1", "QQ", "QQ", "回复", "private:0:964692788 · 晚上好。"),
            event("qq1", "QQ", "QQ", "回复", "private:0:964692788 · 今天还顺利吗？"),
            event("pc1", "文字", "文字", "回复", "我在。"),
        ])
        self.assertEqual([turn["message"] for turn in turns], ["晚上好", "你在吗"])
        self.assertIn("先回一句晚安", turns[0]["thinking"][0][1])
        self.assertEqual(turns[0]["reply"], "晚上好。\n今天还顺利吗？")
        self.assertEqual(turns[1]["reply"], "我在。")
        self.assertNotIn("先回一句晚安", str(turns[1]))

    def test_background_reasoning_is_not_a_chat_turn(self):
        turns = project_turns([
            event("qq1", "QQ", "QQ", "消息", "private:0:964692788 · 你好"),
            event("qq1", "QQ", "模型", "推理内容", "成员记忆反思 · 1/1\n私人资料"),
            event("", "", "模型", "推理内容", "回复 · 1/1\n无关联"),
        ])
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["thinking"], [])


if __name__ == "__main__":
    unittest.main()
