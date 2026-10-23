"""Grounded proactive *intent* queue, never an unsolicited message sender.

A companion may choose to ask a question because a user-approved goal reached
a meaningful result. This module records a draft candidate with provenance;
it does not generate a synthetic memory or contact any user.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
from pathlib import Path
import sqlite3


@dataclass(frozen=True, slots=True)
class InitiativeGrant:
    owner_scope: str
    enabled: bool = False
    daily_limit: int = 1
    min_gap_seconds: float = 8 * 3600

    def __post_init__(self) -> None:
        if not self.owner_scope or type(self.enabled) is not bool:
            raise ValueError("invalid initiative owner/consent")
        if not 1 <= self.daily_limit <= 3 or not 3600 <= self.min_gap_seconds <= 7 * 86400:
            raise ValueError("invalid initiative rate limit")


class InitiativeInbox:
    """Durable, rate-limited ideas for future authorized Replyer composition.

    A draft is not permission to send. The eventual dispatcher needs separate
    consent, quiet-hour checks, privacy and recipient-specific rate limits.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS initiative_drafts (
                goal_id TEXT PRIMARY KEY,
                owner_scope TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                reason TEXT NOT NULL,
                topic TEXT NOT NULL,
                evidence_excerpt TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft'
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_initiative_owner
                          ON initiative_drafts(owner_scope, created_at)""")
            db.execute("""CREATE TABLE IF NOT EXISTS initiative_reviews (
                goal_id TEXT PRIMARY KEY,
                owner_scope TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL NOT NULL,
                proposed_text TEXT NOT NULL,
                reviewed_at REAL NOT NULL,
                reason TEXT NOT NULL
            )""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=8, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            yield db
        finally:
            db.close()

    def propose_from_goal(self, *, goal_id: str, source_event_id: str,
                          owner_scope: str, evidence: str, now: float,
                          grant: InitiativeGrant, topic: str = "") -> bool:
        """Only a confirmed, allowed goal receipt may produce a candidate.

        The caller must verify the goal succeeded and was committed. Text in
        evidence is untrusted tool data and never used as a system instruction.
        """
        if not grant.enabled or grant.owner_scope != owner_scope:
            return False
        if not owner_scope or ":private:" not in owner_scope:
            return False  # B1 disallows spontaneous outreach to group members
        if (not goal_id or not source_event_id or not isinstance(evidence, str)
                or not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 100):
            return False
        if not isinstance(now, (float, int)) or not math.isfinite(now):
            raise ValueError("invalid time")
        excerpt = " ".join(evidence.split())[:220]
        if not excerpt:
            return False
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                recent = db.execute(
                    """SELECT COUNT(*) AS n, MAX(created_at) AS latest
                       FROM initiative_drafts
                       WHERE owner_scope=? AND created_at>? AND created_at<=?""",
                    (owner_scope, now - 86400, now),
                ).fetchone()
                gap_ok = (recent["latest"] is None or
                          now - recent["latest"] >= grant.min_gap_seconds)
                if recent["n"] >= grant.daily_limit or not gap_ok:
                    db.execute("COMMIT")
                    return False
                cursor = db.execute(
                    """INSERT OR IGNORE INTO initiative_drafts
                       (goal_id, owner_scope, source_event_id, created_at, reason,
                        topic, evidence_excerpt) VALUES (?,?,?,?,?,?,?)""",
                    (goal_id, owner_scope, source_event_id, now,
                     "completed_authorized_goal", topic.strip(), excerpt),
                )
                db.execute("COMMIT")
                return cursor.rowcount == 1
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def list_drafts(self, owner_scope: str) -> tuple[dict, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM initiative_drafts
                   WHERE owner_scope=? AND status='draft'
                   ORDER BY created_at DESC LIMIT 20""", (owner_scope,),
            ).fetchall()
            return tuple(dict(row) for row in rows)

    def dismiss(self, goal_id: str, owner_scope: str) -> bool:
        with self._connect() as db:
            return db.execute(
                """UPDATE initiative_drafts SET status='dismissed'
                   WHERE goal_id=? AND owner_scope=? AND status='draft'""",
                (goal_id, owner_scope),
            ).rowcount == 1


    def record_review(self, *, goal_id: str, owner_scope: str, action: str,
                      confidence: float, proposed_text: str, reviewed_at: float,
                      reason: str) -> bool:
        """Durable once-only review; never implies permission to send."""
        if action not in {"ask", "share", "defer", "silence"} or not 0 <= confidence <= 1:
            raise ValueError("invalid review action")
        if not goal_id or not owner_scope or len(proposed_text) > 220:
            raise ValueError("invalid review target/text")
        with self._connect() as db:
            result = db.execute(
                """INSERT OR IGNORE INTO initiative_reviews
                   (goal_id,owner_scope,action,confidence,proposed_text,reviewed_at,reason)
                   SELECT goal_id,owner_scope,?,?,?,?,?
                   FROM initiative_drafts
                   WHERE goal_id=? AND owner_scope=? AND status='draft'""",
                (action, confidence, proposed_text, reviewed_at,
                 reason[:120], goal_id, owner_scope),
            )
            return result.rowcount == 1

    def get_review(self, goal_id: str, owner_scope: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM initiative_reviews WHERE goal_id=? AND owner_scope=?""",
                (goal_id, owner_scope),
            ).fetchone()
            return dict(row) if row else None

    def actionable_reviews(self, owner_scope: str, since: float) -> tuple[dict, ...]:
        """Only current, non-dismissed B1 results may be offered for delivery."""
        with self._connect() as db:
            rows = db.execute(
                """SELECT r.*, d.source_event_id, d.topic, d.evidence_excerpt
                   FROM initiative_reviews AS r JOIN initiative_drafts AS d
                     ON d.goal_id=r.goal_id AND d.owner_scope=r.owner_scope
                   WHERE r.owner_scope=? AND r.action IN ('ask','share')
                     AND d.status='draft' AND d.reason='completed_authorized_goal'
                     AND d.created_at>=? ORDER BY r.reviewed_at LIMIT 20""",
                (owner_scope, since),
            ).fetchall()
            return tuple(dict(row) for row in rows)
