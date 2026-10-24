"""Conservative host-side explicit silence detection.

Never infer silence from sentiments or from quotations/discussion of stop.
Only unambiguous, standalone requests affecting the current turn match.
"""
from __future__ import annotations

import re

_EXPLICIT = (
    r"(?:这条|这次|本条|这句话|这一条)(?:消息)?(?:请|就|可以)?(?:不用|不要|别)(?:再)?(?:回复|回我|回答|理我)",
    r"(?:请)?(?:不要|别)(?:回复|回答)(?:这条|本条|这次)(?:消息)?",
    r"(?:这条|这次|本条)(?:消息)?(?:保持静默|不用说话|不要说话)",
)
_SINGLE = re.compile(r"^(?:" + "|".join(_EXPLICIT) + r")[。.!！\s]*$")


def explicit_current_turn_silence(text: str) -> bool:
    return bool(_SINGLE.fullmatch(text.strip()))
