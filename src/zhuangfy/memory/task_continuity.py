"""Low-cost, source-labelled task continuity recall for verified QQ private chats.

Do not infer task completion or create a goal from similarity. Recalled entries
are *possible* related history, never a confirmed source/task link. Curated
memory remains under its independent permission and vector-index checks.
"""
from __future__ import annotations

import re

from .database import MemoryDatabase
from .identity import ChannelIdentity, IdentityRepository
from .task_links import TaskMemoryLinks
from .timeline import TimelineRepository
from .retrieval import HybridMemoryRetriever

# Only use meaningful local overlap, not generic conversational cues.
FILLER = ("之前", "那个", "这个", "我们", "你们", "你", "我", "一下",
          "方宜", "最近", "今天", "明天", "以后", "现在", "继续",
          "计划", "项目", "任务", "关于", "问题", "记得", "还有", "一下")
CHINESE = re.compile(r"[\u4e00-\u9fff]{2,}")
TECHNICAL = re.compile(r"[a-zA-Z][a-zA-Z0-9_+#.-]{1,}")
MAX_REVIEW = 120
MAX_QUERY_CHARS = 500


def _features(value: str) -> set[str]:
    """Conservative Unicode character ngrams and explicit technical terms."""
    text = value.lower()
    for filler in FILLER:
        text = text.replace(filler, " ")
    terms = {m.group() for m in TECHNICAL.finditer(text)
             if len(m.group()) >= 3}
    # Split on punctuation before forming Chinese ngrams, avoiding false joins.
    for matched in CHINESE.finditer(text):
        phrase = matched.group()
        terms.update(phrase[i:i+3] for i in range(len(phrase) - 2))
    return terms


def _score(query_features: set[str], candidate: str) -> float:
    possible = _features(candidate)
    overlap = query_features & possible
    if not overlap:
        return 0.0
    # Short single technical tokens can be distinctive (e.g., "OSG", "Qt6").
    technical = any(TECHNICAL.fullmatch(s) for s in overlap)
    if len(overlap) < 2 and not technical:
        return 0.0
    return round(len(overlap) / max(len(query_features), 1), 4)


class TaskContinuity:
    def __init__(self, db: MemoryDatabase, identities: IdentityRepository,
                 timeline: TimelineRepository, links: TaskMemoryLinks,
                 retriever: HybridMemoryRetriever) -> None:
        self.db = db
        self.identities = identities
        self.timeline = timeline
        self.links = links
        self.retriever = retriever

    def recall(self, *, agent_id: str, identity: ChannelIdentity,
               query: str, limit: int = 2, exclude_entry_id: str = "") -> dict:
        """Return bounded candidate task histories, never new confirmed links.

        No matching model run and no mutation. Authorization and ownership
        checks occur before and after possible embedding calls.
        """
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= MAX_QUERY_CHARS:
            raise ValueError("Query must be 1..500 characters")
        if not isinstance(exclude_entry_id, str) or len(exclude_entry_id) > 128:
            raise ValueError("Invalid excluded entry ID")
        if type(limit) is not int or not 1 <= limit <= 3:
            raise ValueError("Task recall limit must be 1..3")
        scope = self.links.owner_scope(identity)
        features = _features(query)
        with self.db.connection() as db:
            owner = db.execute("""
                SELECT person_id FROM identities
                WHERE platform=? AND channel_account_id=? AND external_user_id=?
            """, (identity.platform, identity.channel_account_id,
                  identity.external_user_id)).fetchone()
            if owner is None:
                raise PermissionError("Unknown channel identity")
            person_id = owner["person_id"]
            self.timeline._require_timeline_consent(db, person_id)
            rows = db.execute("""
                SELECT t.task_id,t.origin_entry_id,e.content,e.recorded_at
                FROM task_memory_tasks AS t
                JOIN timeline_entries AS e ON e.entry_id=t.origin_entry_id
                JOIN private_conversations AS c ON c.conversation_id=e.conversation_id
                WHERE t.person_id=? AND t.agent_id=? AND t.owner_scope=?
                  AND e.speaker_role='user' AND e.state='received'
                  AND c.person_id=? AND c.platform='qq'
                  AND c.channel_account_id=? AND c.external_conversation_id=?
                ORDER BY t.created_at DESC,t.task_id DESC LIMIT ?
            """, (person_id, agent_id, scope, person_id,
                  identity.channel_account_id,
                  "private:" + identity.external_user_id, MAX_REVIEW)).fetchall()
            candidates = []
            if features:
                for row in rows:
                    relevance = _score(features, row["content"])
                    if relevance:
                        candidates.append((relevance, dict(row)))
            candidates.sort(key=lambda v: (-v[0], -v[1]["recorded_at"], v[1]["task_id"]))
            selected = candidates[:limit]
            # Query a bounded window, not the whole private transcript.
            timeline_rows = db.execute("""
                SELECT e.entry_id,e.content,e.recorded_at
                FROM timeline_entries AS e
                JOIN private_conversations AS c ON c.conversation_id=e.conversation_id
                WHERE c.person_id=? AND c.agent_id=?
                  AND c.platform='qq' AND c.channel_account_id=?
                  AND c.external_conversation_id=?
                  AND e.speaker_role='user' AND e.state='received'
                ORDER BY e.sequence DESC LIMIT 200
            """, (person_id, agent_id, identity.channel_account_id,
                  "private:" + identity.external_user_id)).fetchall() if selected else []

        # Existing Curator permissions are deliberately independent of Timeline.
        curated = []
        if selected and self.identities.has_consent(person_id, "curation"):
            hits = self.retriever.search(
                agent_id=agent_id, person_id=person_id, query=query, limit=3)
            curated = [
                {"memory_id": hit.memory.memory_id,
                 "content": hit.memory.content[:300],
                 "event_state": hit.memory.event_state,
                 "source_entry_ids": list(hit.memory.source_entry_ids),
                 "relation": "semantic_candidate"}
                for hit in hits
            ]

        result = []
        for relevance, origin in selected:
            origin_features = _features(origin["content"])
            historical = []
            for item in timeline_rows:
                if item["entry_id"] == origin["origin_entry_id"]:
                    continue
                # A contextual match must overlap the task topic, not just a
                # generic phrase in the latest message.
                score = _score(origin_features, item["content"])
                if score >= 0.2:
                    historical.append((score, item))
            historical.sort(key=lambda pair: (-pair[0], -pair[1]["recorded_at"],
                                              pair[1]["entry_id"]))
            history = [
                {"entry_id": item["entry_id"],
                 "recorded_at": item["recorded_at"],
                 "excerpt": item["content"][:240],
                 "relation": "possible_related"}
                for _, item in historical[:3]
            ]
            result.append({
                "task_id": origin["task_id"],
                "origin": {"entry_id": origin["origin_entry_id"],
                           "recorded_at": origin["recorded_at"],
                           "excerpt": origin["content"][:260],
                           "relation": "verified_origin"},
                "match_score": relevance,
                "history": history,
                "curated_candidates": curated[:2],
                "task_status": "unverified",  # GoalStore resides in another DB.
            })

        # Re-check after embedding (a different thread may have revoked).
        self.identities.require_consent(person_id, "timeline")
        if curated:
            self.identities.require_consent(person_id, "curation")
        return {"tasks": result}
