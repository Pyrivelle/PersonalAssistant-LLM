"""Bounded, current-process activity stream for the desktop console.

Managed child processes can forward events over a local UDP socket. Nothing is
written to disk; a new console launch begins with an empty view.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
from collections import deque
from datetime import datetime


_LOCK = threading.Lock()
_EVENTS: deque[dict] = deque(maxlen=2000)
_REVISION = 0
_SECRET = re.compile(
    r"(?i)(Bearer\s+)[^\s]+|(sk-[A-Za-z0-9_-]{8,})|"
    r"((?:[A-Z_]*API_KEY|ACCESS_TOKEN|PASSWORD|密码|密钥)\s*[:=]\s*)[^\s,，;；]+"
)


def redact(value: object) -> str:
    text = str(value)
    return _SECRET.sub(lambda match: (match.group(1) or match.group(3) or "") + "[已隐藏]", text)


def _append(entry: dict) -> None:
    global _REVISION
    with _LOCK:
        _EVENTS.append(entry)
        _REVISION += 1


def log_event(source: str, phase: str, detail: object = "") -> None:
    """Record a visible event without interrupting a conversation on failure."""
    entry = {
        "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "pid": os.getpid(),
        "source": redact(source)[:80],
        "phase": redact(phase)[:80],
        "detail": redact(detail)[:4000],
    }
    _append(entry)
    port = os.environ.get("ZHUANGFY_EVENT_PORT", "")
    token = os.environ.get("ZHUANGFY_EVENT_TOKEN", "")
    if port.isdecimal() and token:
        try:
            packet = json.dumps({"token": token, "event": entry}, ensure_ascii=False).encode("utf-8")
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(packet, ("127.0.0.1", int(port)))
        except (OSError, ValueError):
            pass


def receive_event(packet: bytes, token: str) -> bool:
    """Accept one event from a managed child, authenticated to this launch."""
    try:
        data = json.loads(packet)
        event = data["event"]
        if data["token"] != token or not isinstance(event, dict):
            return False
        if not all(isinstance(event.get(field), str) for field in ("time", "source", "phase", "detail")):
            return False
        entry = {
            "time": event["time"][:40],
            "pid": int(event.get("pid", 0)),
            "source": redact(event["source"])[:80],
            "phase": redact(event["phase"])[:80],
            "detail": redact(event["detail"])[:4000],
        }
        _append(entry)
        return True
    except (ValueError, TypeError, KeyError):
        return False


def session_snapshot() -> tuple[int, list[dict]]:
    with _LOCK:
        return _REVISION, list(_EVENTS)


def format_event(event: dict) -> str:
    stamp = str(event["time"])[11:23]
    detail = str(event["detail"]).replace("\r", "").replace("\n", "\n    ")
    return f"{stamp}  [{event['source']}] {event['phase']}  {detail}".rstrip()
