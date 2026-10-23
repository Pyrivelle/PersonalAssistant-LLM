"""Channel-neutral admission policy using GPLv3 Maisaka frequency components.

Original dynamic gate/likelihood code lives in maisaka_port/, attributed and
pinned to a MaiBot source commit. This is ZhuangFY's channel-event adapter,
not part of the original upstream source.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .maisaka_port.dynamic_gate import DynamicReplyGate
from .maisaka_port.reply_likelihood import (
    ReplyLikelihoodInput, estimate_reply_probability, summarize_pending_texts,
)

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
    recent_message_count: int = 0
    seconds_since_bot_message: float = 3600.0
    pending_count: int = 1
    at_other: bool = False


@dataclass(frozen=True, slots=True)
class GateResult:
    decision: TriggerDecision
    reason: str
    delay_seconds: float = 0.0
    score: float = 0.0
    threshold: float = 0.0


class EventTurnGate:
    """Compose Maisaka's model with authorization, debounce and event precedence.

    Group likelihood is based on MaiBot's fitted data, not calibrated to
    ZhuangFY. Live admission remains disabled until the host supplies valid
    per-conversation context. 'hold' does not discard backlog.
    """

    def __init__(self, *, window_seconds: float = 3600) -> None:
        if window_seconds <= 0:
            raise ValueError("Invalid window length")
        self.window_seconds = window_seconds
        self._gates: dict[str, DynamicReplyGate] = {}
        self._seen: dict[str, dict[str, float]] = {}
        self._last_metrics: dict[str, dict[str, float]] = {}

    def _prune(self, session_id: str, now: float) -> dict[str, float]:
        seen = self._seen.setdefault(session_id, {})
        for key, timestamp in tuple(seen.items()):
            if timestamp < now - self.window_seconds:
                del seen[key]
        return seen

    def record_reply(self, session_id: str, now: float) -> None:
        """Call after a *logical* reply was fully acknowledged, not per bubble."""
        self._gates.setdefault(session_id, DynamicReplyGate(self.window_seconds)).record_reply(now)
        # Full-delivery ACK arrives after gate.evaluate; refresh observable
        # metrics immediately instead of reporting the pre-send count.
        if session_id in self._last_metrics:
            self._last_metrics[session_id]["actual_replies"] += 1.0

    def evaluate(self, event: TurnEvent, *, now: float, debounce_remaining: float = 0,
                 frequency: float = 1.0, backoff_remaining: float = 0) -> GateResult:
        if not event.authorized or not event.session_id or not event.event_id:
            return GateResult("ignore", "unauthorized_or_invalid")
        if debounce_remaining > 0:
            return GateResult("delay", "debounce", debounce_remaining)
        if event.kind != "message":
            if event.parent_turn_active and event.kind in {"tool_result", "timer"}:
                return GateResult("trigger", "continuation")
            return GateResult("hold", "unsolicited_event_not_enabled")
        seen = self._prune(event.session_id, now)
        if event.event_id in seen:
            return GateResult("ignore", "duplicate_event")
        if event.session_kind == "private" or event.directed:
            seen[event.event_id] = now
            if event.session_kind == "group" and event.directed:
                self._gates.setdefault(event.session_id, DynamicReplyGate(self.window_seconds)).record_forced_turn(now)
            return GateResult("trigger", "directed_or_private")
        if frequency <= 0:
            return GateResult("hold", "frequency_silent")
        if backoff_remaining > 0:
            return GateResult("delay", "idle_backoff", backoff_remaining)
        seen[event.event_id] = now
        _, has_question, placeholder_only = summarize_pending_texts(
            [event.text], has_at_bot=event.directed,
        )
        data = ReplyLikelihoodInput(
            mention_bot=False,
            at_other=event.at_other,
            has_question_mark=has_question,
            placeholder_only=placeholder_only,
            recent_self_ratio=event.recent_self_ratio,
            recent_message_count=event.recent_message_count,
            seconds_since_bot_message=event.seconds_since_bot_message,
            pending_count=max(1, event.pending_count),
        )
        probability = estimate_reply_probability(data)
        gate = self._gates.setdefault(event.session_id, DynamicReplyGate(self.window_seconds))
        if gate.take_uncounted_message_ids([event.event_id]):
            gate.record_proactive_demand(probability, now)
        decision = gate.evaluate(probability, frequency, now)
        if decision.should_trigger:
            gate.close_round()
        self._last_metrics[event.session_id] = {
            "expected_replies": decision.expected_replies,
            "actual_replies": float(decision.actual_replies),
            "target_replies": decision.target_replies,
            "threshold": decision.threshold,
        }
        return GateResult(
            "trigger" if decision.should_trigger else "hold",
            "maisaka_dynamic_gate", score=probability, threshold=decision.threshold,
        )

    def snapshot(self, session_id: str, now: float) -> dict[str, float]:
        self._prune(session_id, now)
        return dict(self._last_metrics.get(session_id, {
            "expected_replies": 0.0, "actual_replies": 0.0,
            "target_replies": 0.0, "threshold": 0.0,
        }))
