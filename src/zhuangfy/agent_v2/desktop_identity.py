"""Stable installation identity for local desktop capabilities.

This identifies one installation, not a QQ person. Memory Core performs any
cross-channel PersonID link separately after verification and consent.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import uuid4

from zhuangfy.paths import Paths


def installation_id(path: Path | None = None) -> str:
    target = path or Paths.current().project_data_root / "desktop_installation.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
        identity = value.get("installation_id", "")
        if re.fullmatch(r"[0-9a-f]{32}", identity):
            return identity
        raise ValueError("Invalid desktop installation identity")
    except FileNotFoundError:
        identity = uuid4().hex
        temporary = target.with_name(target.name + "." + identity + ".tmp")
        temporary.write_text(json.dumps({"installation_id": identity}), encoding="utf-8")
        try:
            # The first creator wins when two desktop entry points start together.
            os.link(temporary, target)
        except FileExistsError:
            pass
        finally:
            temporary.unlink(missing_ok=True)
        return installation_id(target)


def channel_user_id() -> str:
    """Use the desktop Memory identity if configured, else this installation.

    The value is only a channel identifier. It is never proof of a PersonID.
    """
    configured = os.environ.get("ZHUANGFY_MEMORY_DESKTOP_USER_ID", "").strip()
    return configured if configured else installation_id()
