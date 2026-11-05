"""``connector_links``: one row per (connector, object type, process, local item)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.db import utcnow
from api.app.models.connector import OBJECT_TYPES, ConnectorLink

KEY_FIELDS = ("kind", "object_type", "process_id", "local_ref")
UPDATABLE = ("remote_id", "remote_url", "remote_version", "detail")


async def find_link(session: AsyncSession, *, kind: str, object_type: str, process_id: str,
                    local_ref: str) -> ConnectorLink | None:
    return (await session.execute(select(ConnectorLink).where(
        ConnectorLink.kind == kind, ConnectorLink.object_type == object_type,
        ConnectorLink.process_id == process_id, ConnectorLink.local_ref == local_ref))).scalar_one_or_none()


async def upsert_link(session: AsyncSession, **fields: Any) -> ConnectorLink:
    """Insert the link, or update the remote fields of the existing one for the same local item.
    Flushed, not committed: the caller commits with its audit line."""
    if fields.get("object_type") not in OBJECT_TYPES:
        raise ValueError(f"unknown link object type {fields.get('object_type')!r}")
    link = await find_link(session, **{k: fields[k] for k in KEY_FIELDS})
    if link is None:
        link = ConnectorLink(**fields)
        session.add(link)
    else:
        for name in UPDATABLE:
            if name in fields:
                setattr(link, name, fields[name])
        link.stale = bool(fields.get("stale", False))  # a re-published object is live again
        link.updated_at = utcnow()
    await session.flush()
    return link


async def links_for(session: AsyncSession, process_id: str) -> list[ConnectorLink]:
    return list((await session.execute(select(ConnectorLink).where(ConnectorLink.process_id == process_id)
                                       .order_by(ConnectorLink.created_at, ConnectorLink.id))).scalars())
