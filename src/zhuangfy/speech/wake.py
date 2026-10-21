"""Wake-name correction and the local conversation window.

Only the beginning of an utterance is treated as a wake call.  The ASR model
still produces its original text; correction happens after decoding.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

DEFAULT_NAME = "庄方宜"
DEFAULT_ALIASES = "庄天师，小庄，庄方怡，庄芳怡，庄方一，庄天使"
LEADING = " \t\r\n，,。.!！?？：:、"
TRAILING = " \t\r\n，,。.!！?？：:、"


def parse_aliases(value: str) -> tuple[str, ...]:
    """Accept Chinese/English commas, semicolons, or newlines from the UI."""
    return tuple(
        dict.fromkeys(x.strip() for x in re.split(r"[,，;；\n]+", value) if x.strip())
    )


@dataclass(frozen=True)
class WakeResult:
    event: str  # ignored, woke, or accepted
    text: str = ""
    called_by_name: bool = False


class ConversationGate:
    def __init__(
        self, name: str = DEFAULT_NAME, aliases: tuple[str, ...] = (), timeout: int = 30
    ):
        self.name = name.strip() or DEFAULT_NAME
        self.aliases = tuple(sorted(set((self.name, *aliases)), key=len, reverse=True))
        self.timeout = timeout
        self.active_until: float | None = None

    def reset(self) -> None:
        self.active_until = None

    def remaining(self, now: float | None = None) -> int:
        if self.active_until is None:
            return 0
        now = time.monotonic() if now is None else now
        if now >= self.active_until:
            self.reset()
            return 0
        return max(1, int(self.active_until - now + 0.999))

    def touch(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if self.remaining(now):
            self.active_until = now + self.timeout

    def process(self, text: str, now: float | None = None) -> WakeResult:
        now = time.monotonic() if now is None else now
        was_active = bool(self.remaining(now))
        original = text.strip()
        if not original:
            return WakeResult("ignored")
        body = original.lstrip(LEADING)
        match = next((alias for alias in self.aliases if body.startswith(alias)), None)
        # A place name should not be mistaken for the short nickname.
        if match == "小庄" and body[len(match) :].startswith("园"):
            match = None
        if match:
            rest = body[len(match) :]
            corrected = self.name if not rest.strip(TRAILING) else self.name + rest
            self.active_until = now + self.timeout
            return WakeResult("accepted" if was_active else "woke", corrected, True)
        if was_active:
            self.active_until = now + self.timeout
            return WakeResult("accepted", original)
        return WakeResult("ignored")
