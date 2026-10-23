"""Trusted local R1 controls. Requires separate admin token; no model calls."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from zhuangfy.memory.client import MemoryChannel, MemoryClient

FIELDS = {
    "familiarity": int, "trust": int, "closeness": int,
    "tier_ceiling": str, "tone": str,
    "allow_affection": bool, "avoid_nicknames": bool,
    "auto_growth": bool, "frozen": bool, "enabled": bool,
    "lock_familiarity": bool, "lock_trust": bool, "lock_closeness": bool,
}

def parse_change(text: str):
    name, sep, value = text.partition("=")
    if not sep or name not in FIELDS:
        raise ValueError("Use a permitted field=value pair")
    typ = FIELDS[name]
    if typ is bool:
        if value.lower() not in ("true", "false"):
            raise ValueError("Boolean values must be true or false")
        return name, value.lower() == "true"
    if typ is int:
        if not value.isdecimal():
            raise ValueError("Expected a non-negative integer")
        return name, int(value)
    if not 1 <= len(value) <= 20:
        raise ValueError("Invalid string setting")
    return name, value


def main() -> int:
    p = argparse.ArgumentParser(description="R1 admin controls, verified identities only")
    p.add_argument("--platform", required=True, choices=("qq","desktop","wechat"))
    p.add_argument("--account", required=True, help="Authenticated channel account ID")
    p.add_argument("--user", required=True, help="Verified external user ID")
    sp = p.add_subparsers(dest="command", required=True)
    sp.add_parser("status")
    sp.add_parser("events").add_argument("--limit", type=int, default=30)
    enroll = sp.add_parser("enroll")
    enroll.add_argument("--tier", choices=("L0","L1","L2","L3"), default="L0")
    enroll.add_argument("--allow-affection", action="store_true")
    correct = sp.add_parser("correct")
    correct.add_argument("--version", type=int, required=True)
    correct.add_argument("--set", action="append", required=True, dest="changes")
    erase = sp.add_parser("erase")
    erase.add_argument("--yes-erase", action="store_true", required=True)
    args = p.parse_args()
    client = MemoryClient.from_environment(enabled_var="ZHUANGFY_MEMORY_ADMIN_CLI", admin=True)
    if client is None:
        p.error("Set ZHUANGFY_MEMORY_ADMIN_CLI=1 and ZHUANGFY_MEMORY_ADMIN_TOKEN in environment")
    identity = MemoryChannel(args.platform, args.account, args.user)
    if args.command == "status":
        result = client.relationship_get(identity)
    elif args.command == "events":
        result = client.relationship_events(identity, args.limit)
    elif args.command == "enroll":
        result = client.relationship_enroll(identity, initial_tier=args.tier,
                                           allow_affection=args.allow_affection)
    elif args.command == "correct":
        try:
            changes = dict(parse_change(kv) for kv in args.changes)
        except ValueError as error:
            p.error(str(error))
        result = client.relationship_correct(identity, expected_version=args.version,
                                             changes=changes)
    else:
        result = client.relationship_erase(identity)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
