"""Operator-only B1 background goal host. No chat or autonomous send APIs.

Commands are deliberately explicit:
  add  - record one consented, read-only action with the consent event ID
  once - run a single bounded tick (needs policy enabled)
  serve - process due goals in a cancellable headless loop (needs policy enabled)
  list - inspect goal or draft status without loading skill Python
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import signal
import sys
import time

from .autonomy import BackgroundGoalRunner, GoalGrant
from .goals import GoalStore
from .initiative import InitiativeGrant, InitiativeInbox
from .tools import build_builtin_registry


def load_policy(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
        raise ValueError("autonomy policy is missing, symlinked or oversized")
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or set(config) - {
        "enabled", "poll_seconds", "owner_grants"
    } or type(config.get("enabled", False)) is not bool:
        raise ValueError("invalid autonomy policy")
    owners = config.get("owner_grants", {})
    if not isinstance(owners, dict) or len(owners) > 20:
        raise ValueError("owner_grants must be a small object")
    grants, inquiries = [], []
    for owner, definition in owners.items():
        if not isinstance(owner, str) or len(owner) > 128 or not isinstance(definition, dict):
            raise ValueError("invalid owner scope definition")
        if set(definition) - {"tools", "draft_inquiries"}:
            raise ValueError("invalid owner grant field")
        names = definition.get("tools", [])
        draft = definition.get("draft_inquiries", False)
        if (not isinstance(names, list) or not 1 <= len(names) <= 12
                or any(not isinstance(name, str) for name in names)
                or len(set(names)) != len(names)
                or type(draft) is not bool):
            raise ValueError("owner grants need unique explicit tool names")
        grants.append(GoalGrant(owner, frozenset(names)))
        inquiries.append(InitiativeGrant(owner, enabled=draft))
    poll = config.get("poll_seconds", 30)
    if type(poll) not in (int, float) or not 5 <= poll <= 3600:
        raise ValueError("poll_seconds must be in 5–3600")
    return config.get("enabled", False), float(poll), tuple(grants), tuple(inquiries)


async def _run(args) -> None:
    enabled, poll_seconds, grants, inquiry_grants = load_policy(args.policy)
    if not enabled:
        raise ValueError("autonomy policy disabled; no goal will be executed")
    store = GoalStore(args.database)
    inbox = InitiativeInbox(args.database)
    runtime = BackgroundGoalRunner(
        store, build_builtin_registry(), grants,
        enabled=True, inbox=inbox, inquiry_grants=inquiry_grants)
    if args.action == "once":
        for result in await runtime.run_once():
            print(json.dumps(result.__dict__ if hasattr(result, "__dict__") else {
                "goal_id": result.goal_id, "tool": result.tool_name,
                "committed": result.committed, "success": result.success,
                "inquiry_drafted": result.inquiry_drafted,
            }, ensure_ascii=False))
        return
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    try:
        await runtime.serve(stop, poll_seconds=poll_seconds)
    except KeyboardInterrupt:
        stop.set()


def main() -> None:
    parser = argparse.ArgumentParser(description="Explicitly enabled ZhuangFY read-only goal host")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    commands = parser.add_subparsers(dest="action", required=True)
    add = commands.add_parser("add", help="operator-approved one-off tool action")
    for arg in ("goal-id", "owner-scope", "source-event-id", "tool-name"):
        add.add_argument("--" + arg, required=True)
    add.add_argument("--arguments", default="{}")
    add.add_argument("--due-at", type=float, required=True)
    add.add_argument("--expires-at", type=float, required=True)
    add.add_argument("--consent-confirmed", action="store_true", required=True)
    commands.add_parser("once", help="run one tick, only if policy enabled")
    commands.add_parser("serve", help="run explicit background process, only if policy enabled")
    inspect = commands.add_parser("inspect", help="inspect goal and drafts without running code")
    inspect.add_argument("--goal-id", required=True)
    args = parser.parse_args()
    if args.action == "add":
        if not args.consent_confirmed:
            parser.error("explicit consent confirmation required")
        if not args.policy:
            parser.error("--policy required to ensure owner/tool grants")
        enabled, _, grants, _ = load_policy(args.policy)
        registry = build_builtin_registry()
        matching = next((g for g in grants if g.owner_scope == args.owner_scope), None)
        kind = "group" if ":group:" in args.owner_scope else "private"
        if not enabled or matching is None or args.tool_name not in matching.tool_names or not registry.available(args.tool_name, scope=kind, allow_mutation=False):
            parser.error("tool and owner must be explicitly granted, read-only and enabled")
        arguments = json.loads(args.arguments)
        if not isinstance(arguments, dict):
            parser.error("tool arguments must be a JSON object")
        if not registry._check_schema(arguments, registry._tools[args.tool_name].parameters):
            parser.error("arguments violate registered tool schema")
        created = GoalStore(args.database).create(
            goal_id=args.goal_id, owner_scope=args.owner_scope,
            source_event_id=args.source_event_id, due_at=args.due_at,
            expires_at=args.expires_at, consent=True,
            tool_name=args.tool_name, tool_arguments=arguments,
        )
        print("created" if created else "already exists")
    elif args.action == "inspect":
        store = GoalStore(args.database)
        print(json.dumps(store.inspect(args.goal_id), ensure_ascii=False, indent=2))
        if args.policy:
            _, _, _, inquiries = load_policy(args.policy)
            for grant in inquiries:
                for item in InitiativeInbox(args.database).list_drafts(grant.owner_scope):
                    if item["goal_id"] == args.goal_id:
                        print(json.dumps(item, ensure_ascii=False, indent=2))
    else:
        if args.policy is None:
            parser.error("--policy is required for execution")
        asyncio.run(_run(args))


if __name__ == "__main__":
    main()
