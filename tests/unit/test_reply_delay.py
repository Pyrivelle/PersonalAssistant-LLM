"""Explicit delayed replies are distinct from short continuation waits."""
import unittest

from zhuangfy.agent_v2.reply_delay import requested_reply_delay


class ReplyDelayTests(unittest.TestCase):
    def test_explicit_reply_time_is_bounded(self):
        self.assertEqual(requested_reply_delay("一分钟后再回复这条消息"), 60)
        self.assertEqual(requested_reply_delay("等1分钟再回复我"), 60)
        self.assertEqual(requested_reply_delay("30秒后回答我"), 30)
        self.assertEqual(requested_reply_delay("半分钟后回我"), 30)
        self.assertIsNone(requested_reply_delay("十分钟后的会议怎么样？"))
        self.assertIsNone(requested_reply_delay("20分钟后再回复"))
        self.assertIsNone(requested_reply_delay("我一分钟后再说"))
        self.assertIsNone(requested_reply_delay("一分钟后不要回复"))
        self.assertIsNone(requested_reply_delay("我一分钟后再回复你"))


if __name__ == "__main__":
    unittest.main()
