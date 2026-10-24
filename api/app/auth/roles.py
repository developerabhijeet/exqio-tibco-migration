"""Who is calling, and which of the platform's roles they hold.

Role keys: ``admin`` (the About panel), ``viewer`` (read only) and one approver role per gate
owner, ``GATE_OWNERS`` (mirrors ``web/lib/personas.ts``: the persona that owns a gate there holds
the role that decides it here).

``AUTH_MODE=oidc``: roles come from the Entra token's ``groups`` claim (group object IDs) and
``roles`` claim (app role values), each matched against ``ROLE_GROUPS`` (``{"engineer":
["<group id>", "Exiqo.Engineer"]}``). Entra leaves ``groups`` out of a token whose user is in more
groups than it will list (about 200 in a JWT) and points at Graph instead (``_claim_names``); the
platform does not call Graph, so for such users only the ``roles`` claim counts. Installs where
users exceed the group limit should assign app roles and list their values in ``ROLE_GROUPS``.

``AUTH_MODE=dev``: the persona sent as ``X-User`` holds its own role key, and ``ops.sam`` also
``admin`` (controller Ruling B), so the screens can be exercised one role at a time.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, field_serializer

log = logging.getLogger(__name__)

ROLE_KEYS: frozenset[str] = frozenset({"admin", "viewer", "sme", "architect", "engineer", "qa", "ops"})
GATE_OWNERS: dict[str, str] = {"G1": "sme", "G2": "sme", "G3": "architect", "G4": "engineer",
                               "G5": "qa", "G6": "ops"}
DEV_PERSONAS: dict[str, frozenset[str]] = {
    "sme.vicky": frozenset({"sme"}), "architect.lee": frozenset({"architect"}),
    "engineer.ravi": frozenset({"engineer"}), "qa.priya": frozenset({"qa"}),
    "ops.sam": frozenset({"ops", "admin"}),
}


class Principal(BaseModel):
    """A signed-in human. ``upn`` is what gate rows and audit lines name as the actor."""

    model_config = ConfigDict(frozen=True)

    oid: str
    upn: str
    name: str
    roles: frozenset[str]

    @field_serializer("roles")
    def _sorted(self, roles: frozenset[str]) -> list[str]:
        return sorted(roles)

    @property
    def actor(self) -> str:
        return self.upn or self.oid


def roles_from_groups(groups: Iterable[str], role_groups: Mapping[str, list[str]]) -> frozenset[str]:
    """The role keys whose configured ids include any of ``groups``. Unknown keys are ignored."""
    held = set(groups)
    return frozenset(role for role, ids in role_groups.items()
                     if role in ROLE_KEYS and held.intersection(ids))


def roles_from_claims(claims: Mapping[str, Any], role_groups: Mapping[str, list[str]]) -> frozenset[str]:
    """Role keys from a token's ``groups`` and ``roles`` claims (``groups`` dropped on overage)."""
    values = list(_strings(claims.get("roles")))
    if "groups" in (claims.get("_claim_names") or {}):
        log.warning("Entra group overage for oid %s: the token lists no groups, so only its app "
                    "roles count; assign app roles to users in that many groups", claims.get("oid"))
    else:
        values += _strings(claims.get("groups"))
    return roles_from_groups(values, role_groups)


def _strings(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def principal_from_claims(claims: Mapping[str, Any], role_groups: Mapping[str, list[str]]) -> Principal:
    oid = str(claims.get("oid") or claims.get("sub") or "")
    upn = str(claims.get("preferred_username") or claims.get("upn") or claims.get("email") or oid)
    return Principal(oid=oid, upn=upn, name=str(claims.get("name") or upn),
                     roles=roles_from_claims(claims, role_groups))


def dev_principal(user: str) -> Principal:
    """``AUTH_MODE=dev``: the persona named by ``X-User``, an unverified claim by design."""
    return Principal(oid=user, upn=user, name=user, roles=DEV_PERSONAS.get(user, frozenset()))
