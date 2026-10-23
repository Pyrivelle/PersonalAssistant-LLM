"""Independent deterministic implementation of MaiBot-style debounce behavior."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

DebounceMode=Literal["fast","natural","steady"]

@dataclass(frozen=True,slots=True)
class WaitPreset:
    quiet: float
    active: float
    same_author: float
    other_author: float

WAIT_PRESETS: dict[DebounceMode,WaitPreset]={
    "fast":WaitPreset(1,3,1,.5),
    "natural":WaitPreset(2,5,2,.5),
    "steady":WaitPreset(4,7,4,2),
}

class MessageDebouncer:
    """One instance per conversation. Uses caller monotonic timestamps; no API calls."""
    def __init__(self, mode: DebounceMode="natural")->None:
        if mode not in WAIT_PRESETS: raise ValueError("Unknown debounce mode")
        self.mode=mode
        self._first_at=None
        self._last_at=None
        self._last_author=None
        self._deadline=None
        self._urgent_deadline=None

    @property
    def deadline(self)->float|None: return self._deadline

    def observe(self, *, author:str, now:float, is_bare_image:bool=False,
                is_pure_emoji:bool=False, mentioned:bool=False)->float:
        if not author: raise ValueError("Message author is required")
        previous_at,previous_author=self._last_at,self._last_author
        arrival=max(now,previous_at) if previous_at is not None else now
        self._last_at,self._last_author=arrival,author
        fresh=self._first_at is None
        if fresh: self._first_at=arrival
        if mentioned:
            urgent=arrival+1
            self._urgent_deadline=min(self._urgent_deadline,urgent) if self._urgent_deadline is not None else urgent
        if self._deadline is not None and self._deadline<=now: return self._deadline
        preset=WAIT_PRESETS[self.mode]
        if fresh:
            duration=preset.active if previous_at is not None and arrival-previous_at<=30 else preset.quiet
            if is_bare_image: duration=max(duration,4)
        else: duration=preset.same_author if author==previous_author else preset.other_author
        candidate=arrival+duration
        if is_pure_emoji and not fresh and self._deadline is not None:
            candidate=min(candidate,self._deadline)
        deadline=min(candidate,self._first_at+8)
        if self._urgent_deadline is not None: deadline=min(deadline,self._urgent_deadline)
        self._deadline=deadline
        return deadline

    def remaining(self,now:float)->float:
        return max(0,(self._deadline if self._deadline is not None else now)-now)

    def clear_batch(self)->None:
        self._first_at=self._deadline=self._urgent_deadline=None
