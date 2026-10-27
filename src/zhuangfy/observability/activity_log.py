"""Current-process console events and a separate durable chat timeline.

Managed child processes can forward events over a local UDP socket.
The console begins empty each launch; only bounded conversation events are
stored locally so the chat stream can survive a dashboard restart.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import socket
import threading
from collections import deque
from contextvars import ContextVar
from datetime import datetime

from .chat_journal import ChatJournal, is_chat_event


_LOCK = threading.Lock()
_EVENTS: deque[dict] = deque(maxlen=2000)
_REVISION = 0
try:
    _CHAT_JOURNAL = ChatJournal() if not os.environ.get("ZHUANGFY_EVENT_PORT") else None
    _CHAT_EVENTS: deque[dict] = deque(
        _CHAT_JOURNAL.recent() if _CHAT_JOURNAL is not None else [], maxlen=5000)
except (OSError, sqlite3.Error):
    _CHAT_JOURNAL = None
    _CHAT_EVENTS = deque(maxlen=5000)
_CHAT_REVISION = len(_CHAT_EVENTS)
_CURRENT_TURN: ContextVar[tuple[str, str]] = ContextVar("zhuangfy_current_turn", default=("", ""))
_SECRET = re.compile(
    r"(?i)(Bearer\s+)[^\s]+|(sk-[A-Za-z0-9_-]{8,})|"
    r"((?:[A-Z_]*API_KEY|ACCESS_TOKEN|PASSWORD|密码|密钥)\s*[:=]\s*)[^\s,，;；]+"
)


def redact(value: object) -> str:
    text = str(value)
    return _SECRET.sub(lambda match: (match.group(1) or match.group(3) or "") + "[已隐藏]", text)


def _append(entry: dict) -> None:
    global _REVISION, _CHAT_REVISION
    with _LOCK:
        _EVENTS.append(entry)
        _REVISION += 1
        if is_chat_event(entry):
            _CHAT_EVENTS.append(entry)
            _CHAT_REVISION += 1
            if _CHAT_JOURNAL is not None:
                try:
                    _CHAT_JOURNAL.append(entry)
                except (OSError, sqlite3.Error):
                    pass


def set_current_turn(turn_id: str, channel: str) -> None:
    """Associate subsequent events in this thread/async task with a chat turn."""
    _CURRENT_TURN.set((turn_id, channel))


def current_turn_context() -> tuple[str, str]:
    """Return the conversation turn attached to this model call, if any."""
    return _CURRENT_TURN.get()


def log_event(source: str, phase: str, detail: object = "", *,
              turn_id: str = "", channel: str = "") -> None:
    """Record a visible event without interrupting a conversation on failure."""
    current_turn, current_channel = _CURRENT_TURN.get()
    entry = {
        "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "pid": os.getpid(),
        "source": redact(source)[:80],
        "phase": redact(phase)[:80],
        "detail": redact(detail)[:4000],
        "turn_id": (turn_id or current_turn)[:64],
        "channel": (channel or current_channel)[:32],
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
            "turn_id": str(event.get("turn_id", ""))[:64],
            "channel": str(event.get("channel", ""))[:32],
        }
        _append(entry)
        return True
    except (ValueError, TypeError, KeyError):
        return False


def session_snapshot() -> tuple[int, list[dict]]:
    with _LOCK:
        return _REVISION, list(_EVENTS)


def chat_snapshot() -> tuple[int, list[dict]]:
    with _LOCK:
        return _CHAT_REVISION, list(_CHAT_EVENTS)


def format_event(event: dict) -> str:
    stamp = str(event["time"])[11:23]
    detail = str(event["detail"]).replace("\r", "").replace("\n", "\n    ")
    return f"{stamp}  [{event['source']}] {event['phase']}  {detail}".rstrip()
