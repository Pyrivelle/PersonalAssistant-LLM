"""R1-B opt-in, evidence-linked relationship state for one verified person and agent.

This trusted internal API does not accept model-generated instructions as commands.
It never sends messages, grants tools, derives identity from names, or asserts
that a Timeline entry describes an objectively true event.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
import time
from uuid import uuid4

from zhuangfy.memory.database import MemoryDatabase
from zhuangfy.memory.identity import ChannelIdentity, IdentityRepository, _nonempty

TIERS = ("L0", "L1", "L2", "L3", "L4")
SEEDS = {
    "L0": (10, 10, 0),
    "L1": (35, 30, 15),
    "L2": (65, 65, 35),
    "L3": (85, 80, 80),
    "L4": (95, 90, 95),
}
# These are conservative design heuristics, NOT claims of psychological accuracy.
EVENTS = {
    "shared_topic": ("familiarity", 2, "received", 86400),
    "cooperative_exchange": ("trust", 2, "received", 604800),
    "affection_signal": ("closeness", 1, "received", 604800),
}
TONE_OPTIONS = ("natural", "gentle", "direct")


@dataclass(frozen=True)
class RelationshipSnapshot:
    agent_id: str
    person_id: str
    version: int
    familiarity: int
    trust: int
    closeness: int
    tier: str
    tier_ceiling: str
    allow_affection: bool
    avoid_nicknames: bool
    tone: str
    auto_growth: bool
    frozen: bool
    lock_familiarity: bool
    lock_trust: bool
    lock_closeness: bool

    def prompt_hint(self) -> str:
        """Bounded host-authored advice; never grants tool/send/identity rights."""
        expression = ("可以更自然地表达亲近、想念与偏爱，但尊重对方当下的意愿和边界"
                      if self.tier == "L4" else
                      "可以在用户欢迎的范围内温柔表达" if self.allow_affection
                      else "不预设暧昧关系或使用亲密称呼")
        nickname = "避免亲昵称呼" if self.avoid_nicknames else "称呼保持自然"
        return (
            f"【关系快照 v{self.version}，可信宿主建议】关系参考层级：{self.tier}。"
            f"{expression}；{nickname}；语气选项：{self.tone}。"
            "这些只是表达约束，不代表真实共同经历，也不授予工具、发信或读取隐私的权限。"
            "所有事实仍需要独立证据。"
        )


@dataclass(frozen=True)
class RelationshipEvent:
    event_id: str
    kind: str
    source_entry_id: str | None
    dimension: str
    old_value: int | None
    new_value: int | None
    occurred_at: int
    detail: str


def _number(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 100:
        raise ValueError(f"{name} must be an integer in 0..100")
    return value


def _tier(row: sqlite3.Row) -> str:
    f, t, c = row["familiarity"], row["trust"], row["closeness"]
    if (row["l4_opt_in"] and row["allow_affection"] and row["tier_ceiling"] == "L3"
            and f >= 90 and t >= 85 and c >= 90):
        return "L4"
    if f >= 75 and t >= 65 and c >= 75 and row["allow_affection"]:
        rank = 3
    elif f >= 55 and t >= 60:
        rank = 2
    elif f >= 25:
        rank = 1
    else:
        rank = 0
    return TIERS[min(rank, TIERS.index(row["tier_ceiling"]))]


class RelationshipCore:
    """A trusted service boundary. Caller must authenticate channel/UI actions."""

    def __init__(self, database: MemoryDatabase, identities: IdentityRepository):
        self.database = database
        self.identities = identities
        with self.database.write() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS relationship_profiles (
                    agent_id TEXT NOT NULL,
                    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
                    version INTEGER NOT NULL DEFAULT 1,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                    auto_growth INTEGER NOT NULL DEFAULT 1 CHECK(auto_growth IN (0,1)),
                    frozen INTEGER NOT NULL DEFAULT 0 CHECK(frozen IN (0,1)),
                    lock_familiarity INTEGER NOT NULL DEFAULT 0 CHECK(lock_familiarity IN (0,1)),
                    lock_trust INTEGER NOT NULL DEFAULT 0 CHECK(lock_trust IN (0,1)),
                    lock_closeness INTEGER NOT NULL DEFAULT 0 CHECK(lock_closeness IN (0,1)),
                    familiarity INTEGER NOT NULL CHECK(familiarity BETWEEN 0 AND 100),
                    trust INTEGER NOT NULL CHECK(trust BETWEEN 0 AND 100),
                    closeness INTEGER NOT NULL CHECK(closeness BETWEEN 0 AND 100),
                    tier_ceiling TEXT NOT NULL CHECK(tier_ceiling IN ('L0','L1','L2','L3')),
                    l4_opt_in INTEGER NOT NULL DEFAULT 0 CHECK(l4_opt_in IN (0,1)),
                    allow_affection INTEGER NOT NULL DEFAULT 0 CHECK(allow_affection IN (0,1)),
                    avoid_nicknames INTEGER NOT NULL DEFAULT 1 CHECK(avoid_nicknames IN (0,1)),
                    tone TEXT NOT NULL DEFAULT 'natural'
                        CHECK(tone IN ('natural','gentle','direct')),
                    PRIMARY KEY(agent_id, person_id)
                );
                CREATE TABLE IF NOT EXISTS relationship_events (
                    event_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    person_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source_entry_id TEXT,
                    dimension TEXT NOT NULL,
                    old_value INTEGER,
                    new_value INTEGER,
                    detail TEXT NOT NULL,
                    occurred_at INTEGER NOT NULL,
                    FOREIGN KEY(agent_id,person_id)
                        REFERENCES relationship_profiles(agent_id,person_id) ON DELETE CASCADE
                );
                CREATE UNIQUE INDEX IF NOT EXISTS relationship_event_source
                    ON relationship_events(agent_id,person_id,kind,source_entry_id);
                CREATE INDEX IF NOT EXISTS relationship_event_recent
                    ON relationship_events(agent_id,person_id,kind,occurred_at);
            """)
            # SQLite cannot extend the existing tier_ceiling CHECK in place.
            # This additive column keeps old profiles and event foreign keys intact.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(relationship_profiles)")}
            if "l4_opt_in" not in columns:
                db.execute("ALTER TABLE relationship_profiles ADD COLUMN "
                           "l4_opt_in INTEGER NOT NULL DEFAULT 0 CHECK(l4_opt_in IN (0,1))")

    def _identity(self, db: sqlite3.Connection, identity: ChannelIdentity) -> str:
        # Authentication is performed by the trusted channel adapter; identity
        # lookup here is independent of nicknames and verified across platforms.
        record = db.execute("""
            SELECT person_id FROM identities
            WHERE platform=? AND channel_account_id=? AND external_user_id=?
        """, (identity.platform, identity.channel_account_id, identity.external_user_id)).fetchone()
        if record is None:
            raise PermissionError("Unverified channel identity")
        return str(record["person_id"])

    @staticmethod
    def _profile(db: sqlite3.Connection, agent_id: str, person_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM relationship_profiles WHERE agent_id=? AND person_id=?",
                         (agent_id, person_id)).fetchone()
        if row is None:
            raise ValueError("Relationship not enrolled")
        return row

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> RelationshipSnapshot:
        return RelationshipSnapshot(
            agent_id=row["agent_id"], person_id=row["person_id"],
            version=row["version"], familiarity=row["familiarity"],
            trust=row["trust"], closeness=row["closeness"], tier=_tier(row),
            tier_ceiling="L4" if row["l4_opt_in"] else row["tier_ceiling"],
            allow_affection=bool(row["allow_affection"]),
            avoid_nicknames=bool(row["avoid_nicknames"]), tone=row["tone"],
            auto_growth=bool(row["auto_growth"]), frozen=bool(row["frozen"]),
            lock_familiarity=bool(row["lock_familiarity"]),
            lock_trust=bool(row["lock_trust"]),
            lock_closeness=bool(row["lock_closeness"]),
        )

    @staticmethod
    def _audit(db: sqlite3.Connection, agent_id: str, person_id: str, kind: str,
               dimension: str, old: int | None, new: int | None,
               source_entry_id: str | None, detail: str, now: int) -> None:
        db.execute("""
            INSERT INTO relationship_events
            (event_id,agent_id,person_id,kind,source_entry_id,dimension,
             old_value,new_value,detail,occurred_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (str(uuid4()), agent_id, person_id, kind, source_entry_id, dimension,
              old, new, detail, now))

    def enroll(self, *, agent_id: str, identity: ChannelIdentity, initial_tier: str = "L0",
               allow_affection: bool = False) -> RelationshipSnapshot:
        """Explicit opt-in by an authenticated UI/adapter; no implicit creation."""
        agent_id = _nonempty(agent_id, "agent_id")
        if initial_tier not in SEEDS or type(allow_affection) is not bool:
            raise ValueError("Invalid relationship seed")
        if initial_tier in {"L3", "L4"} and not allow_affection:
            raise ValueError("An L3/L4 starting point requires explicit affection consent")
        with self.database.write() as db:
            person_id = self._identity(db, identity)
            existing = db.execute("SELECT 1 FROM relationship_profiles WHERE agent_id=? AND person_id=?",
                                  (agent_id, person_id)).fetchone()
            if existing:
                raise ValueError("Relationship already enrolled; use manual correction")
            f, t, c = SEEDS[initial_tier]
            db.execute("""
                INSERT INTO relationship_profiles
                    (agent_id,person_id,familiarity,trust,closeness,
                     tier_ceiling,l4_opt_in,allow_affection)
                VALUES(?,?,?,?,?,?,?,?)
            """, (agent_id, person_id, f, t, c, "L3",
                  int(initial_tier == "L4"), int(allow_affection)))
            self._audit(db, agent_id, person_id, "enroll", "profile", None, None,
                        None, "trusted_initial_" + initial_tier, int(time.time()))
            return self._snapshot(self._profile(db, agent_id, person_id))

    def snapshot(self, *, agent_id: str, identity: ChannelIdentity) -> RelationshipSnapshot | None:
        agent_id = _nonempty(agent_id, "agent_id")
        with self.database.connection() as db:
            person_id = self._identity(db, identity)
            row = db.execute("SELECT * FROM relationship_profiles WHERE agent_id=? AND person_id=?",
                             (agent_id, person_id)).fetchone()
            return self._snapshot(row) if row and row["enabled"] else None

    def correct(self, *, agent_id: str, identity: ChannelIdentity, expected_version: int,
                familiarity: int | None = None, trust: int | None = None,
                closeness: int | None = None, tier_ceiling: str | None = None,
                allow_affection: bool | None = None, avoid_nicknames: bool | None = None,
                tone: str | None = None, auto_growth: bool | None = None,
                frozen: bool | None = None, enabled: bool | None = None,
                lock_familiarity: bool | None = None,
                lock_trust: bool | None = None,
                lock_closeness: bool | None = None) -> RelationshipSnapshot:
        """Manual correction from trusted UI; optimistic lock defeats stale updates."""
        agent_id = _nonempty(agent_id, "agent_id")
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("Invalid expected version")
        changes = {}
        for name, value in (("familiarity", familiarity), ("trust", trust), ("closeness", closeness)):
            if value is not None:
                changes[name] = _number(value, name)
        for name, value in (("allow_affection", allow_affection),
                            ("avoid_nicknames", avoid_nicknames),
                            ("auto_growth", auto_growth), ("frozen", frozen),
                            ("enabled", enabled),
                            ("lock_familiarity", lock_familiarity),
                            ("lock_trust", lock_trust),
                            ("lock_closeness", lock_closeness)):
            if value is not None:
                if type(value) is not bool:
                    raise ValueError(f"{name} must be bool")
                changes[name] = int(value)
        if tier_ceiling is not None:
            if tier_ceiling not in TIERS:
                raise ValueError("Invalid tier ceiling")
            changes["tier_ceiling"] = "L3" if tier_ceiling == "L4" else tier_ceiling
            changes["l4_opt_in"] = int(tier_ceiling == "L4")
        if tone is not None:
            if tone not in TONE_OPTIONS:
                raise ValueError("Invalid preferred tone")
            changes["tone"] = tone
        if not changes:
            raise ValueError("No correction supplied")
        with self.database.write() as db:
            person_id = self._identity(db, identity)
            row = self._profile(db, agent_id, person_id)
            if row["version"] != expected_version:
                raise ValueError("Stale relationship snapshot")
            meaningful = {k: v for k, v in changes.items() if row[k] != v}
            if not meaningful:
                return self._snapshot(row)
            # No automatic L4 promotion; only a trusted correction/enrollment opts in.
            # Manual score corrections alone cannot bypass this gate.
            for k, v in meaningful.items():
                db.execute(f"UPDATE relationship_profiles SET {k}=? WHERE agent_id=? AND person_id=?",
                           (v, agent_id, person_id))
            db.execute("UPDATE relationship_profiles SET version=version+1 WHERE agent_id=? AND person_id=?",
                       (agent_id, person_id))
            details = json.dumps({name: {"before": row[name], "after": value}
                                  for name, value in meaningful.items()}, ensure_ascii=False,
                                 sort_keys=True)
            self._audit(db, agent_id, person_id, "manual_correction", "policy", None, None,
                        None, details[:1400], int(time.time()))
            return self._snapshot(self._profile(db, agent_id, person_id))

    def observe(self, *, agent_id: str, identity: ChannelIdentity, event_kind: str,
                timeline_entry_id: str, expected_version: int) -> RelationshipSnapshot:
        """Apply a bounded heuristic backed by an actual consented Timeline entry.

        Event classification remains a fallible inference; this method verifies
        provenance, not the semantic truth of the user's statement. It must be
        called by a trusted event-validation worker, not directly by model text.
        """
        agent_id = _nonempty(agent_id, "agent_id")
        entry = _nonempty(timeline_entry_id, "timeline_entry_id")
        if event_kind not in EVENTS:
            raise ValueError("Unknown evidence event kind")
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("Invalid expected version")
        dimension, delta, required_state, cooldown = EVENTS[event_kind]
        now = int(time.time())
        with self.database.write() as db:
            person_id = self._identity(db, identity)
            row = self._profile(db, agent_id, person_id)
            if row["version"] != expected_version:
                raise ValueError("Stale relationship snapshot")
            if not row["enabled"] or not row["auto_growth"] or row["frozen"]:
                return self._snapshot(row)
            # Requiring Timeline consent also blocks re-growth after /forget_me
            # or permission withdrawal. Check inside the SAME transaction.
            consent = db.execute("""
                SELECT 1 FROM memory_consents
                WHERE person_id=? AND purpose='timeline'
                  AND granted_at IS NOT NULL AND revoked_at IS NULL
            """, (person_id,)).fetchone()
            if consent is None:
                raise PermissionError("Timeline consent is not active")
            source = db.execute("""
                SELECT e.entry_id,e.sequence,e.conversation_id FROM timeline_entries e
                JOIN private_conversations c ON c.conversation_id=e.conversation_id
                WHERE e.entry_id=? AND c.agent_id=? AND c.person_id=?
                  AND e.state=?
                  AND ((e.speaker_role='user' AND e.speaker_person_id=? AND ?='received')
                    OR (e.speaker_role='assistant' AND ?='delivered'))
            """, (entry, agent_id, person_id, required_state, person_id,
                  required_state, required_state)).fetchone()
            if source is None:
                raise ValueError("Evidence is missing or belongs to another person/agent")
            if event_kind == "cooperative_exchange":
                # An outbound delivery alone does not establish the user trusts us.
                # The user must have engaged AFTER a delivered reply.
                engaged = db.execute("""
                    SELECT 1 FROM timeline_entries
                    WHERE conversation_id=? AND speaker_role='assistant'
                      AND state='delivered' AND sequence<? LIMIT 1
                """, (source["conversation_id"], source["sequence"])).fetchone()
                if engaged is None:
                    raise ValueError("Trust signal needs user engagement after delivered reply")
            duplicate = db.execute("""
                SELECT 1 FROM relationship_events
                WHERE agent_id=? AND person_id=? AND kind=? AND source_entry_id=?
            """, (agent_id, person_id, event_kind, entry)).fetchone()
            if duplicate:
                return self._snapshot(row)
            recent = db.execute("""
                SELECT 1 FROM relationship_events
                WHERE agent_id=? AND person_id=? AND kind=?
                  AND occurred_at>? LIMIT 1
            """, (agent_id, person_id, event_kind, now - cooldown)).fetchone()
            if recent:
                return self._snapshot(row)
            if row["lock_" + dimension]:
                return self._snapshot(row)
            if event_kind == "affection_signal" and not row["allow_affection"]:
                return self._snapshot(row)
            before = row[dimension]
            after = min(100, before + delta)
            if after == before:
                return self._snapshot(row)
            db.execute(f"UPDATE relationship_profiles SET {dimension}=?, version=version+1 "
                       "WHERE agent_id=? AND person_id=?", (after, agent_id, person_id))
            self._audit(db, agent_id, person_id, event_kind, dimension, before, after,
                        entry, "verified_timeline_reference", now)
            return self._snapshot(self._profile(db, agent_id, person_id))

    def events(self, *, agent_id: str, identity: ChannelIdentity,
               limit: int = 50) -> tuple[RelationshipEvent, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid event limit")
        agent_id = _nonempty(agent_id, "agent_id")
        with self.database.connection() as db:
            person_id = self._identity(db, identity)
            # Disabled profiles are not exposed through normal read APIs.
            row = db.execute("SELECT enabled FROM relationship_profiles WHERE agent_id=? AND person_id=?",
                             (agent_id, person_id)).fetchone()
            if not row or not row["enabled"]:
                return ()
            items = db.execute("""
                SELECT event_id,kind,source_entry_id,dimension,old_value,new_value,
                       occurred_at,detail FROM relationship_events
                WHERE agent_id=? AND person_id=? ORDER BY occurred_at DESC,event_id DESC LIMIT ?
            """, (agent_id, person_id, limit)).fetchall()
            return tuple(RelationshipEvent(**dict(item)) for item in items)

    def erase(self, *, agent_id: str, identity: ChannelIdentity) -> bool:
        """Explicit erasure of this agent/person's profile AND event references."""
        agent_id = _nonempty(agent_id, "agent_id")
        with self.database.write() as db:
            person_id = self._identity(db, identity)
            deleted = db.execute("DELETE FROM relationship_profiles WHERE agent_id=? AND person_id=?",
                                 (agent_id, person_id)).rowcount
            return bool(deleted)
