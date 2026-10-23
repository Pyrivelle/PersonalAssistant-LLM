"""Host-owned, explicit tool capabilities for the V2 Planner.

No network, filesystem, message send, subprocess or device-control capability
is registered implicitly. Tools return bounded, verifiable receipts.
"""
from __future__ import annotations

import ast
import math
import operator
from datetime import datetime, timezone

from .runtime import AuthorizedToolRegistry, ToolBinding

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _calculate(source: str) -> float:
    if not isinstance(source, str) or not 1 <= len(source) <= 100:
        raise ValueError("expression must contain 1–100 characters")
    tree = ast.parse(source, mode="eval")
    def visit(node, depth: int = 0) -> float:
        if depth > 10:
            raise ValueError("expression too deeply nested")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            result = float(node.value)
        elif isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            result = _OPS[type(node.op)](visit(node.left, depth + 1),
                                         visit(node.right, depth + 1))
        elif isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            result = _UNARY[type(node.op)](visit(node.operand, depth + 1))
        else:
            raise ValueError("unsupported math expression")
        if not math.isfinite(result) or abs(result) > 1e12:
            raise ValueError("nonfinite or out-of-range result")
        return result
    return visit(tree.body)


async def _math_calculate(args: dict) -> str:
    if set(args) != {"expression"}:
        raise ValueError("only expression is allowed")
    value = _calculate(args["expression"])
    return f"计算结果：{value:g}"


async def _clock_utc_now(args: dict) -> str:
    if args:
        raise ValueError("this tool takes no arguments")
    return "当前 UTC 时间：" + datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_builtin_registry() -> AuthorizedToolRegistry:
    """Small useful read-only starter set; extensions must explicitly register."""
    registry = AuthorizedToolRegistry()
    registry.register(ToolBinding("calculator.evaluate", _math_calculate,
                                  frozenset({"private", "group"})))
    registry.register(ToolBinding("clock.utc_now", _clock_utc_now,
                                  frozenset({"private", "group"})))
    return registry
