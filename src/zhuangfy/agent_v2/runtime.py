"""Bounded, permission-aware Planner action loop.

A small independent implementation of Maisaka's key *execution semantics*:
plan -> authorized action -> feedback -> replan -> reply/wait/stop.
No channel I/O, model SDK, filesystem, or plugins are imported here.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import monotonic
from typing import Awaitable, Callable, Literal

from .planner import PlanDecision, choose_action


@dataclass(frozen=True, slots=True)
class ToolReceipt:
    name: str
    success: bool
    summary: str

    def as_context(self) -> str:
        # Tool output is untrusted data. Never embed arbitrary tool results in
        # a system instruction or assume an action succeeded without a receipt.
        safe = " ".join(self.summary.split())[:450]
        return f"{self.name}: {'成功' if self.success else '失败'}；{safe}"


@dataclass(frozen=True, slots=True)
class ToolBinding:
    name: str
    handler: Callable[[dict], Awaitable[str]]
    scopes: frozenset[str] = field(default_factory=lambda: frozenset({"private"}))
    mutating: bool = False
    description: str = ""
    parameters: dict = field(default_factory=dict)


class AuthorizedToolRegistry:
    """Explicit host registration and per-call authorization; deny by default."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolBinding] = {}

    def register(self, tool: ToolBinding) -> None:
        if not tool.name or tool.name in self._tools:
            raise ValueError("Tool name empty or already registered")
        self._tools[tool.name] = tool

    def available(self, name: str, *, scope: str, allow_mutation: bool = False) -> bool:
        tool = self._tools.get(name)
        return bool(tool and scope in tool.scopes and (not tool.mutating or allow_mutation))

    def public_names(self, *, scope: str, allow_mutation: bool = False) -> tuple[str, ...]:
        return tuple(name for name in self._tools if self.available(name, scope=scope, allow_mutation=allow_mutation))

    def prompt_catalog(self, *, scope: str, allow_mutation: bool = False) -> str:
        """Only host-registered, authorized tool schemas are shown to the model."""
        import json
        lines = []
        for name in self.public_names(scope=scope, allow_mutation=allow_mutation)[:12]:
            tool = self._tools[name]
            spec = json.dumps(tool.parameters, ensure_ascii=False, separators=(",", ":"))
            lines.append(f"{name}：{tool.description[:150]}；参数 schema：{spec[:450]}")
        return "\n".join(lines)[:2400]

    @staticmethod
    def _check_schema(arguments: dict, schema: dict) -> bool:
        """Small no-dependency guard for host-declared top-level object fields."""
        if not schema:
            return True
        if schema.get("type") != "object":
            return False
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            return False
        if any(key not in arguments for key in required):
            return False
        if schema.get("additionalProperties") is False and any(
                key not in properties for key in arguments):
            return False
        for key, value in arguments.items():
            prop = properties.get(key, {})
            if not isinstance(prop, dict):
                return False
            kind = prop.get("type")
            if kind == "string":
                if not isinstance(value, str) or len(value) > prop.get("maxLength", 1024):
                    return False
            elif kind == "integer":
                if type(value) is not int:
                    return False
            elif kind == "number":
                if type(value) not in (int, float):
                    return False
            elif kind == "boolean":
                if type(value) is not bool:
                    return False
            elif kind is not None:
                return False
        return True

    async def invoke(self, name: str, arguments: dict, *, scope: str,
                     allow_mutation: bool = False, timeout: float = 6.0) -> ToolReceipt:
        if not self.available(name, scope=scope, allow_mutation=allow_mutation):
            return ToolReceipt(name, False, "工具未授权或不可用")
        if not isinstance(arguments, dict) or len(repr(arguments)) > 3000:
            return ToolReceipt(name, False, "工具参数不合法或过长")
        if not self._check_schema(arguments, self._tools[name].parameters):
            return ToolReceipt(name, False, "工具参数不符合已注册的 schema")
        try:
            output = await asyncio.wait_for(self._tools[name].handler(dict(arguments)),
                                            timeout=max(0.1, timeout))
            return ToolReceipt(name, True, str(output)[:450])
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            return ToolReceipt(name, False, f"执行失败（{type(exc).__name__}）")


@dataclass(frozen=True, slots=True)
class CycleResult:
    plan: PlanDecision
    action: Literal["reply", "wait", "stop"]
    receipts: tuple[ToolReceipt, ...]
    rounds: int
    reason: str = ""

    def tool_feedback(self) -> str:
        return "\n".join(receipt.as_context() for receipt in self.receipts)[:1200]


class AgentCycleEngine:
    """One logical turn. The event scheduler decides whether to create it.

    Tools must be independently registered/authorized. No tool is installed by
    default. A failed/denied tool cannot silently become a successful action.
    """

    def __init__(self, *, max_rounds: int = 4, max_tools: int = 2,
                 total_timeout: float = 30.0) -> None:
        if not 1 <= max_rounds <= 8 or not 0 <= max_tools <= 6 or not 1 <= total_timeout <= 120:
            raise ValueError("Agent cycle budgets outside allowed bounds")
        self.max_rounds = max_rounds
        self.max_tools = max_tools
        self.total_timeout = total_timeout

    async def run(
        self,
        planner: Callable[[tuple[ToolReceipt, ...]], Awaitable[PlanDecision]],
        *,
        scope: str = "private",
        registry: AuthorizedToolRegistry | None = None,
        allow_tools: bool = False,
        allow_mutation: bool = False,
        allow_silence: bool = False,
        already_waited: bool = False,
        tool_timeout: float = 6.0,
    ) -> CycleResult:
        deadline = monotonic() + self.total_timeout
        receipts: list[ToolReceipt] = []
        invoked: set[tuple[str, str]] = set()
        tools_used = 0
        fallback = PlanDecision()
        for round_no in range(1, self.max_rounds + 1):
            remaining = deadline - monotonic()
            if remaining <= 0:
                return CycleResult(fallback, "reply", tuple(receipts), round_no-1, "total_timeout")
            try:
                plan = await asyncio.wait_for(planner(tuple(receipts)), timeout=remaining)
            except asyncio.CancelledError:
                raise
            except Exception:
                return CycleResult(fallback, "reply", tuple(receipts), round_no, "planner_failure")
            if not isinstance(plan, PlanDecision):
                return CycleResult(fallback, "reply", tuple(receipts), round_no, "invalid_plan")
            fallback = plan
            authorized = bool(
                allow_tools and registry is not None
                and registry.available(plan.tool_name, scope=scope, allow_mutation=allow_mutation)
            )
            action = choose_action(plan, allow_silence=allow_silence,
                                   already_waited=already_waited, tools_enabled=authorized)
            if action != "tool":
                reason = "tool_denied" if plan.action == "tool" else ""
                return CycleResult(plan, action, tuple(receipts), round_no, reason)
            if tools_used >= self.max_tools or round_no >= self.max_rounds:
                return CycleResult(plan, "reply", tuple(receipts), round_no, "budget_exhausted")
            key = (plan.tool_name, repr(sorted((plan.tool_arguments or {}).items())))
            if key in invoked:
                return CycleResult(plan, "reply", tuple(receipts), round_no, "duplicate_tool")
            invoked.add(key)
            tools_used += 1
            remaining = deadline - monotonic()
            if remaining <= 0:
                return CycleResult(plan, "reply", tuple(receipts), round_no, "total_timeout")
            receipt = await registry.invoke(
                plan.tool_name, plan.tool_arguments or {},
                scope=scope, allow_mutation=allow_mutation,
                timeout=min(tool_timeout, remaining),
            )
            receipts.append(receipt)
            # Failed tools do not trigger repeated retries or pretend success.
            if not receipt.success:
                return CycleResult(plan, "reply", tuple(receipts), round_no, "tool_failed")
        return CycleResult(fallback, "reply", tuple(receipts), self.max_rounds, "budget_exhausted")
