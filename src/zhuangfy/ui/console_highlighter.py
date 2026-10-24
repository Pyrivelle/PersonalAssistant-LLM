"""Colorize the in-app event console without changing or interpreting events."""

from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat


PREFIX = re.compile(r"^(\d{2}:\d{2}:\d{2}(?:\.\d+)?)  (\[[^\]]+\]) ([^ ]+)  ")
ERROR = re.compile(r"(?i)\b(?:HTTP\s*[45]\d\d|ECONNRESET|EACCES|ERROR|Exception|Traceback)\b|失败|错误|异常|超时|拒绝")
WARNING = re.compile(r"(?i)\bWARN(?:ING)?\b|等待|跳过|回退|重试|限流|未就绪")
SUCCESS = re.compile(r"(?i)\bHTTP\s*2\d\d\b|完成|成功|已连接|已投递|回复")
LEVEL_ERROR = re.compile(r"失败|错误|异常|超时|拒绝|断开")
LEVEL_WARNING = re.compile(r"等待|跳过|回退|重试|排队|未就绪")
LEVEL_SUCCESS = re.compile(r"完成|成功|已连接|已投递|回复|就绪")

COLORS = {
    "time": "#e4bd68",
    "source": "#6ac4d0",
    "source_model": "#b7a7ef",
    "source_qq": "#7fb6f1",
    "source_memory": "#70c7ad",
    "phase": "#d6e4ea",
    "error": "#f08080",
    "warning": "#f4c879",
    "success": "#8bd7a5",
}


def console_spans(line: str) -> list[tuple[int, int, str]]:
    """Return deterministic highlight spans for a formatted event line."""
    spans: list[tuple[int, int, str]] = []
    prefix = PREFIX.match(line)
    body_start = 0
    if prefix:
        stamp, source, phase = prefix.group(1, 2, 3)
        spans.append((prefix.start(1), len(stamp), "time"))
        source_style = ("source_model" if "模型" in source else
                        "source_qq" if "QQ" in source else
                        "source_memory" if "记忆" in source else "source")
        spans.append((prefix.start(2), len(source), source_style))
        level = ("error" if LEVEL_ERROR.search(phase) else
                 "warning" if LEVEL_WARNING.search(phase) else
                 "success" if LEVEL_SUCCESS.search(phase) else "phase")
        spans.append((prefix.start(3), len(phase), level))
        body_start = prefix.end()
    body = line[body_start:]
    for pattern, style in ((WARNING, "warning"), (SUCCESS, "success"), (ERROR, "error")):
        spans.extend((body_start + match.start(), len(match.group()), style)
                     for match in pattern.finditer(body))
    return spans


class ConsoleHighlighter(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)
        self._formats: dict[str, QTextCharFormat] = {}
        for name, color in COLORS.items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            if name in {"time", "source", "source_model", "source_qq", "source_memory",
                        "error", "warning", "success"}:
                fmt.setFontWeight(QFont.Weight.DemiBold)
            self._formats[name] = fmt

    def highlightBlock(self, text: str) -> None:
        for start, length, style in console_spans(text):
            self.setFormat(start, length, self._formats[style])
