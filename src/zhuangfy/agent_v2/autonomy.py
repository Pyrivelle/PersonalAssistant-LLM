"""B1 opt-in background executor: consented scheduled goals -> read-only tools.

Does not generate spontaneous speech, send messages, or run an idle LLM loop.
A separate operator process supplies explicit per-owner, per-tool grants and
must invoke serve()/run_once(). Every receipt is committed to GoalStore.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import time
from typing import Callable

from .goals import GoalEvent, GoalStore
from .initiative import InitiativeInbox, InitiativeGrant
from .runtime import AuthorizedToolRegistry, ToolReceipt


@dataclass(frozen=True, slots=True)
class GoalGrant:
    owner_scope: str
    tool_names: frozenset[str]

    def __post_init__(self) -> None:
        if not self.owner_scope or not self.tool_names or any(
                not isinstance(x, str) or not x for x in self.tool_names):
            raise ValueError("Explicit owner and tool grants required")


@dataclass(frozen=True, slots=True)
class BackgroundResult:
    goal_id: str
    event_id: str
    success: bool
    committed: bool
    tool_name: str
    receipt: str
    inquiry_drafted: bool = False


class BackgroundGoalRunner:
    """A single bounded tick, plus a cancellable, low-frequency service loop.

    The persistent store uses at-least-once claims; every registered callable
    MUST be read-only or externally idempotent. B1 permits only read-only tools.
    No chat/QQ/voice dispatch occurs here.
    """

    def __init__(self, store: GoalStore, registry: AuthorizedToolRegistry,
                 grants: tuple[GoalGrant, ...], *,
                 enabled: bool = False, batch_size: int = 2,
                 tool_timeout: float = 6.0,
                 lease_seconds: float = 20.0,
                 clock: Callable[[], float] = time.time,
                 inbox: InitiativeInbox | None = None,
                 inquiry_grants: tuple[InitiativeGrant, ...] = ()) -> None:
        if type(enabled) is not bool:
            raise ValueError("enabled must be explicitly boolean")
        if not 1 <= batch_size <= 5:
            raise ValueError("batch_size must be 1–5")
        if not all(math.isfinite(x) for x in (tool_timeout, lease_seconds)) or not (
            0.1 <= tool_timeout and batch_size * (tool_timeout + 0.5) < lease_seconds <= 300
        ):
            raise ValueError("tool deadline must be smaller than the lease")
        if len({g.owner_scope for g in grants}) != len(grants):
            raise ValueError("Duplicated owner grants")
        self.store, self.registry = store, registry
        self.grants = {g.owner_scope: g.tool_names for g in grants}
        self.enabled = enabled
        self.batch_size, self.tool_timeout, self.lease_seconds = batch_size, tool_timeout, lease_seconds
        self.clock = clock
        if len({g.owner_scope for g in inquiry_grants}) != len(inquiry_grants):
            raise ValueError("Duplicated inquiry grants")
        if any(g.owner_scope not in self.grants for g in inquiry_grants):
            raise ValueError("Proactive inquiry scope needs a goal tool grant")
        self.inbox = inbox
        self.inquiry_grants = {g.owner_scope: g for g in inquiry_grants}

    async def run_once(self) -> tuple[BackgroundResult, ...]:
        """Returns [] when disabled. Does not queue tasks or start a daemon."""
        if not self.enabled or not self.grants:
            return ()
        now = self.clock()
        events = self.store.claim_due(
            now=now, owner_scopes=frozenset(self.grants),
            limit=self.batch_size, lease_seconds=self.lease_seconds,
            require_action=True,
        )
        results = []
        for event in events:
            # The claim may have been revoked between retrieval and execution.
            current = self.store.inspect(event.goal_id)
            if not current or current["status"] != "running" or not current["consent"]:
                continue
            permitted = (event.tool_name in self.grants.get(event.owner_scope, ())
                         and self.registry.available(event.tool_name, scope=(
                             "group" if ":group:" in event.owner_scope else "private"),
                             allow_mutation=False))
            if not permitted:
                receipt = ToolReceipt(event.tool_name, False, "后台工具未授权或不可用")
                terminal_failure = True
            else:
                receipt = await self.registry.invoke(
                    event.tool_name, event.tool_arguments,
                    scope=("group" if ":group:" in event.owner_scope else "private"),
                    allow_mutation=False, timeout=self.tool_timeout,
                )
                terminal_failure = False
            finished_at = self.clock()
            committed = self.store.finish(
                event, now=finished_at, success=receipt.success,
                summary=receipt.as_context(), retry_delay=15,
                terminal_failure=terminal_failure,
            )
            drafted = False
            grant = self.inquiry_grants.get(event.owner_scope)
            if (receipt.success and committed and grant is not None and
                    self.inbox is not None and current["inquiry_allowed"] and
                    current["inquiry_topic"]):
                # A grounded *draft*, not permission to send or generate speech.
                drafted = self.inbox.propose_from_goal(
                    goal_id=event.goal_id, source_event_id=event.source_event_id,
                    owner_scope=event.owner_scope, evidence=receipt.as_context(),
                    now=finished_at, grant=grant, topic=current["inquiry_topic"])
            results.append(BackgroundResult(
                event.goal_id, event.event_id, receipt.success and committed,
                committed, event.tool_name, receipt.as_context(), drafted,
            ))
        return tuple(results)

    async def serve(self, stop: asyncio.Event, *, poll_seconds: float = 30) -> None:
        """Host-managed loop: sleep on event; no LLM calls during idle periods."""
        if not self.enabled:
            return
        if not 5 <= poll_seconds <= 3600:
            raise ValueError("poll_seconds must be 5–3600")
        while not stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass
