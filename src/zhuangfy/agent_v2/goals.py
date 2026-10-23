"""Durable, opt-in, read-only goal event store (B0 foundation).

No background daemon, LLM call, message send or external tool is started here.
A future host supplies permissions and explicitly invokes poll/run actions.
SQLite transactions and leases implement at-least-once observation tasks;
non-idempotent physical actions MUST NOT use this queue.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time


@dataclass(frozen=True, slots=True)
class GoalEvent:
    goal_id: str
    owner_scope: str
    event_id: str
    attempt: int
    lease_until: float
    source_event_id: str


class GoalStore:
    """Small SQLite-backed one-off goal queue; no private message bodies stored."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=8, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=8000")
        return connection

    def _init_db(self):
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
              CREATE TABLE IF NOT EXISTS goals (
                goal_id TEXT PRIMARY KEY,
                owner_scope TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                consent INTEGER NOT NULL,
                status TEXT NOT NULL,
                due_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                max_attempts INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                lease_until REAL NOT NULL DEFAULT 0,
                last_receipt TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
              );
              CREATE INDEX IF NOT EXISTS idx_goals_ready
                ON goals(status, due_at, lease_until);
              CREATE TABLE IF NOT EXISTS goal_audit (
                event_id TEXT PRIMARY KEY,
                goal_id TEXT NOT NULL,
                action TEXT NOT NULL,
                at REAL NOT NULL,
                summary TEXT NOT NULL
              );
            """)

    def create(self, *, goal_id: str, owner_scope: str,
               source_event_id: str, due_at: float, expires_at: float,
               consent: bool = False, max_attempts: int = 2) -> bool:
        """Caller must have verified permission; consent=False is never runnable."""
        if not all((isinstance(x, str) and 1 <= len(x) <= 128
                    for x in (goal_id, owner_scope, source_event_id))):
            raise ValueError("goal identity is missing or too long")
        if type(consent) is not bool:
            raise ValueError("consent must be bool")
        if not 1 <= max_attempts <= 3 or not due_at < expires_at:
            raise ValueError("invalid goal deadline or attempt budget")
        now = time.time()
        with self._connect() as db:
            cursor = db.execute("""
              INSERT OR IGNORE INTO goals
                (goal_id, owner_scope, source_event_id, consent, status,
                 due_at, expires_at, max_attempts, created_at)
              VALUES (?, ?, ?, ?, 'scheduled', ?, ?, ?, ?)
            """, (goal_id, owner_scope, source_event_id, int(consent),
                  due_at, expires_at, max_attempts, now))
            return cursor.rowcount == 1

    def claim_due(self, *, now: float, owner_scopes: frozenset[str],
                  limit: int = 4, lease_seconds: float = 20) -> tuple[GoalEvent, ...]:
        """Atomic claims across workers. Only consented, scoped goals can start."""
        if not 1 <= limit <= 20 or not 1 <= lease_seconds <= 300:
            raise ValueError("invalid claim budget")
        if not owner_scopes:
            return ()
        claimed = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                # Reclaim crashed observation tasks. They may run again; handlers
                # must be read-only or otherwise idempotent.
                db.execute("""UPDATE goals SET status='failed', lease_until=0
                   WHERE status='running' AND lease_until <= ?
                         AND attempts >= max_attempts""", (now,))
                db.execute("""UPDATE goals SET status='scheduled', lease_until=0
                   WHERE status='running' AND lease_until <= ?
                         AND attempts < max_attempts""", (now,))
                rows = db.execute("""
                  SELECT * FROM goals WHERE status='scheduled' AND consent=1
                         AND due_at<=? AND expires_at>?
                  ORDER BY due_at, created_at LIMIT ?
                """, (now, now, max(20, limit * 8))).fetchall()
                for row in rows:
                    if row["owner_scope"] not in owner_scopes:
                        continue
                    n = row["attempts"] + 1
                    db.execute("""UPDATE goals SET status='running',
                                      attempts=?, lease_until=?
                                  WHERE goal_id=?""",
                               (n, now + lease_seconds, row["goal_id"]))
                    claimed.append(GoalEvent(
                        row["goal_id"], row["owner_scope"],
                        f"goal:{row['goal_id']}:{n}", n,
                        now + lease_seconds, row["source_event_id"]))
                    if len(claimed) >= limit:
                        break
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return tuple(claimed)

    def finish(self, event: GoalEvent, *, now: float, success: bool,
               summary: str = "", retry_delay: float = 15) -> bool:
        """Idempotent receipt. A revoked/expired/stale lease cannot complete."""
        if type(success) is not bool or retry_delay < 0:
            raise ValueError("invalid receipt")
        summary = " ".join(summary.split())[:350]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM goals WHERE goal_id=?",
                                 (event.goal_id,)).fetchone()
                if (row is None or row["status"] != "running"
                        or row["attempts"] != event.attempt
                        or row["owner_scope"] != event.owner_scope
                        or now > row["lease_until"]):
                    db.execute("ROLLBACK")
                    return False
                next_status = ("done" if success else
                               "scheduled" if row["attempts"] < row["max_attempts"]
                               and now + retry_delay < row["expires_at"] else "failed")
                due = now + retry_delay if next_status == "scheduled" else row["due_at"]
                db.execute("""UPDATE goals SET status=?, due_at=?, lease_until=0,
                               last_receipt=? WHERE goal_id=?""",
                           (next_status, due, summary, event.goal_id))
                db.execute("""INSERT INTO goal_audit
                              (event_id, goal_id, action, at, summary)
                              VALUES (?,?,?,?,?)""",
                           (event.event_id, event.goal_id, next_status, now, summary))
                db.execute("COMMIT")
                return True
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def revoke(self, goal_id: str) -> bool:
        """Immediately prevent later claims or successful receipts."""
        with self._connect() as db:
            return db.execute("""UPDATE goals SET status='revoked', lease_until=0
                                 WHERE goal_id=? AND status IN ('scheduled','running')""",
                              (goal_id,)).rowcount == 1

    def inspect(self, goal_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM goals WHERE goal_id=?",
                             (goal_id,)).fetchone()
            return dict(row) if row is not None else None

    def audit(self, goal_id: str) -> tuple[dict, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM goal_audit WHERE goal_id=? ORDER BY at",
                              (goal_id,)).fetchall()
            return tuple(dict(row) for row in rows)
