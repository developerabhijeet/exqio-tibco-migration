"""Audit lines for connector tests and publishes: ``action = "connector <kind> <op>"``.

``detail`` holds ids, URLs and counts; never a header, a token or a request body. The keys that
could carry one are refused rather than written."""
from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from api.app.models import AuditLog

FORBIDDEN_DETAIL_KEYS = frozenset({"authorization", "token", "headers", "body", "password", "secret"})


async def audit_connector(session: AsyncSession, actor: str, kind: str, op: str, *,
                          project_id: str | None = None, process_id: str | None = None, target: str = "",
                          outcome: Literal["ok", "failed", "refused"],
                          detail: dict[str, Any] | None = None) -> None:
    extra = dict(detail or {})
    bad = sorted(k for k in extra if k.lower() in FORBIDDEN_DETAIL_KEYS)
    if bad:
        raise ValueError(f"connector audit detail must not carry {', '.join(bad)}")
    session.add(AuditLog(project_id=project_id, process_id=process_id, actor=actor, actor_kind="user",
                         action=f"connector {kind} {op}",
                         detail={"target": target, "outcome": outcome, **extra}))
