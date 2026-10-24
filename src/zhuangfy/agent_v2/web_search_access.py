"""QQ per-principal visibility gate for an externally billed Web Search Skill.

Installation and enabling are NOT authorization to spend API requests for
every chat participant. The host grants only exact verified bot:user pairs.
This remains separate from future Mijia physical-device permissions.
"""
from __future__ import annotations

import re

from .runtime import AuthorizedToolRegistry
from .skill_access import SkillPrincipal, SkillAccessPolicy, SkillGrant

WEB_TOOL = "web_search.search"
WEB_RESOURCE = "public_search_api"
_PAIR = re.compile(r"([1-9]\d{0,19}):([1-9]\d{0,19})\Z")


def restrict_web_search_for_qq(
    registry: AuthorizedToolRegistry, *,
    principal: SkillPrincipal,
    allowed_pairs: str,
) -> AuthorizedToolRegistry:
    """Return a private-turn registry with Web Search removed unless granted.

    A model cannot provide principal or grants. The trusted QQ adapter calls
    this after deriving bot/user from the authenticated inbound event. If the
    administrator config is missing or malformed, fail closed.
    """
    if WEB_TOOL not in registry.public_names(scope="private", allow_mutation=True):
        return registry
    if not isinstance(principal, SkillPrincipal):
        raise TypeError("Verified SkillPrincipal required")
    grants = []
    if isinstance(allowed_pairs, str) and len(allowed_pairs) <= 8192:
        for item in allowed_pairs.split(","):
            item = item.strip()
            found = _PAIR.fullmatch(item)
            if not found:
                continue
            bot, user = found.groups()
            grants.append(SkillGrant(
                principal_key=f"qq:{bot}:private:{user}",
                capability=WEB_TOOL,
                resource_id=WEB_RESOURCE,
            ))
    allowed = SkillAccessPolicy(tuple(grants)).allows(
        principal, WEB_TOOL, resource_id=WEB_RESOURCE)
    if allowed and principal.scope == "private" and principal.platform == "qq":
        return registry

    result = AuthorizedToolRegistry()
    safe_bindings = [
        binding for binding in registry.discovery_bindings(
            scope="private", allow_mutation=True)
        if binding.name != WEB_TOOL
    ]
    if safe_bindings:
        result.register_many(safe_bindings)
    return result
