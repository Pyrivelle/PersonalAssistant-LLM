import unittest

from zhuangfy.ui.console_highlighter import console_spans


class ConsoleHighlighterTests(unittest.TestCase):
    def test_time_source_phase_and_error_are_distinct(self):
        line = "00:14:19.123  [SnowLuma] 失败  HTTP 401，连接失败"
        marked = [(line[start:start + length], style)
                  for start, length, style in console_spans(line)]
        self.assertIn(("00:14:19.123", "time"), marked)
        self.assertIn(("[SnowLuma]", "source"), marked)
        self.assertIn(("失败", "error"), marked)
        self.assertIn(("HTTP 401", "error"), marked)

    def test_multiline_continuation_can_highlight_warning(self):
        line = "    模型暂时超时，等待重试"
        marked = [(line[start:start + length], style)
                  for start, length, style in console_spans(line)]
        self.assertIn(("超时", "error"), marked)
        self.assertIn(("等待", "warning"), marked)


if __name__ == "__main__":
    unittest.main()
