"""Local, cross-process activity journal for the desktop control console.

One file per process and day avoids competing log rotation on Windows. Message
text is intentionally logged for the owner's console; obvious credentials are
redacted, and HTTP headers or raw requests are never recorded.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path


LOG_DIR = Path(__file__).resolve().parent / "logs"
_LOCK = threading.Lock()
_SECRET = re.compile(
    r"(?i)(Bearer\s+)[^\s]+|(sk-[A-Za-z0-9_-]{8,})|"
    r"((?:[A-Z_]*API_KEY|ACCESS_TOKEN|PASSWORD|密码|密钥)\s*[:=]\s*)[^\s,，;；]+"
)


def redact(value: object) -> str:
    text = str(value)
    return _SECRET.sub(lambda match: (match.group(1) or match.group(3) or "") + "[已隐藏]", text)


def log_event(source: str, phase: str, detail: object = "") -> None:
    """Best-effort append: a log failure must never interrupt a conversation."""
    now = datetime.now().astimezone()
    entry = {"time": now.isoformat(timespec="milliseconds"), "pid": os.getpid(),
             "source": redact(source), "phase": redact(phase), "detail": redact(detail)}
    path = LOG_DIR / f"activity-{now:%Y-%m-%d}-{os.getpid()}.jsonl"
    try:
        with _LOCK:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def available_days() -> list[str]:
    try:
        return sorted({path.name[9:19] for path in LOG_DIR.glob("activity-????-??-??-*.jsonl")},
                      reverse=True)
    except OSError:
        return []


def read_day(day: str) -> list[dict]:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return []
    entries = []
    for path in LOG_DIR.glob(f"activity-{day}-*.jsonl"):
        try:
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(item, dict) and {"time", "source", "phase", "detail"} <= item.keys():
                        entries.append(item)
        except OSError:
            continue
    return sorted(entries, key=lambda item: (item["time"], item.get("pid", 0)))


def format_event(event: dict) -> str:
    stamp = str(event["time"])[11:23]
    detail = str(event["detail"]).replace("\r", "").replace("\n", "\n    ")
    return f"{stamp}  [{event['source']}] {event['phase']}  {detail}".rstrip()
