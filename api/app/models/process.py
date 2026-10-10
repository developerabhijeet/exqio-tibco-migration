"""Processes: the neutral IR of one workflow kept as JSONB, with its spec hash and complexity."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, CreatedMixin, IdMixin, fk


class Process(IdMixin, CreatedMixin, Base):
    __tablename__ = "processes"
    __table_args__ = (UniqueConstraint("project_id", "ir_id"),)

    project_id: Mapped[str] = mapped_column(fk("projects.id"), index=True)
    application_id: Mapped[str] = mapped_column(fk("applications.id"), index=True)
    upload_id: Mapped[str] = mapped_column(fk("uploads.id"))
    domain_id: Mapped[str | None] = mapped_column(fk("domains.id", ondelete="SET NULL"), index=True)
    ir_id: Mapped[str]  # engine Process.id, unique per project
    name: Mapped[str]
    module: Mapped[str] = mapped_column(default="")
    package: Mapped[str] = mapped_column(default="")
    dialect: Mapped[str]
    source_path: Mapped[str]  # posix path relative to the upload's scan root
    starter_kind: Mapped[str | None]
    ir: Mapped[dict[str, Any]] = mapped_column(JSONB)
    spec_hash: Mapped[str]
    complexity: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    complexity_score: Mapped[float | None]
    complexity_band: Mapped[str | None]
    migration_status: Mapped[str] = mapped_column(default="not_started")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
