"""Local vision descriptions keyed by image bytes, and message-associated context.

Only text descriptions are persisted. Caller must pass a verified message key;
the image payload itself is never written to this database.
"""
from __future__ import annotations

import base64
import hashlib
import sqlite3
import threading
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
    if not sep or not head.startswith("data:image/") or ";base64" not in head:
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
                image_hash TEXT PRIMARY KEY, description TEXT NOT NULL,
                model_signature TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )""")
            con.execute("""CREATE TABLE IF NOT EXISTS message_images(
                message_key TEXT PRIMARY KEY, image_hash TEXT NOT NULL,
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

    def associate(self, message_key: str, image_hash: str, status: str = "pending") -> None:
        if not message_key or status not in ("pending", "completed", "failed"):
            raise ValueError("Invalid message association")
        with self._lock, self._connect() as con:
            con.execute("""INSERT INTO message_images(message_key,image_hash,status)
                VALUES(?,?,?) ON CONFLICT(message_key) DO UPDATE SET
                image_hash=excluded.image_hash,status=excluded.status""",
                (message_key, image_hash, status))

    def store(self, message_key: str, image_hash: str, signature: str, description: str) -> None:
        if not description.strip():
            raise ValueError("Empty vision result")
        with self._lock, self._connect() as con:
            con.execute("""INSERT INTO descriptions(image_hash,description,model_signature)
                VALUES(?,?,?) ON CONFLICT(image_hash) DO UPDATE SET
                description=excluded.description,model_signature=excluded.model_signature,
                updated_at=CURRENT_TIMESTAMP""", (image_hash, description, signature))
            con.execute("""INSERT INTO message_images(message_key,image_hash,status)
                VALUES(?,?,'completed') ON CONFLICT(message_key) DO UPDATE SET
                image_hash=excluded.image_hash,status='completed'""", (message_key, image_hash))

    def refresh(self, message_key: str, signature: str) -> VisualEvidence | None:
        """Refresh a pending message reference from a completed cached description."""
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT image_hash,status FROM message_images WHERE message_key=?",
                (message_key,)).fetchone()
        if row is None:
            return None
        digest, status = row
        description = self.lookup(digest, signature)
        if description and status != "completed":
            self.associate(message_key, digest, "completed")
            status = "completed"
        return VisualEvidence(message_key, digest, description, status)
