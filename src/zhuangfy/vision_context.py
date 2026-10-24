"""Local vision descriptions keyed by image bytes, and message-associated context.

Only text descriptions are persisted. Caller must pass a verified message key;
the image payload itself is never written to this database.
"""
from __future__ import annotations

import base64
import hashlib
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VisualEvidence:
    message_key: str
    image_hash: str
    description: str
    status: str = "completed"


def image_digest(data_url: str) -> str:
    """Hash actual image bytes, not the data URL string or temporary QQ URL."""
    head, sep, payload = data_url.partition(",")
    if len(payload) > 28 * 1024 * 1024:\n        raise ValueError("Image payload too large")\n    if not sep or not head.startswith("data:image/") or ";base64" not in head:
        raise ValueError("Expected a base64 image data URL")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Invalid image payload") from exc
    if not raw or len(raw) > 20 * 1024 * 1024:
        raise ValueError("Image payload outside supported bounds")
    return hashlib.sha256(raw).hexdigest()


class VisionDescriptionStore:
    """SQLite cache with per-message references and pending/complete state."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS descriptions(
                image_hash TEXT NOT NULL, description TEXT NOT NULL,
                model_signature TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(image_hash, model_signature)
            )""")
            con.execute("""CREATE TABLE IF NOT EXISTS message_images(
                message_key TEXT PRIMARY KEY, image_hash TEXT NOT NULL,
                model_signature TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK(status IN ('pending','completed','failed'))
            )""")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def lookup(self, image_hash: str, model_signature: str) -> str:
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT description FROM descriptions WHERE image_hash=? AND model_signature=?",
                (image_hash, model_signature)).fetchone()
        return row[0] if row else ""

    def associate(self, message_key: str, image_hash: str, status: str = "pending", signature: str = "") -> None:
        if not message_key or status not in ("pending", "completed", "failed"):
            raise ValueError("Invalid message association")
        with self._lock, self._connect() as con:
            con.execute("""INSERT INTO message_images(message_key,image_hash,status,model_signature)
                VALUES(?,?,?,?) ON CONFLICT(message_key) DO UPDATE SET
                image_hash=excluded.image_hash,status=excluded.status,
                model_signature=excluded.model_signature""",
                (message_key, image_hash, status, signature))

    def store(self, message_key: str, image_hash: str, signature: str, description: str) -> None:
        if not description.strip():
            raise ValueError("Empty vision result")
        with self._lock, self._connect() as con:
            con.execute("""INSERT INTO descriptions(image_hash,description,model_signature)
                VALUES(?,?,?) ON CONFLICT(image_hash,model_signature) DO UPDATE SET
                description=excluded.description,updated_at=CURRENT_TIMESTAMP""", (image_hash, description, signature))
            con.execute("""INSERT INTO message_images(message_key,image_hash,status,model_signature)
                VALUES(?,?,'completed',?) ON CONFLICT(message_key) DO UPDATE SET
                image_hash=excluded.image_hash,status='completed',
                model_signature=excluded.model_signature""", (message_key, image_hash, signature))

    def refresh(self, message_key: str, signature: str) -> VisualEvidence | None:
        """Refresh a pending message reference from a completed cached description."""
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT image_hash,status,model_signature FROM message_images WHERE message_key=?",
                (message_key,)).fetchone()
        if row is None:
            return None
        digest, status = row
        description = self.lookup(digest, signature)
        if description and status != "completed":
            self.associate(message_key, digest, "completed", signature)
            status = "completed"
        return VisualEvidence(message_key, digest, description, status)

    def mark_failed(self, message_key: str, image_hash: str, signature: str) -> None:
        self.associate(message_key, image_hash, "failed", signature)

    def cleanup(self, *, days: int = 30) -> None:
        """Bound local retention of descriptions and message references."""
        cutoff = f"-{max(1, min(days, 365))} days"
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM descriptions WHERE updated_at < datetime('now', ?)", (cutoff,))
            con.execute("""DELETE FROM message_images WHERE image_hash NOT IN
                (SELECT image_hash FROM descriptions) AND status != 'pending'""")
