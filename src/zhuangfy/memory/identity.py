"""Stable person IDs, verified channel identities, and explicit memory consent.

This is a trusted application-internal API, not a user-facing authentication
endpoint. Verification proofs must be issued by a trusted, authenticated
channel adapter or explicitly confirmed administrator; an untrusted QQ message
or a matching nickname NEVER qualifies as account-ownership proof.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from uuid import uuid4

from .database import MemoryDatabase


PROOF_METHODS = frozenset({"authenticated_channel", "challenge_response", "trusted_admin"})
CONSENT_PURPOSES = frozenset({"timeline", "curation"})


def _nonempty(value: str, name: str, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be a nonempty string of at most {maximum} characters")
    return value.strip()


@dataclass(frozen=True)
class Person:
    person_id: str
    display_name: str
    created_at: int


@dataclass(frozen=True)
class ChannelIdentity:
    """An account claim verified outside this repository layer."""

    platform: str
    channel_account_id: str
    external_user_id: str


@dataclass(frozen=True)
class IdentityProof:
    """An auditable proof reference, NOT the secret challenge or token itself."""

    method: str
    reference: str


class IdentityRepository:
    def __init__(self, database: MemoryDatabase):
        self.database = database
        with database.write() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS persons (
                    person_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS identities (
                    platform TEXT NOT NULL,
                    channel_account_id TEXT NOT NULL,
                    external_user_id TEXT NOT NULL,
                    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
                    proof_method TEXT NOT NULL,
                    proof_reference TEXT NOT NULL,
                    verified_at INTEGER NOT NULL,
                    PRIMARY KEY (platform, channel_account_id, external_user_id)
                );
                CREATE INDEX IF NOT EXISTS identities_person ON identities(person_id);
                CREATE TABLE IF NOT EXISTS memory_consents (
                    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
                    purpose TEXT NOT NULL CHECK(purpose IN ('timeline','curation')),
                    granted_at INTEGER,
                    revoked_at INTEGER,
                    PRIMARY KEY (person_id, purpose)
                );
            """)

    def create_person(self, display_name: str = "") -> Person:
        if not isinstance(display_name, str) or len(display_name) > 160:
            raise ValueError("display_name must have at most 160 characters")
        person = Person(str(uuid4()), display_name.strip(), int(time.time()))
        with self.database.write() as db:
            db.execute("INSERT INTO persons(person_id,display_name,created_at) VALUES(?,?,?)",
                       (person.person_id, person.display_name, person.created_at))
        return person

    def ensure_qq_person(self, identity: ChannelIdentity, display_name: str, *,
                         initial_timeline: bool = False) -> tuple[Person, bool]:
        """Provision an empty person only for a trusted inbound QQ private identity.

        A caller must have authenticated the QQ transport and admitted the
        message. For a NEW identity only, an explicit trusted-host capture
        policy may grant local Timeline retention, but never curation.
        This never re-enables revoked consent or merges by nickname.
        """
        if type(initial_timeline) is not bool:
            raise ValueError("initial_timeline must be boolean")
        if identity.platform != "qq":
            raise ValueError("Automatic onboarding is restricted to QQ")
        bot_id = _nonempty(identity.channel_account_id, "channel_account_id")
        user_id = _nonempty(identity.external_user_id, "external_user_id")
        if (not bot_id.isascii() or not user_id.isascii()
                or not bot_id.isdecimal() or not user_id.isdecimal()
                or int(bot_id) <= 0 or int(user_id) <= 0
                or str(int(bot_id)) != bot_id or str(int(user_id)) != user_id
                or bot_id == user_id):
            raise ValueError("Invalid QQ channel identity")
        if not isinstance(display_name, str):
            raise ValueError("display_name must be a string")
        nickname = "".join(ch for ch in display_name if ch.isprintable()).strip()[:80]
        nickname = nickname or f"QQ用户{user_id}"
        with self.database.write() as db:
            existing = db.execute("""
                SELECT p.person_id, p.display_name, p.created_at
                FROM identities i JOIN persons p ON p.person_id = i.person_id
                WHERE i.platform=? AND i.channel_account_id=? AND i.external_user_id=?
            """, ("qq", bot_id, user_id)).fetchone()
            if existing is not None:
                # Preserve any administrator-chosen or previously verified name.
                return Person(**dict(existing)), False
            person = Person(str(uuid4()), nickname, int(time.time()))
            db.execute("INSERT INTO persons(person_id,display_name,created_at) VALUES(?,?,?)",
                       (person.person_id, person.display_name, person.created_at))
            db.execute("""INSERT INTO identities
                (platform,channel_account_id,external_user_id,person_id,
                 proof_method,proof_reference,verified_at)
                VALUES(?,?,?,?,?,?,?)""",
                ("qq", bot_id, user_id, person.person_id, "authenticated_channel",
                 "trusted_qq_private_message", int(time.time())))
            if initial_timeline:
                db.execute("""INSERT INTO memory_consents
                    (person_id,purpose,granted_at,revoked_at) VALUES(?,?,?,NULL)""",
                    (person.person_id, "timeline", int(time.time())))
        return person, True

    def get_person(self, person_id: str) -> Person | None:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM persons WHERE person_id=?", (person_id,)).fetchone()
        return Person(**dict(row)) if row else None

    def resolve(self, identity: ChannelIdentity) -> str | None:
        platform = _nonempty(identity.platform, "platform", 64)
        account = _nonempty(identity.channel_account_id, "channel_account_id")
        user_id = _nonempty(identity.external_user_id, "external_user_id")
        with self.database.connection() as db:
            row = db.execute("""SELECT person_id FROM identities
                WHERE platform=? AND channel_account_id=? AND external_user_id=?""",
                             (platform, account, user_id)).fetchone()
            return str(row["person_id"]) if row else None

    def link_verified(self, person_id: str, identity: ChannelIdentity, proof: IdentityProof) -> None:
        """Requires a proof issued/checked by a trusted caller.

        Never accept proof metadata directly from an unauthenticated user.
        An existing identity cannot be silently reassigned to another person.
        """
        platform = _nonempty(identity.platform, "platform", 64)
        account = _nonempty(identity.channel_account_id, "channel_account_id")
        user_id = _nonempty(identity.external_user_id, "external_user_id")
        if proof.method not in PROOF_METHODS:
            raise ValueError("Unrecognized identity verification method")
        reference = _nonempty(proof.reference, "proof_reference")
        with self.database.write() as db:
            if db.execute("SELECT 1 FROM persons WHERE person_id=?", (person_id,)).fetchone() is None:
                raise ValueError("Unknown person_id")
            existing = db.execute("""SELECT person_id FROM identities
                WHERE platform=? AND channel_account_id=? AND external_user_id=?""",
                (platform, account, user_id)).fetchone()
            if existing is not None:
                if existing["person_id"] != person_id:
                    raise ValueError("Verified identity is already linked to another person")
                return  # Idempotent: preserve the original verification audit.
            db.execute("""INSERT INTO identities
                (platform,channel_account_id,external_user_id,person_id,
                 proof_method,proof_reference,verified_at)
                VALUES(?,?,?,?,?,?,?)""",
                (platform, account, user_id, person_id, proof.method, reference, int(time.time())))

    def set_consent(self, person_id: str, purpose: str, granted: bool) -> None:
        """Trusted UI only. Revocation stops new capture/curation, not existing retention.

        For erasure call TimelineRepository.erase_private_history and the future
        memory erasure workflow explicitly; consent alone is not deletion.
        """
        if purpose not in CONSENT_PURPOSES:
            raise ValueError("Unknown consent purpose")
        with self.database.write() as db:
            if db.execute("SELECT 1 FROM persons WHERE person_id=?", (person_id,)).fetchone() is None:
                raise ValueError("Unknown person_id")
            now = int(time.time())
            db.execute("""INSERT INTO memory_consents(person_id,purpose,granted_at,revoked_at)
                VALUES(?,?,?,?)
                ON CONFLICT(person_id,purpose) DO UPDATE SET
                  granted_at=excluded.granted_at,revoked_at=excluded.revoked_at""",
                (person_id, purpose, now if granted else None, None if granted else now))

    def has_consent(self, person_id: str, purpose: str) -> bool:
        if purpose not in CONSENT_PURPOSES:
            raise ValueError("Unknown consent purpose")
        with self.database.connection() as db:
            row = db.execute("""SELECT 1 FROM memory_consents
                WHERE person_id=? AND purpose=? AND granted_at IS NOT NULL AND revoked_at IS NULL""",
                (person_id, purpose)).fetchone()
        return bool(row)

    def require_consent(self, person_id: str, purpose: str) -> None:
        if not self.has_consent(person_id, purpose):
            raise PermissionError(f"person_id lacks explicit {purpose} consent")
