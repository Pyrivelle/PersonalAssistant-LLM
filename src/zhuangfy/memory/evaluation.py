"""M5 deterministic evaluation for person-scoped retrieval.

These are test metrics, not unverified marketing percentages. Every query is
executed under its intended identity; forbidden IDs flag privacy failures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .retrieval import HybridMemoryRetriever


@dataclass(frozen=True)
class MemoryEvalCase:
    person_id: str
    query: str
    expected_memory_ids: frozenset[str]
    forbidden_memory_ids: frozenset[str] = frozenset()
    include_outdated: bool = False


@dataclass(frozen=True)
class MemoryEvalReport:
    cases: int
    hit_at_k: float
    recall_at_k: float
    leakage_count: int
    stale_count: int


def evaluate_memory_retrieval(
    retriever: HybridMemoryRetriever,
    *, agent_id: str, cases: Iterable[MemoryEvalCase], k: int = 5,
) -> MemoryEvalReport:
    if not 1 <= k <= 20:
        raise ValueError("k must be 1..20")
    cases = list(cases)
    if not cases:
        raise ValueError("Evaluation set cannot be empty")
    hits_count = 0
    recall_sum = 0.0
    leaked = stale = 0
    for case in cases:
        results = retriever.search(agent_id=agent_id, person_id=case.person_id,
                                   query=case.query, limit=k,
                                   include_outdated=case.include_outdated)
        ids = {item.memory.memory_id for item in results}
        expected = case.expected_memory_ids
        if ids & expected:
            hits_count += 1
        recall_sum += len(ids & expected) / len(expected) if expected else float(not ids)
        leaked += len(ids & case.forbidden_memory_ids)
        leaked += sum(item.memory.person_id != case.person_id for item in results)
        stale += sum(item.memory.status != "active" or
                     (item.memory.event_state == "outdated" and not case.include_outdated)
                     for item in results)
    return MemoryEvalReport(
        cases=len(cases), hit_at_k=hits_count / len(cases),
        recall_at_k=recall_sum / len(cases),
        leakage_count=leaked, stale_count=stale,
    )
