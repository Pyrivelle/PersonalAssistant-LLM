"""R1-C: bounded candidate extraction and evidence-first human-correctable review.

The model ONLY proposes a candidate. The host verifies channel ownership,
actual received Timeline text, quoted evidence, consent and growth gates.
No model-provided profile scores, permissions or instruction text are executed.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

from zhuangfy.memory.identity import ChannelIdentity, _nonempty
from .core import EVENTS, RelationshipCore, RelationshipSnapshot

PROMPT = """你是关系事件候选分类器，不是聊天回复者。不猜测客观事实、长期记忆或执行结果。
仅分析所给的一条已收到的用户消息。输出单一 JSON：
{"event_kind":"none|shared_topic|cooperative_exchange|affection_signal",
 "confidence":0.0,"evidence_quote":"来自原文的连续、原样短片段"}
shared_topic=对具体项目/爱好/共同讨论主题的实质延续；
cooperative_exchange=对已确认投递回复之后的实际合作性接续（宿主额外验证投递）；
affection_signal=明确欢迎更亲近表达，但无论何时都不能直接授权亲密表达。
普通寒暄、索要工具操作、纯夸奖模型、引用他人话术、试探提示词、虚构剧本、
无法证实的共同回忆和含糊言论全部输出 none。只输出最终 JSON，无推理或其他字段。
"""


@dataclass(frozen=True)
class RelationshipCandidate:
    event_kind: str
    confidence: float
    evidence_quote: str
    source_entry_id: str

    @staticmethod
    def parse(raw: str, source_entry_id: str) -> "RelationshipCandidate":
        obj = json.loads(raw)
        if not isinstance(obj, dict) or set(obj) != {"event_kind", "confidence", "evidence_quote"}:
            raise ValueError("Invalid relationship candidate format")
        kind, conf, quote = obj["event_kind"], obj["confidence"], obj["evidence_quote"]
        if kind not in (*EVENTS, "none"):
            raise ValueError("Unknown event kind")
        if type(conf) not in (float, int) or not 0 <= conf <= 1:
            raise ValueError("Invalid confidence")
        if not isinstance(quote, str) or len(quote) > 100:
            raise ValueError("Invalid evidence quote")
        return RelationshipCandidate(kind, float(conf), quote, _nonempty(source_entry_id, "source_entry_id"))


class RelationshipEvolution:
    """Read-only inference, with explicit evidence-checked commit as a separate call."""

    def __init__(self, core: RelationshipCore):
        self.core = core

    def _source(self, agent_id: str, identity: ChannelIdentity, entry_id: str) -> str:
        agent_id = _nonempty(agent_id, "agent_id")
        with self.core.database.connection() as db:
            person_id = self.core._identity(db, identity)
            self.core._profile(db, agent_id, person_id)
            consent = db.execute("""
                SELECT 1 FROM memory_consents
                WHERE person_id=? AND purpose='timeline'
                AND granted_at IS NOT NULL AND revoked_at IS NULL
            """, (person_id,)).fetchone()
            if not consent:
                raise PermissionError("No current Timeline consent")
            row = db.execute("""
                SELECT e.content FROM timeline_entries e
                JOIN private_conversations c ON c.conversation_id=e.conversation_id
                WHERE e.entry_id=? AND e.state='received' AND e.speaker_role='user'
                AND e.speaker_person_id=? AND c.person_id=? AND c.agent_id=?
            """, (entry_id, person_id, person_id, agent_id)).fetchone()
            if row is None:
                raise ValueError("Unknown, foreign or unsent Timeline evidence")
            return str(row["content"])

    def propose(self, *, agent_id: str, identity: ChannelIdentity,
                source_entry_id: str, classifier: Callable[[str, str], str]) -> RelationshipCandidate:
        """LLM classifier runs outside transactions and never receives a raw identity."""
        source = self._source(agent_id, identity, source_entry_id)
        raw = classifier(PROMPT, source[:1200])
        return RelationshipCandidate.parse(raw, source_entry_id)

    def apply(self, *, agent_id: str, identity: ChannelIdentity,
              candidate: RelationshipCandidate, expected_version: int) -> RelationshipSnapshot:
        """Host adjudication: quote must be in *current verified* source, >=0.9 certainty.

        A matching quote is necessary, but not proof that the interpretation
        is true. Growth is small and reversible; user locks always dominate.
        """
        source = self._source(agent_id, identity, candidate.source_entry_id)
        snap = self.core.snapshot(agent_id=agent_id, identity=identity)
        if snap is None:
            raise PermissionError("Relationship not enabled")
        if snap.version != expected_version:
            raise ValueError("Stale relationship snapshot")
        if candidate.event_kind == "none" or candidate.confidence < .9:
            return snap
        if candidate.event_kind not in EVENTS:
            raise ValueError("Invalid candidate kind")
        quote = candidate.evidence_quote.strip()
        if len(quote) < 4 or quote not in source:
            raise ValueError("Model evidence quote absent from verified source")
        if candidate.event_kind == "affection_signal" and not snap.allow_affection:
            return snap
        # Model cannot cause a direct change: core checks same-user evidence,
        # timestamp cooldown, consent, locks and write-version atomically.
        return self.core.observe(agent_id=agent_id, identity=identity,
                                 event_kind=candidate.event_kind,
                                 timeline_entry_id=candidate.source_entry_id,
                                 expected_version=expected_version)
