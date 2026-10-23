"""Deterministic ASR-final-segment -> conversation-turn buffering (Voice V0).

This is a pure state machine; it has no Qt objects, audio capture, LLM calls,
TTS effects or wall-clock reads. The owner supplies monotonic timestamps and
dispatches returned VoiceTurn objects exactly once. Only FINAL, authorized ASR
segments may enter; partial hypotheses are explicitly rejected.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class VoiceTurnConfig:
    pause_seconds: float = 1.3
    max_wait_seconds: float = 5.0
    max_segments: int = 6
    max_chars: int = 800

    def __post_init__(self) -> None:
        if not 0.2 <= self.pause_seconds <= 5:
            raise ValueError("pause_seconds must be between 0.2 and 5")
        if not self.pause_seconds <= self.max_wait_seconds <= 20:
            raise ValueError("max_wait_seconds must be >= pause and <=20")
        if not 1 <= self.max_segments <= 20 or not 20 <= self.max_chars <= 5000:
            raise ValueError("invalid turn capacity")


@dataclass(frozen=True, slots=True)
class VoiceTurn:
    """A completed, single logical utterance. Generation rejects stale output."""
    sequence: int
    generation: int
    text: str
    languages: tuple[str, ...]
    parts: int
    started_at: float
    finalized_at: float
    reason: Literal["quiet", "deadline", "capacity", "forced"]


class VoiceTurnManager:
    """Merge consecutive finalized ASR segments; never calls Planner itself."""

    def __init__(self, config: VoiceTurnConfig | None = None) -> None:
        self.config = config or VoiceTurnConfig()
        self._segments: list[str] = []
        self._languages: list[str] = []
        self._first_at: float | None = None
        self._last_at: float | None = None
        self._generation = 0
        self._sequence = 0

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def has_pending(self) -> bool:
        return bool(self._segments)

    @property
    def deadline(self) -> float | None:
        if not self._segments or self._first_at is None or self._last_at is None:
            return None
        return min(self._last_at + self.config.pause_seconds,
                   self._first_at + self.config.max_wait_seconds)

    def _finish(self, reason: Literal["quiet", "deadline", "capacity", "forced"],
                *, now: float) -> VoiceTurn:
        self._sequence += 1
        started = self._first_at
        if started is None:
            raise ValueError("No pending ASR segments")
        turn = VoiceTurn(self._sequence, self._generation,
                         " ".join(self._segments), tuple(self._languages),
                         len(self._segments), started, now, reason)
        self._segments.clear()
        self._languages.clear()
        self._first_at = None
        self._last_at = None
        return turn

    def offer_final(self, text: str, *, at: float, language: str = "",
                    authorized: bool = True, final: bool = True) -> VoiceTurn | None:
        """Return a previous completed turn if a new segment arrives too late.

        Host must dispatch any returned turn before scheduling the next timer.
        An unauthorized or partial segment cannot change pending state.
        """
        if not authorized or not final:
            return None
        if not isinstance(text, str):
            raise ValueError("ASR final result must be text")
        text = " ".join(text.strip().split())
        if not text:
            return None
        if len(text) > self.config.max_chars:
            raise ValueError("ASR segment exceeds configured size")
        if self._last_at is not None and at < self._last_at:
            raise ValueError("ASR timestamps must be monotonic")
        emitted: VoiceTurn | None = None
        if self.has_pending and self.deadline is not None and at >= self.deadline:
            reason = ("deadline" if self._first_at is not None and
                      self._first_at + self.config.max_wait_seconds <= at else "quiet")
            emitted = self._finish(reason, now=self.deadline)
        if self.has_pending and (len(self._segments) >= self.config.max_segments or
                                 len(" ".join(self._segments)) + 1 + len(text) > self.config.max_chars):
            # Flush previous turn before appending to prevent unbounded batches.
            emitted = self._finish("capacity", now=at)
        if self._first_at is None:
            self._first_at = at
        if self._segments and self._segments[-1] == text:
            # Typical repeated final callback: do not duplicate the utterance or
            # extend the timer unless new speech actually arrived.
            return emitted
        self._segments.append(text)
        self._languages.append(language[:12] if isinstance(language, str) else "")
        self._last_at = at
        return emitted

    def take_ready(self, *, now: float) -> VoiceTurn | None:
        due = self.deadline
        if due is None or now < due:
            return None
        assert self._first_at is not None
        reason = "deadline" if due >= self._first_at + self.config.max_wait_seconds else "quiet"
        return self._finish(reason, now=due)

    def flush(self, *, now: float) -> VoiceTurn | None:
        if not self.has_pending:
            return None
        return self._finish("forced", now=now)

    def interrupt(self) -> int:
        """Invalidate all previous in-flight outputs and drop buffered text.

        Host must still cancel/ignore Qt, TTS and model tasks explicitly.
        """
        self._generation += 1
        self._segments.clear()
        self._languages.clear()
        self._first_at = None
        self._last_at = None
        return self._generation

    def accepts_output(self, turn: VoiceTurn) -> bool:
        return turn.generation == self._generation
