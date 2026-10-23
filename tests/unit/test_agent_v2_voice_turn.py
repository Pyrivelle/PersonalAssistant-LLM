"""Offline VoiceTurnManager regressions; no Qt, ASR model or microphone needed."""
import unittest

from zhuangfy.agent_v2.voice_turn import VoiceTurnManager, VoiceTurnConfig


class VoiceTurnTests(unittest.TestCase):
    def test_three_final_asr_segments_combine_into_single_turn(self):
        manager = VoiceTurnManager(VoiceTurnConfig(pause_seconds=1.5, max_wait_seconds=5))
        self.assertIsNone(manager.offer_final("小庄，今天", at=0, language="zh"))
        self.assertIsNone(manager.offer_final("我遇到一件", at=1, language="zh"))
        self.assertIsNone(manager.offer_final("很有意思的事", at=2, language="zh"))
        self.assertIsNone(manager.take_ready(now=3.4))
        turn = manager.take_ready(now=3.5)
        self.assertEqual(turn.parts, 3)
        self.assertEqual(turn.text, "小庄，今天 我遇到一件 很有意思的事")
        self.assertEqual(turn.reason, "quiet")
        self.assertFalse(manager.has_pending)
        self.assertIsNone(manager.take_ready(now=10))
        self.assertTrue(manager.accepts_output(turn))

    def test_speech_end_gap_and_maximum_wait(self):
        manager = VoiceTurnManager(VoiceTurnConfig(pause_seconds=2, max_wait_seconds=4))
        manager.offer_final("第一句", at=0)
        self.assertIsNone(manager.offer_final("第二句", at=1.9))
        self.assertIsNone(manager.offer_final("第三句", at=3.2))
        self.assertEqual(manager.deadline, 4)
        turn = manager.take_ready(now=4.1)
        self.assertEqual(turn.reason, "deadline")
        self.assertEqual(turn.parts, 3)

    def test_late_final_flushes_previous_and_buffers_next(self):
        manager = VoiceTurnManager(VoiceTurnConfig(pause_seconds=1, max_wait_seconds=5))
        manager.offer_final("第一轮", at=10)
        prev = manager.offer_final("新的第二轮", at=12)
        self.assertEqual(prev.text, "第一轮")
        self.assertTrue(manager.has_pending)
        self.assertEqual(manager.take_ready(now=13).text, "新的第二轮")

    def test_partial_unauthorized_and_duplicate_results_are_silent(self):
        manager = VoiceTurnManager()
        self.assertIsNone(manager.offer_final("尚未完成", at=0, final=False))
        self.assertIsNone(manager.offer_final("不应录入", at=0, authorized=False))
        self.assertFalse(manager.has_pending)
        manager.offer_final("已完成", at=1)
        first_deadline = manager.deadline
        manager.offer_final("已完成", at=1.2)
        self.assertEqual(manager.deadline, first_deadline)
        self.assertEqual(manager.take_ready(now=first_deadline).parts, 1)

    def test_interrupt_makes_old_output_stale(self):
        manager = VoiceTurnManager(VoiceTurnConfig(pause_seconds=1))
        manager.offer_final("原来的问题", at=0)
        old_turn = manager.flush(now=.3)
        self.assertTrue(manager.accepts_output(old_turn))
        generation = manager.interrupt()
        self.assertEqual(generation, 1)
        self.assertFalse(manager.accepts_output(old_turn))
        self.assertIsNone(manager.take_ready(now=10))
        manager.offer_final("新的问题", at=11)
        newer = manager.take_ready(now=12)
        self.assertTrue(manager.accepts_output(newer))

    def test_capacity_and_validation(self):
        manager = VoiceTurnManager(VoiceTurnConfig(pause_seconds=1, max_wait_seconds=5,
                                                   max_segments=2, max_chars=22))
        manager.offer_final("first", at=0)
        manager.offer_final("second", at=.1)
        previous = manager.offer_final("third", at=.2)
        self.assertEqual(previous.parts, 2)
        self.assertEqual(manager.take_ready(now=1.2).text, "third")
        with self.assertRaises(ValueError):
            VoiceTurnConfig(pause_seconds=0)
        with self.assertRaises(ValueError):
            VoiceTurnConfig(pause_seconds=4, max_wait_seconds=2)
        manager.offer_final("valid", at=5)
        with self.assertRaises(ValueError):
            manager.offer_final("earlier", at=4)
        with self.assertRaises(ValueError):
            manager.offer_final("x" * 200, at=6)
        self.assertEqual(manager.flush(now=6).text, "valid")


if __name__ == "__main__":
    unittest.main()
