"""Message-linked, bounded visual evidence cache inspired by MaiBot.

Only image descriptions are stored, never original image bytes. Callers control
conversation scope and may delete cached visual evidence at any time.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_DESCRIPTION = 1800


@dataclass(frozen=True)
class VisualEvidence:
    message_key: str
    image_hash: str
    description: str
    status: str


def image_digest(data_url: str) -> str:
    head, sep, payload = data_url.partition(",")
    if not sep or not head.startswith("data:image/") or ";base64" not in head:
        raise ValueError("Expected base64 image")
    if len(payload) > (MAX_IMAGE_BYTES * 4 // 3 + 8):
        raise ValueError("Image too large")
    try:
        image = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid base64 image") from exc
    if not image or len(image) > MAX_IMAGE_BYTES:
        raise ValueError("Image size outside limits")
    return hashlib.sha256(image).hexdigest()


class VisionDescriptionStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            columns = [row[1] for row in db.execute(
                "PRAGMA table_info(descriptions)")]
            if columns:
                # v1 cache only supported one version per image. Rebuild without
                # discarding its latest data; new indexes include model/prompt.
                keys = [row[1] for row in db.execute("PRAGMA table_info(descriptions)")
                        if row[5]]
                if keys != ["image_hash", "model_signature"]:
                    db.execute("ALTER TABLE descriptions RENAME TO descriptions_old")
                    db.execute("""CREATE TABLE descriptions(
                        image_hash TEXT NOT NULL, model_signature TEXT NOT NULL,
                        description TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(image_hash, model_signature))""")
                    db.execute("""INSERT OR REPLACE INTO descriptions
                        (image_hash,model_signature,description,updated_at)
                        SELECT image_hash,model_signature,description,updated_at
                        FROM descriptions_old""")
                    db.execute("DROP TABLE descriptions_old")
            else:
                db.execute("""CREATE TABLE descriptions(
                    image_hash TEXT NOT NULL, model_signature TEXT NOT NULL,
                    description TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(image_hash,model_signature))""")
            db.execute("""CREATE TABLE IF NOT EXISTS message_images(
                message_key TEXT PRIMARY KEY, image_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','completed','failed')))""")
            msg_cols = {row[1] for row in db.execute("PRAGMA table_info(message_images)")}
            if "model_signature" not in msg_cols:
                db.execute("ALTER TABLE message_images ADD COLUMN model_signature TEXT NOT NULL DEFAULT ''")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def lookup(self, digest: str, signature: str) -> str:
        with self._lock, self._connect() as db:
            row = db.execute("""SELECT description FROM descriptions
                WHERE image_hash=? AND model_signature=?""", (digest, signature)).fetchone()
        return row[0] if row else ""

    def associate(self, message_key: str, digest: str,
                  status: str = "pending", signature: str = "") -> None:
        if not message_key or status not in ("pending", "completed", "failed"):
            raise ValueError("Invalid visual association")
        with self._lock, self._connect() as db:
            db.execute("""INSERT INTO message_images(message_key,image_hash,status,model_signature)
                VALUES(?,?,?,?) ON CONFLICT(message_key) DO UPDATE SET
                image_hash=excluded.image_hash,status=excluded.status,
                model_signature=excluded.model_signature""",
                (message_key, digest, status, signature))

    def store(self, message_key: str, digest: str, signature: str, description: str) -> None:
        if not description.strip():
            raise ValueError("Empty visual evidence")
        with self._lock, self._connect() as db:
            db.execute("""INSERT INTO descriptions(image_hash,model_signature,description)
                VALUES(?,?,?) ON CONFLICT(image_hash,model_signature) DO UPDATE SET
                description=excluded.description,updated_at=CURRENT_TIMESTAMP""",
                (digest, signature, description[:MAX_DESCRIPTION]))
            db.execute("""INSERT INTO message_images(message_key,image_hash,status,model_signature)
                VALUES(?,?,'completed',?) ON CONFLICT(message_key) DO UPDATE SET
                image_hash=excluded.image_hash,status='completed',
                model_signature=excluded.model_signature""", (message_key, digest, signature))

    def mark_failed(self, message_key: str, digest: str, signature: str) -> None:
        self.associate(message_key, digest, "failed", signature)

    def refresh(self, message_key: str, signature: str) -> VisualEvidence | None:
        """Fetch a completed result for an earlier pending message, if available."""
        with self._lock, self._connect() as db:
            row = db.execute("""SELECT image_hash,status,model_signature FROM message_images
                WHERE message_key=?""", (message_key,)).fetchone()
        if row is None:
            return None
        digest, status, stored_signature = row
        description = self.lookup(digest, signature) if stored_signature in ("", signature) else ""
        if description and status != "completed":
            self.associate(message_key, digest, "completed", signature)
            status = "completed"
        return VisualEvidence(message_key, digest, description, status)

    def recent(self, prefix: str, signature: str, limit: int = 3) -> list[VisualEvidence]:
        """Return only same-conversation completed visual context."""
        if not prefix:
            return []
        with self._lock, self._connect() as db:
            rows = db.execute("""SELECT m.message_key,m.image_hash,d.description
                FROM message_images m JOIN descriptions d
                ON m.image_hash=d.image_hash AND m.model_signature=d.model_signature
                WHERE substr(m.message_key,1,?)=? AND m.status='completed'
                AND d.model_signature=? ORDER BY m.rowid DESC LIMIT ?""",
                (len(prefix), prefix, signature, max(1, min(limit, 5)))).fetchall()
        return [VisualEvidence(key, digest, description, "completed")
                for key,digest,description in reversed(rows)]

    def cleanup(self, days: int = 30) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM descriptions WHERE updated_at < datetime('now',?)",
                       (f"-{max(1,min(days,365))} days",))
            db.execute("""DELETE FROM message_images WHERE status != 'pending'
                AND NOT EXISTS (SELECT 1 FROM descriptions d
                WHERE d.image_hash=message_images.image_hash
                AND d.model_signature=message_images.model_signature)""")
