"""Consent-aware, append-oriented private conversation Timeline.

Only verified channel identities can produce inbound entries. Assistant output
has separate GENERATED, DELIVERED, and DISCARDED states: curators cannot use an
unsent draft as an actual shared experience. Group chats are intentionally not
enabled until a group-specific visibility/consent policy is designed.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from uuid import uuid4

from .database import MemoryDatabase
from .identity import ChannelIdentity, IdentityRepository, _nonempty


@dataclass(frozen=True)
class PrivateConversation:
    conversation_id: str
    agent_id: str
    person_id: str
    platform: str
    channel_account_id: str
    external_conversation_id: str


@dataclass(frozen=True)
class TimelineEntry:
    sequence: int
    entry_id: str
    conversation_id: str
    speaker_role: str
    speaker_person_id: str | None
    content: str
    state: str
    source_message_id: str
    in_reply_to: str | None
    recorded_at: int


def _entry(row: sqlite3.Row) -> TimelineEntry:
    return TimelineEntry(
        sequence=row["sequence"], entry_id=row["entry_id"],
        conversation_id=row["conversation_id"], speaker_role=row["speaker_role"],
        speaker_person_id=row["speaker_person_id"], content=row["content"],
        state=row["state"], source_message_id=row["source_message_id"],
        in_reply_to=row["in_reply_to"], recorded_at=row["recorded_at"],
    )


class TimelineRepository:
    def __init__(self, database: MemoryDatabase, identities: IdentityRepository):
        self.database = database
        self.identities = identities
        with database.write() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS private_conversations (
                    conversation_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
                    platform TEXT NOT NULL,
                    channel_account_id TEXT NOT NULL,
                    external_conversation_id TEXT NOT NULL,
                    UNIQUE (agent_id, platform, channel_account_id, external_conversation_id)
                );
                CREATE TABLE IF NOT EXISTS timeline_entries (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id TEXT NOT NULL UNIQUE,
                    conversation_id TEXT NOT NULL
                        REFERENCES private_conversations(conversation_id) ON DELETE CASCADE,
                    speaker_role TEXT NOT NULL CHECK(speaker_role IN ('user','assistant')),
                    speaker_person_id TEXT REFERENCES persons(person_id),
                    content TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('received','generated','delivered','discarded')),
                    source_message_id TEXT NOT NULL,
                    in_reply_to TEXT,
                    recorded_at INTEGER NOT NULL,
                    UNIQUE (conversation_id, speaker_role, source_message_id)
                );
                CREATE INDEX IF NOT EXISTS timeline_conversation_sequence
                    ON timeline_entries(conversation_id,sequence);
            """)

    @staticmethod
    def _conversation(row: sqlite3.Row) -> PrivateConversation:
        return PrivateConversation(**dict(row))

    def open_private(self, *, agent_id: str, identity: ChannelIdentity,
                     external_conversation_id: str) -> PrivateConversation:
        """A trusted adapter supplies the verified channel identity and stable chat ID."""
        agent_id = _nonempty(agent_id, "agent_id")
        chat_id = _nonempty(external_conversation_id, "external_conversation_id")
        person_id = self.identities.resolve(identity)
        if person_id is None:
            raise PermissionError("Unknown/unverified channel identity")
        self.identities.require_consent(person_id, "timeline")
        with self.database.write() as db:
            row = db.execute("""SELECT * FROM private_conversations
                WHERE agent_id=? AND platform=? AND channel_account_id=?
                  AND external_conversation_id=?""",
                (agent_id, identity.platform.strip(), identity.channel_account_id.strip(), chat_id)).fetchone()
            if row:
                if row["person_id"] != person_id:
                    raise PermissionError("Conversation identity collision")
                return self._conversation(row)
            conversation = PrivateConversation(
                str(uuid4()), agent_id, person_id, identity.platform.strip(),
                identity.channel_account_id.strip(), chat_id,
            )
            db.execute("""INSERT INTO private_conversations
                (conversation_id,agent_id,person_id,platform,channel_account_id,external_conversation_id)
                VALUES(?,?,?,?,?,?)""",
                (conversation.conversation_id, conversation.agent_id, conversation.person_id,
                 conversation.platform, conversation.channel_account_id, conversation.external_conversation_id))
            return conversation

    def _private(self, db: sqlite3.Connection, conversation_id: str,
                 person_id: str | None = None) -> PrivateConversation:
        row = db.execute("SELECT * FROM private_conversations WHERE conversation_id=?",
                         (conversation_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown conversation")
        if person_id is not None and row["person_id"] != person_id:
            raise PermissionError("Cannot access another person's conversation")
        return self._conversation(row)

    @staticmethod
    def _validate_content(content: str) -> str:
        if not isinstance(content, str) or not content.strip() or len(content) > 10000:
            raise ValueError("Message content must contain 1–10000 characters")
        return content.strip()

    @staticmethod
    def _insert_or_get(db: sqlite3.Connection, *, conversation_id: str, role: str,
                       person_id: str | None, content: str, state: str,
                       message_id: str, in_reply_to: str | None = None) -> TimelineEntry:
        previous = db.execute("""SELECT * FROM timeline_entries
            WHERE conversation_id=? AND speaker_role=? AND source_message_id=?""",
            (conversation_id, role, message_id)).fetchone()
        if previous:
            if (previous["speaker_person_id"] != person_id or previous["content"] != content
                    or previous["in_reply_to"] != in_reply_to):
                raise ValueError("Conflicting replay of the same source message ID")
            return _entry(previous)
        entry_id = str(uuid4())
        db.execute("""INSERT INTO timeline_entries
            (entry_id,conversation_id,speaker_role,speaker_person_id,content,state,
             source_message_id,in_reply_to,recorded_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (entry_id, conversation_id, role, person_id, content, state,
             message_id, in_reply_to, int(time.time())))
        row = db.execute("SELECT * FROM timeline_entries WHERE entry_id=?", (entry_id,)).fetchone()
        return _entry(row)

    def receive(self, *, conversation_id: str, identity: ChannelIdentity,
                source_message_id: str, content: str) -> TimelineEntry:
        """Record authorized input BEFORE model generation, with replay protection."""
        content = self._validate_content(content)
        message_id = _nonempty(source_message_id, "source_message_id")
        person_id = self.identities.resolve(identity)
        if person_id is None:
            raise PermissionError("Unknown channel identity")
        self.identities.require_consent(person_id, "timeline")
        with self.database.write() as db:
            conversation = self._private(db, conversation_id, person_id)
            if (conversation.platform != identity.platform.strip()
                    or conversation.channel_account_id != identity.channel_account_id.strip()):
                raise PermissionError("Channel/account mismatch")
            return self._insert_or_get(
                db, conversation_id=conversation_id, role="user", person_id=person_id,
                content=content, state="received", message_id=message_id,
            )

    def generate_reply(self, *, conversation_id: str, in_reply_to: str,
                       source_message_id: str, content: str) -> TimelineEntry:
        """Store a draft; it is not evidence of a delivered conversation."""
        content = self._validate_content(content)
        message_id = _nonempty(source_message_id, "source_message_id")
        with self.database.write() as db:
            conversation = self._private(db, conversation_id)
            self.identities.require_consent(conversation.person_id, "timeline")
            parent = db.execute("""SELECT * FROM timeline_entries WHERE entry_id=?
                AND conversation_id=? AND speaker_role='user' AND state='received'""",
                (in_reply_to, conversation_id)).fetchone()
            if not parent:
                raise ValueError("Reply must reference a received user message from the same conversation")
            return self._insert_or_get(
                db, conversation_id=conversation_id, role="assistant", person_id=None,
                content=content, state="generated", message_id=message_id, in_reply_to=in_reply_to,
            )

    def set_reply_state(self, *, conversation_id: str, entry_id: str, new_state: str) -> TimelineEntry:
        """Trusted outbound transport calls only after real delivery confirmation."""
        if new_state not in {"delivered", "discarded"}:
            raise ValueError("Only delivered or discarded transitions are supported")
        with self.database.write() as db:
            row = db.execute("""SELECT * FROM timeline_entries
                WHERE conversation_id=? AND entry_id=? AND speaker_role='assistant'""",
                (conversation_id, entry_id)).fetchone()
            if row is None:
                raise ValueError("Unknown assistant entry")
            if row["state"] != "generated" and row["state"] != new_state:
                raise ValueError("Cannot change a finalized reply to a contradictory status")
            self.identities.require_consent(self._private(db, conversation_id).person_id, "timeline")
            db.execute("UPDATE timeline_entries SET state=? WHERE entry_id=?", (new_state, entry_id))
            return _entry(db.execute("SELECT * FROM timeline_entries WHERE entry_id=?", (entry_id,)).fetchone())

    def curated_source_entries(self, *, conversation_id: str, person_id: str,
                               limit: int = 80, after_sequence: int = 0,
                               through_sequence: int | None = None) -> list[TimelineEntry]:
        """Get only real user messages and confirmed assistant deliveries.

        The caller cannot retrieve even its own private history without
        explicit timeline AND curation consent.
        """
        if not 1 <= limit <= 500 or after_sequence < 0:
            raise ValueError("Invalid curation read bounds")
        self.identities.require_consent(person_id, "timeline")
        self.identities.require_consent(person_id, "curation")
        with self.database.connection() as db:
            self._private(db, conversation_id, person_id)
            rows = db.execute("""SELECT * FROM timeline_entries WHERE conversation_id=?
                AND sequence > ? AND (? IS NULL OR sequence <= ?)
                AND ((speaker_role='user' AND state='received')
                     OR (speaker_role='assistant' AND state='delivered'))
                ORDER BY sequence LIMIT ?""",
                (conversation_id, after_sequence, through_sequence, through_sequence, limit)).fetchall()
            return [_entry(row) for row in rows]

    def erase_private_history(self, *, person_id: str) -> int:
        """Delete all private conversations and their timeline entries for a person."""
        with self.database.write() as db:
            count = db.execute("SELECT COUNT(*) FROM private_conversations WHERE person_id=?",
                               (person_id,)).fetchone()[0]
            db.execute("DELETE FROM private_conversations WHERE person_id=?", (person_id,))
            return count
