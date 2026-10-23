"""Opt-in asyncio bridge from QQ's admitted turns to the shared Focus broker.

Scope: one QQ bot process; session IDs are local to that bot instance. Explicit
membership is required. In shadow mode no turn waits on Focus. In active mode,
Focus claims are acquired before generation_lock, allowing pending sessions to
compete while the previous turn is running. Each lease is released even on
reply failure or task cancellation. No chat content or memory is shared.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

from .focus import FocusCoordinator, FocusGroup, FocusLease, FocusSignal


@dataclass(frozen=True, slots=True)
class FocusDecision:
    mode: str
    session_id: str
    scope_id: str = ""
    admitted: bool = False


class QqFocusRuntime:
    def __init__(self, mode: str = "off",
                 groups: tuple[tuple[str, tuple[str, ...]], ...] = ()) -> None:
        if mode not in ("off", "shadow", "active"):
            raise ValueError("invalid Focus mode")
        self.mode = mode
        self.coordinator = FocusCoordinator()
        for name, sessions in groups:
            self.coordinator.register_group(FocusGroup(name, frozenset(sessions)))
        self._lock = asyncio.Lock()
        self._pending: dict[tuple[str, str], asyncio.Future] = {}
        self._decisions: list[FocusDecision] = []

    @property
    def decisions(self) -> tuple[FocusDecision, ...]:
        return tuple(self._decisions[-40:])

    def _pump(self, now: float) -> None:
        for scope in list(self.coordinator._groups):
            lease = self.coordinator.claim(scope, now=now)
            if lease is None:
                continue
            key = (lease.signal.session_id, lease.signal.event_id)
            future = self._pending.get(key)
            if future is None or future.cancelled():
                self.coordinator.complete(lease)
                continue
            future.set_result(lease)

    @asynccontextmanager
    async def turn(self, session_id: str, event_id: str, *,
                   directed: bool = False, priority: int = 0):
        """Focus only sees already-authorized, admitted message metadata."""
        scope = self.coordinator.scope_for(session_id)
        if self.mode == "off" or scope is None:
            yield FocusDecision(self.mode, session_id)
            return
        if self.mode == "shadow":
            # Observe only; no timeouts or priorities change real QQ execution.
            decision = FocusDecision("shadow", session_id, scope, True)
            self._decisions.append(decision)
            yield decision
            return
        loop = asyncio.get_running_loop()
        unique = (session_id, event_id)
        signal = FocusSignal(session_id, event_id, "message", loop.time(),
                             priority=priority, directed=directed,
                             privacy="group" if session_id.startswith("group:") else "private")
        lease: FocusLease | None = None
        async with self._lock:
            if unique in self._pending:
                raise ValueError("Focus event already queued")
            future = loop.create_future()
            if not self.coordinator.offer(signal, authorized=True):
                raise ValueError("Focus rejected admitted message (duplicate or queue full)")
            self._pending[unique] = future
            self._pump(loop.time())
        try:
            lease = await future
            decision = FocusDecision("active", session_id, scope, True)
            self._decisions.append(decision)
            yield decision
        finally:
            async with self._lock:
                self._pending.pop(unique, None)
                if lease is not None:
                    self.coordinator.complete(lease)
                else:
                    self.coordinator.cancel(scope, session_id=session_id)
                    future.cancel()
                self._pump(loop.time())

    def snapshot(self) -> dict:
        return {scope: self.coordinator.snapshot(scope)
                for scope in self.coordinator._groups}
