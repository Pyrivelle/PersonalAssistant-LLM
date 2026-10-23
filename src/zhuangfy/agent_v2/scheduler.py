"""Unified event admission for QQ and future transports.

A transport provides a fully verified normalized message batch and its
debounce remaining time. This module owns the admission decision, not sending.
MaiBot's calibrated-for-MaiBot group gate is used only on explicit opt-in.
"""
from __future__ import annotations

from dataclasses import dataclass

from .turn_trigger import EventTurnGate, TurnEvent, GateResult


@dataclass(slots=True)
class SchedulerConfig:
    group_frequency: float = 0.45
    idle_backoff_start: int = 2
    idle_backoff_base_seconds: float = 2.0
    idle_backoff_cap_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not 0 <= self.group_frequency <= 1:
            raise ValueError("group_frequency must be between 0 and 1")


class UnifiedTurnScheduler:
    """One host-wide scheduler; each conversation has independent gate state."""

    def __init__(self, config: SchedulerConfig | None = None) -> None:
        self.config = config or SchedulerConfig()
        self._gate = EventTurnGate()
        self._idle_counts: dict[str, int] = {}
        self._backoff_until: dict[str, float] = {}

    def decide(self, event: TurnEvent, *, now: float,
               debounce_remaining: float = 0.0) -> GateResult:
        backoff = max(0.0, self._backoff_until.get(event.session_id, 0) - now)
        return self._gate.evaluate(
            event, now=now, debounce_remaining=debounce_remaining,
            backoff_remaining=backoff,
            frequency=self.config.group_frequency if event.session_kind == "group" else 1.0,
        )

    def record_cycle(self, session_id: str, *, now: float, action: str,
                     delivered: bool = False, group: bool = False) -> None:
        if delivered:
            self._gate.record_reply(session_id, now)
        if not group or action != "stop":
            self._idle_counts.pop(session_id, None)
            self._backoff_until.pop(session_id, None)
            return
        count = self._idle_counts.get(session_id, 0) + 1
        self._idle_counts[session_id] = count
        cfg = self.config
        if count >= cfg.idle_backoff_start:
            delay = min(cfg.idle_backoff_cap_seconds,
                        cfg.idle_backoff_base_seconds * 2 ** min(10, count - cfg.idle_backoff_start))
            self._backoff_until[session_id] = now + delay

    def metrics(self, session_id: str, *, now: float) -> dict[str, float]:
        return self._gate.snapshot(session_id, now)
