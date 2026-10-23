"""Event-first Planner gate, inspired by Maisaka's scheduling layers.

The numerical score is an intentionally *uncalibrated* bootstrap heuristic.
MaiBot's fitted coefficients are not copied: they reflect MaiBot's own logs.
All timestamps here use a caller-provided monotonic clock.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

TriggerDecision = Literal["trigger", "delay", "hold", "ignore"]


@dataclass(frozen=True, slots=True)
class TurnEvent:
    session_id: str
    event_id: str
    kind: Literal["message", "timer", "tool_result", "plugin"]
    session_kind: Literal["private", "group"]
    text: str = ""
    directed: bool = False
    authorized: bool = True
    parent_turn_active: bool = False
    recent_self_ratio: float = 0.0


@dataclass(frozen=True, slots=True)
class GateResult:
    decision: TriggerDecision
    reason: str
    delay_seconds: float = 0.0
    score: float = 0.0


class EventTurnGate:
    """A1/A2 debounce should run before this gate, once per message batch.

    Gate order: authorization -> debounce -> active tool/wait continuation ->
    directed/private -> adaptive group-frequency budget -> passive hold.
    A 'hold' is not a deletion; the channel owns pending message retention.
    """

    def __init__(self, *, window_seconds: float = 3600, baseline: float = .45) -> None:
        if window_seconds <= 0 or not 0 <= baseline <= 1:
            raise ValueError("Invalid gate configuration")
        self.window_seconds = window_seconds
        self.baseline = baseline
        self._demand: dict[str, deque[tuple[float, float]]] = {}
        self._replies: dict[str, deque[float]] = {}
        self._seen: dict[str, dict[str, float]] = {}

    def _prune(self, session_id: str, now: float) -> None:
        floor = now - self.window_seconds
        for values in (self._demand.setdefault(session_id, deque()),
                       self._replies.setdefault(session_id, deque())):
            while values and values[0][0 if values is self._demand[session_id] else 0] < floor:
                values.popleft()
        seen = self._seen.setdefault(session_id, {})
        for key, at in list(seen.items()):
            if at < floor:
                del seen[key]

    def record_reply(self, session_id: str, now: float) -> None:
        self._prune(session_id, now)
        self._replies[session_id].append(now)

    def _score(self, event: TurnEvent) -> float:
        # Explicitly a deterministic starter policy, not a learned probability.
        score = .16
        if "？" in event.text or "?" in event.text:
            score += .30
        if len(event.text.strip()) > 16:
            score += .12
        if event.text.strip().startswith(("[图片", "[表情")):
            score -= .10
        score -= min(.16, max(0.0, event.recent_self_ratio) * .18)
        return min(1.0, max(0.0, score))

    def evaluate(self, event: TurnEvent, *, now: float, debounce_remaining: float = 0,
                 frequency: float = 1.0, backoff_remaining: float = 0.0) -> GateResult:
        if not event.authorized or not event.session_id or not event.event_id:
            return GateResult("ignore", "unauthorized_or_invalid")
        if debounce_remaining > 0:
            return GateResult("delay", "debounce", debounce_remaining)
        if event.kind != "message":
            if event.parent_turn_active and event.kind in {"tool_result", "timer"}:
                return GateResult("trigger", "continuation")
            # Plugin-originated/unsolicited signals must be explicitly admitted
            # through the future proactive-policy layer.
            return GateResult("hold", "unsolicited_event_not_enabled")
        self._prune(event.session_id, now)
        seen = self._seen[event.session_id]
        if event.event_id in seen:
            return GateResult("ignore", "duplicate_event")
        seen[event.event_id] = now
        if event.session_kind == "private" or event.directed:
            return GateResult("trigger", "directed_or_private")
        if frequency <= 0:
            return GateResult("hold", "frequency_silent")
        if backoff_remaining > 0:
            return GateResult("delay", "idle_backoff", backoff_remaining)
        score = self._score(event)
        self._demand[event.session_id].append((now, score))
        expected = sum(value for _, value in self._demand[event.session_id])
        actual = len(self._replies[event.session_id])
        ratio = max(0.0, min(1.0, frequency))
        overshoot = max(0.0, actual - expected * ratio)
        threshold = self.baseline + (1-ratio) * .35 + min(.35, overshoot / max(1.0, expected) * .35)
        return GateResult("trigger" if score >= threshold else "hold",
                          "group_gate", score=score)

    def snapshot(self, session_id: str, now: float) -> dict[str, float]:
        self._prune(session_id, now)
        return {
            "expected_replies": round(sum(s for _, s in self._demand[session_id]), 3),
            "actual_replies": float(len(self._replies[session_id])),
        }
