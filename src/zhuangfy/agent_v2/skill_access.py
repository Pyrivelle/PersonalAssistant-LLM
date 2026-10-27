"""Trusted turn-scoped identity and fail-closed permission boundary for Skills.

This is *not* a login service. Only a trusted channel adapter may construct the
principal from an authenticated event or local installation; model tool arguments never select a
user, account, Person ID, or device owner.

Mutating physical-world tools are intentionally denied until a separate
approval/token and auditable execution mechanism exists.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class SkillPrincipal:
    platform: str
    channel_account_id: str
    external_user_id: str
    scope: str

    @classmethod
    def verified_qq_private(cls, *, bot_id: int, user_id: int) -> "SkillPrincipal":
        if (type(bot_id) is not int or type(user_id) is not int
                or bot_id <= 0 or user_id <= 0):
            raise ValueError("A trusted positive QQ Bot ID and sender ID are required")
        return cls("qq", str(bot_id), str(user_id), "private")

    @classmethod
    def local_desktop(cls, *, installation_id: str) -> "SkillPrincipal":
        if (not isinstance(installation_id, str) or
                not re.fullmatch(r"[0-9a-f]{32}", installation_id)):
            raise ValueError("A persisted local installation ID is required")
        return cls("desktop", "local", installation_id, "private")

    @property
    def key(self) -> str:
        return (self.platform + ":" + self.channel_account_id +
                ":" + self.scope + ":" + self.external_user_id)


@dataclass(frozen=True, slots=True)
class SkillGrant:
    principal_key: str
    capability: str
    resource_id: str
    expires_at: int | None = None
    # Mutation and real device control must not be silently enabled by adding a
    # Skill. A separate authenticated approval handshake is not implemented.
    allow_mutation: bool = False


class SkillAccessPolicy:
    """Exact-principal, exact-capability, exact-resource allowlist.

    Future Mijia credentials and device ownership must be checked by a
    separate connector; a model-provided device name is never proof of access.
    """

    def __init__(self, grants: tuple[SkillGrant, ...] = ()) -> None:
        if not isinstance(grants, tuple) or any(
                not isinstance(g, SkillGrant) for g in grants):
            raise ValueError("Invalid grants")
        self._grants = grants

    def allows(self, principal: SkillPrincipal, capability: str, *,
               resource_id: str, mutating: bool = False,
               now: int | None = None) -> bool:
        if (not isinstance(principal, SkillPrincipal)
                or not isinstance(capability, str) or not isinstance(resource_id, str)):
            return False
        if not re.fullmatch(r"[a-z][a-z0-9_.]{1,100}", capability):
            return False
        # Physical mutation is deliberately unsupported for now. Merely
        # having a grant, regardless of its allow_mutation flag, is not an
        # authenticated per-operation confirmation.
        if mutating:
            return False
        from time import time
        current = int(time()) if now is None else now
        return any(
            grant.principal_key == principal.key and
            grant.capability == capability and
            grant.resource_id == resource_id and
            (grant.expires_at is None or current < grant.expires_at)
            for grant in self._grants
        )
