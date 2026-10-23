"""Explicitly scoped, privacy-preserving Focus attention broker (A6 prototype).

MaiBot's Focus shares a single foreground Planner across several chat streams.
This module establishes the attention and ownership contract; it deliberately
does not share conversation text, memories, tools, or credentials across chats.
There is no cross-channel account linking or autonomous message delivery here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FocusKind = Literal["message", "timer", "tool_result", "skill_event"]


@dataclass(frozen=True, slots=True)
class FocusSignal:
    """Metadata only. Fetch session content later with its OWN access policy."""
    session_id: str
    event_id: str
    kind: FocusKind
    arrived_at: float
    priority: int = 0
    directed: bool = False
    privacy: Literal["private", "group"] = "private"

    def __post_init__(self) -> None:
        if not self.session_id or not self.event_id:
            raise ValueError("Focus signals require session and event IDs")
        if type(self.priority) is not int or not 0 <= self.priority <= 100:
            raise ValueError("priority must be an integer 0..100")
        if self.privacy not in {"private", "group"}:
            raise ValueError("invalid privacy scope")
        if self.kind not in {"message", "timer", "tool_result", "skill_event"}:
            raise ValueError("unsupported Focus event")


@dataclass(frozen=True, slots=True)
class FocusGroup:
    """Only explicitly listed sessions may share attention scheduling."""
    id: str
    sessions: frozenset[str]
    max_pending_per_session: int = 8
    max_total_pending: int = 64
    max_consecutive: int = 2

    def __post_init__(self) -> None:
        if not self.id or not self.sessions or any(not s for s in self.sessions):
            raise ValueError("Focus group must have an ID and explicit members")
        if not 1 <= self.max_pending_per_session <= 64:
            raise ValueError("invalid per-session queue limit")
        if not 1 <= self.max_total_pending <= 512:
            raise ValueError("invalid global queue limit")
        if not 1 <= self.max_consecutive <= 8:
            raise ValueError("invalid consecutive-turn limit")


@dataclass(frozen=True, slots=True)
class FocusLease:
    scope_id: str
    signal: FocusSignal


@dataclass(slots=True)
class _State:
    group: FocusGroup
    pending: list[FocusSignal] = field(default_factory=list)
    active: FocusSignal | None = None
    seen: dict[str, float] = field(default_factory=dict)
    last_session: str = ""
    streak: int = 0


class FocusCoordinator:
    """Single-owner event-loop coordinator, no threads, I/O or timer callbacks.

    Each Focus group has exactly one foreground lease at a time. Its session
    histories and private memories still remain isolated. Caller must validate
    upstream admission *before* offering a signal.
    """

    def __init__(self) -> None:
        self._groups: dict[str, _State] = {}
        self._members: dict[str, str] = {}

    def register_group(self, group: FocusGroup) -> None:
        if group.id in self._groups:
            raise ValueError("Focus group already registered")
        if any(session in self._members for session in group.sessions):
            raise ValueError("Session is already assigned to another Focus group")
        self._groups[group.id] = _State(group)
        self._members.update({session: group.id for session in group.sessions})

    def scope_for(self, session_id: str) -> str | None:
        """None means standalone; never auto-join unrelated private sessions."""
        return self._members.get(session_id)

    def offer(self, signal: FocusSignal, *, authorized: bool = False) -> bool:
        """Denied by default; no content/personal-memory data is retained."""
        if not authorized:
            return False
        scope = self.scope_for(signal.session_id)
        if scope is None:
            return False
        state = self._groups[scope]
        # Avoid replayed transport events. Per-session IDs prevent collisions
        # between unrelated QQ/WeChat providers.
        seen_key = f"{signal.session_id}\x00{signal.event_id}"
        if seen_key in state.seen:
            return False
        if state.active is not None and (
            state.active.session_id == signal.session_id
            and state.active.event_id == signal.event_id
        ):
            return False
        count = sum(item.session_id == signal.session_id for item in state.pending)
        if count >= state.group.max_pending_per_session:
            return False
        if len(state.pending) >= state.group.max_total_pending:
            return False
        state.pending.append(signal)
        state.seen[seen_key] = signal.arrived_at
        # A separate, bounded set of event IDs protects against replay even
        # after turns are acknowledged.
        if len(state.seen) > 4096:
            oldest = sorted(state.seen, key=state.seen.get)
            for old in oldest[:1024]:
                del state.seen[old]
        return True

    def claim(self, scope_id: str, *, now: float) -> FocusLease | None:
        """Select best eligible session; return None when Planner already busy."""
        state = self._groups.get(scope_id)
        if state is None or state.active is not None or not state.pending:
            return None
        group = state.group
        has_other = any(signal.session_id != state.last_session
                        for signal in state.pending)
        def score(signal: FocusSignal) -> tuple[float, float]:
            aging = min(25.0, max(0.0, now - signal.arrived_at) * 0.4)
            directed = 50.0 if signal.directed else 0.0
            # Fairness prevents one busy group from permanently monopolizing
            # attention. Directed messages still take precedence.
            burst_penalty = (
                50.0 if state.streak >= group.max_consecutive
                and has_other and signal.session_id == state.last_session
                and not signal.directed else 0.0
            )
            return (signal.priority + aging + directed - burst_penalty,
                    -signal.arrived_at)
        winner = max(state.pending, key=score)
        state.pending.remove(winner)
        state.active = winner
        return FocusLease(scope_id, winner)

    def complete(self, lease: FocusLease) -> None:
        state = self._groups.get(lease.scope_id)
        if state is None or state.active != lease.signal:
            raise ValueError("Unknown or stale Focus lease")
        session = lease.signal.session_id
        state.streak = state.streak + 1 if state.last_session == session else 1
        state.last_session = session
        state.active = None

    def cancel(self, scope_id: str, *, session_id: str) -> int:
        """Cancel queued work on disconnect/deauthorization, not active turns."""
        state = self._groups[scope_id]
        before = len(state.pending)
        state.pending = [signal for signal in state.pending
                         if signal.session_id != session_id]
        return before - len(state.pending)

    def snapshot(self, scope_id: str) -> dict:
        """Safe metadata for GUI/telemetry; never expose user message bodies."""
        state = self._groups[scope_id]
        return {
            "scope_id": scope_id,
            "active_session": state.active.session_id if state.active else None,
            "pending_sessions": sorted({item.session_id for item in state.pending}),
            "pending_count": len(state.pending),
        }
