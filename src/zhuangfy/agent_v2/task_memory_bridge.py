"""Operator-side bridge: connect a stored, approved GoalStore task to Memory Core.

Only a trusted local operator with the separate Memory ADMIN token may run
this bridge. Never expose it to QQ events, the model, or ToolRegistry.
"""
from __future__ import annotations

from .goals import GoalStore
from zhuangfy.memory.client import MemoryChannel, MemoryClient


def link_existing_qq_goal(*, goals: GoalStore, admin: MemoryClient,
                          goal_id: str, bot_id: str, user_id: str) -> dict:
    goal = goals.inspect(goal_id)
    if goal is None:
        raise ValueError("Unknown GoalStore goal")
    if not goal["consent"] or goal["status"] in ("revoked", "expired"):
        raise PermissionError("Goal has no current operator authorization")
    identity = MemoryChannel("qq", bot_id, user_id)
    # Never permit ambiguous, pre-migration scopes such as qq:private:42.
    scope = "qq:" + bot_id + ":private:" + user_id
    if goal["owner_scope"] != scope:
        raise PermissionError("GoalStore owner does not match the QQ identity")
    # A GoalStore source_event_id is NOT automatically a Memory Timeline ID.
    # The caller must explicitly supply the exact entry_id returned by
    # MemoryClient.receive as source_event_id when creating a linkable goal.
    return admin.admin_bind_goal_origin(
        identity, task_id=goal_id, owner_scope=scope,
        source_entry_id=goal["source_event_id"],
    )
