"""User-authored, person-scoped self description kept separate from inferred memory."""

from __future__ import annotations

import time

from .database import MemoryDatabase


class SelfProfileStore:
    def __init__(self, database: MemoryDatabase):
        self.database = database
        with database.write() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS self_profiles (
                    person_id TEXT PRIMARY KEY REFERENCES persons(person_id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    full_text TEXT NOT NULL,
                    prompt_summary TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
            """)

    def get(self, person_id: str) -> dict | None:
        with self.database.connection() as db:
            row = db.execute(
                "SELECT version,full_text,prompt_summary,updated_at FROM self_profiles "
                "WHERE person_id=?", (person_id,)).fetchone()
            return dict(row) if row else None

    def set(self, person_id: str, *, expected_version: int,
            full_text: str, prompt_summary: str) -> dict:
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("Invalid self-profile version")
        if not isinstance(full_text, str) or not 1 <= len(full_text.strip()) <= 10000:
            raise ValueError("Self-profile text must be 1..10000 characters")
        if not isinstance(prompt_summary, str) or not 1 <= len(prompt_summary.strip()) <= 1400:
            raise ValueError("Self-profile summary must be 1..1400 characters")
        with self.database.write() as db:
            if db.execute("SELECT 1 FROM persons WHERE person_id=?", (person_id,)).fetchone() is None:
                raise ValueError("Unknown person")
            old = db.execute("SELECT version FROM self_profiles WHERE person_id=?", (person_id,)).fetchone()
            current = old["version"] if old else 0
            if current != expected_version:
                raise ValueError("Stale self-profile version")
            db.execute("""
                INSERT INTO self_profiles(person_id,version,full_text,prompt_summary,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(person_id) DO UPDATE SET
                    version=excluded.version, full_text=excluded.full_text,
                    prompt_summary=excluded.prompt_summary, updated_at=excluded.updated_at
            """, (person_id, current + 1, full_text.strip(),
                  prompt_summary.strip(), int(time.time())))
        return self.get(person_id)
