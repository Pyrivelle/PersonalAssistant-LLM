"""Recognize a narrow, explicit request to reply after a stated delay."""
from __future__ import annotations

import re

MAX_REPLY_DELAY_SECONDS = 600
_AMOUNTS = {"半": 0.5, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_DURATION = r"(?P<amount>\d{1,3}|半|[一二两三四五六七八九十])\s*(?P<unit>分钟|分|秒钟|秒)"
_REPLY = r"(?:回复|回答|回我|告诉我)"
_AFTER = re.compile(_DURATION + r"\s*后.{0,12}?" + _REPLY)
_WAIT = re.compile(r"(?:等|过)\s*" + _DURATION + r".{0,8}?" + _REPLY)


def requested_reply_delay(text: str) -> int | None:
    """Return a bounded duration only for an explicit delayed-reply request."""
    if not isinstance(text, str) or len(text) > 500:
        return None
    match = _AFTER.search(text) or _WAIT.search(text)
    if match is None:
        return None
    phrase = text[match.start():match.end()]
    if re.search(r"(?:不|别|不要|无需|不用)\s*(?:再)?" + _REPLY, phrase):
        return None
    if re.search(r"(?:^|[，。！？\s])我(?:会|准备|打算|要)?\s*$", text[:match.start()]):
        return None
    raw = match.group("amount")
    amount = float(raw) if raw.isascii() and raw.isdecimal() else _AMOUNTS[raw]
    seconds = int(amount * (60 if match.group("unit") in {"分钟", "分"} else 1))
    return seconds if 1 <= seconds <= MAX_REPLY_DELAY_SECONDS else None
