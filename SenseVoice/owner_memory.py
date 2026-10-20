"""Explicitly link the local voice user to one QQ member-memory identity."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from llm import ROOT
from member_memory import MemberKey, MemberMemoryStore, reflect_member
from turn_planner import PlannerSettings


LINK_FILE = ROOT / "owner_link.json"
IDENTITY_FILE = ROOT / "qq_identity.json"
MEMORY_FILE = ROOT / "conversation_data" / "qq_members.sqlite3"


@dataclass(frozen=True)
class OwnerLink:
    bot_id: int
    qq_id: int

    @property
    def key(self) -> MemberKey:
        return MemberKey(self.bot_id, self.qq_id)


def current_bot_id(path: Path = IDENTITY_FILE) -> int | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))["bot_id"]
        return value if type(value) is int and value > 0 else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def load_link(path: Path = LINK_FILE, *, validate: bool = True) -> OwnerLink | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("enabled") is not True:
            return None
        link = OwnerLink(raw["bot_id"], raw["qq_id"])
        if type(link.bot_id) is not int or type(link.qq_id) is not int:
            return None
        link.key
        if validate:
            from qq_bridge import QqSettings
            if link.qq_id not in QqSettings.load().allowed_private_ids:
                return None
            planner = PlannerSettings.load(ROOT / "cognition_settings.json")
            if link.qq_id not in planner.memory_private_ids:
                return None
            active_bot = current_bot_id()
            if active_bot is not None and active_bot != link.bot_id:
                return None
        return link
    except (OSError, ValueError, TypeError, KeyError):
        return None


def save_link(bot_id: int, qq_id: int, path: Path = LINK_FILE) -> OwnerLink:
    link = OwnerLink(bot_id, qq_id)
    link.key
    from qq_bridge import QqSettings
    if qq_id not in QqSettings.load().allowed_private_ids:
        raise ValueError("该 QQ 号尚未加入私聊白名单")
    planner = PlannerSettings.load(ROOT / "cognition_settings.json")
    if qq_id not in planner.memory_private_ids:
        raise ValueError("该 QQ 号尚未启用个人记忆")
    active_bot = current_bot_id()
    if active_bot is not None and active_bot != bot_id:
        raise ValueError("关联的机器人账号与当前登录账号不一致")
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps({"enabled": True, "bot_id": bot_id, "qq_id": qq_id},
                                        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return link


def memory_snapshot(link: OwnerLink) -> str:
    if not MEMORY_FILE.is_file():
        return ""
    return MemberMemoryStore(MEMORY_FILE).context_for(link.key)


def record_voice_turn(link: OwnerLink, user: str, answer: str, settings, api_key: str) -> int:
    store = MemberMemoryStore(MEMORY_FILE)
    count = store.record_turn(link.key, user, answer)
    if count >= 6 and (api_key or settings.provider == "custom"):
        reflect_member(store, link.key, settings, api_key)
    return count
