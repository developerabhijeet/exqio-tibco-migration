"""Infrastructure assets, Azure connections and plans (tab 7, Deploy)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, CreatedMixin, IdMixin, fk


class InfraAsset(IdMixin, CreatedMixin, Base):
    __tablename__ = "infra_assets"
    __table_args__ = (UniqueConstraint("build_id", "path"),)

    build_id: Mapped[str] = mapped_column(fk("builds.id"), index=True)
    path: Mapped[str]
    kind: Mapped[str]  # terraform|terragrunt|dockerfile|pipeline|other
    sha256: Mapped[str]
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # InfraValidation rows


class AzureConnection(IdMixin, CreatedMixin, Base):
    """Connect Azure form; the service principal secret is Fernet-encrypted with APP_SECRET."""

    __tablename__ = "azure_connections"

    project_id: Mapped[str] = mapped_column(fk("projects.id"), unique=True)
    tenant: Mapped[str]
    subscription: Mapped[str]
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # non-secret fields
    secret_enc: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class Plan(IdMixin, CreatedMixin, Base):
    __tablename__ = "plans"

    build_id: Mapped[str] = mapped_column(fk("builds.id"), index=True)
    job_id: Mapped[str | None]
    connected: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str]  # connect_later|acknowledged|running|planned|failed
    plan_hash: Mapped[str | None]
    output: Mapped[str | None] = mapped_column(Text)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {units: [{unit, add, change, destroy}], ...}
    note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None]  # the operations user who acknowledged connect-later
