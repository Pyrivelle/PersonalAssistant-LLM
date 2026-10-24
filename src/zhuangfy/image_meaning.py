"""Conversation-scoped explicit image-meaning corrections.

Never infer the sender's intentions from image pixels. Only a direct user
clarification about a recent image becomes an association.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path


def explicit_image_meaning(text: str) -> str:
    """Conservative matcher: opt in only for clear first-person explanations."""
    message = " ".join(text.strip().split())
    patterns = (
        r"^(?:其实|刚才那张图|那张图)(?:我发(?:那张图)?(?:的)?意思是|那张图(?:的)?意思是|是在表达|表示的是)[:：，, ]*(.{2,100})[。.!！]?$",
        r"^我(?:发那张图|刚才发图)(?:是想|的意思是|其实是想)(.{2,100})[。.!！]?$",
        r"^其实(?:那张图)?是在表达(.{2,100})[。.!！]?$",
    )
    for pattern in patterns:
        found = re.fullmatch(pattern, message)
        if found:
            explanation = found.group(1).strip(" ，,。!！")
            if explanation and not any(q in explanation for q in ("？", "?", "是不是", "比如说")):
                return explanation
    return ""


class ImageMeaningStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS image_meanings (
                conversation_id TEXT NOT NULL,
                image_hash TEXT NOT NULL,
                explanation TEXT NOT NULL,
                source_message_id TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(conversation_id,image_hash)
            )""")
            db.commit()

    def save(self, conversation_id: str, digest: str,
             explanation: str, source_message_id: str):
        if not all((conversation_id, digest, explanation, source_message_id)):
            raise ValueError("missing image-meaning provenance")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid image digest")
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("""INSERT INTO image_meanings
                (conversation_id,image_hash,explanation,source_message_id)
                VALUES(?,?,?,?) ON CONFLICT(conversation_id,image_hash) DO UPDATE SET
                explanation=excluded.explanation,
                source_message_id=excluded.source_message_id,
                created_at=CURRENT_TIMESTAMP""",
                (conversation_id,digest,explanation[:180],source_message_id))
            db.commit()

    def lookup(self, conversation_id: str, digest: str) -> str:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("""SELECT explanation FROM image_meanings
                WHERE conversation_id=? AND image_hash=?""",
                (conversation_id,digest)).fetchone()
        return row[0] if row else ""
