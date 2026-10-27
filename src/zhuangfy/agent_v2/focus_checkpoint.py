"""F2 metadata-only Focus checkpoints. Never persist private message text.

A checkpoint captures only the last completed session and fairness streak.
Pending/active leases are intentionally not recovered: a process restart must
re-admit new events through the channel authorization gate.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
from pathlib import Path
import sqlite3


def group_signature(sessions: frozenset[str]) -> str:
    return hashlib.sha256("\n".join(sorted(sessions)).encode("utf-8")).hexdigest()


class FocusCheckpointStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS focus_checkpoints (
                instance_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                member_signature TEXT NOT NULL,
                last_session TEXT NOT NULL,
                streak INTEGER NOT NULL,
                PRIMARY KEY(instance_id, group_id)
            )""")

    def read(self, instance_id: str, group_id: str,
             members: frozenset[str]) -> tuple[str, int] | None:
        if not instance_id or not group_id:
            raise ValueError("invalid checkpoint scope")
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                """SELECT last_session, streak FROM focus_checkpoints
                   WHERE instance_id=? AND group_id=? AND member_signature=?""",
                (instance_id, group_id, group_signature(members)),
            ).fetchone()
        if row is None or row[0] not in members or not 1 <= row[1] <= 8:
            return None
        return row[0], row[1]

    def write(self, instance_id: str, group_id: str, members: frozenset[str],
              last_session: str, streak: int) -> None:
        if not instance_id or not group_id or last_session not in members or not 1 <= streak <= 8:
            raise ValueError("invalid Focus completion")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                """INSERT INTO focus_checkpoints
                  (instance_id, group_id, member_signature, last_session, streak)
                  VALUES (?,?,?,?,?)
                  ON CONFLICT(instance_id, group_id) DO UPDATE SET
                    member_signature=excluded.member_signature,
                    last_session=excluded.last_session,
                    streak=excluded.streak""",
                (instance_id, group_id, group_signature(members), last_session, streak),
            )


def session_ref(*, channel: str, account: str, kind: str, peer: str) -> str:
    """Canonical metadata-only identity for future cross-channel Focus adapters."""
    import re
    fields = (channel, account, kind, peer)
    if (kind not in ("private", "group") or
            any(not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", part) for part in fields)):
        raise ValueError("invalid channel/account/conversation identity")
    return ":".join(fields)
