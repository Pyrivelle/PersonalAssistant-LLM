"""Read-only, local and deterministic capability shortlist for Planner V3.

No model or network calls, no import of disabled skills and no implicit
permission expansion. A candidate is *visible*, not authorized for execution.
"""
from __future__ import annotations
from dataclasses import dataclass
from .runtime import AuthorizedToolRegistry


@dataclass(frozen=True, slots=True)
class SkillSelection:
    names: tuple[str, ...]
    core_names: tuple[str, ...]
    deferred_names: tuple[str, ...]


class SkillDiscovery:
    def __init__(self, registry: AuthorizedToolRegistry, *,
                 max_core: int = 4, max_deferred: int = 3):
        if not 0 <= max_core <= 8 or not 0 <= max_deferred <= 5:
            raise ValueError("Invalid discovery bounds")
        self.registry = registry
        self.max_core = max_core
        self.max_deferred = max_deferred

    def select(self, query: str, *, scope: str,
               allow_mutation: bool = False) -> SkillSelection:
        if not isinstance(query, str):
            raise ValueError("Discovery query must be a string")
        phrase = query.casefold()[:1500]
        available = self.registry.discovery_bindings(
            scope=scope, allow_mutation=allow_mutation)
        core = tuple(binding.name for binding in available
                     if binding.visibility == "core")[:self.max_core]
        ranked: list[tuple[int, str]] = []
        for binding in available:
            if binding.visibility != "deferred":
                continue
            # Explicit descriptor keywords prevent accidental matches from
            # generic words such as '工具', '操作' and vague greetings.
            matches = [term for term in binding.discovery_terms
                       if term.casefold() in phrase]
            # Directly naming the tool is also valid. No arbitrary model-provided
            # tool name may be executed unless it is already registered.
            directly_named = binding.name.casefold() in phrase
            if matches or directly_named:
                score = (100 if directly_named else 0) + sum(
                    min(len(term), 24) for term in matches)
                ranked.append((score, binding.name))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        deferred = tuple(name for _, name in ranked[:self.max_deferred])
        return SkillSelection(core + deferred, core, deferred)
