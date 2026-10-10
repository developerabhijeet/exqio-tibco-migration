"""Gates G1 to G6 per process and the append-only audit log (design spec section 5.2)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, IdMixin, fk


class Gate(IdMixin, Base):
    __tablename__ = "gates"
    __table_args__ = (UniqueConstraint("process_id", "gate"),)

    process_id: Mapped[str] = mapped_column(fk("processes.id"), index=True)
    gate: Mapped[str]  # G1..G6
    state: Mapped[str] = mapped_column(default="pending")  # pending|approved|rejected|needs_review
    spec_hash: Mapped[str | None]
    design_hash: Mapped[str | None]
    build_hash: Mapped[str | None]
    evidence_hash: Mapped[str | None]  # parity report hash (G5) or plan hash (G6)
    decided_by: Mapped[str | None]
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    reset_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class AuditLog(Base):
    """Append only. No foreign keys, so the trail outlives rescans and deletions."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    project_id: Mapped[str | None] = mapped_column(index=True)
    process_id: Mapped[str | None] = mapped_column(index=True)
    actor: Mapped[str]
    actor_kind: Mapped[str] = mapped_column(default="user")  # user|system
    action: Mapped[str]  # e.g. "gate approved", "rule edited", "domain renamed"
    gate: Mapped[str | None]
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
