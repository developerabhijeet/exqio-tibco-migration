"""Editable settings, provider records and provider evals (Settings page)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, CreatedMixin, IdMixin


class Setting(Base):
    """Key/value settings edited in the UI (models per stage, routing bands); never secrets."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[str | None]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class Provider(IdMixin, Base):
    __tablename__ = "providers"

    name: Mapped[str] = mapped_column(unique=True)
    kind: Mapped[str]  # claude|azure|fake
    enabled: Mapped[bool] = mapped_column(default=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # model names; keys stay in env
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class Eval(IdMixin, CreatedMixin, Base):
    __tablename__ = "evals"

    job_id: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="queued")
    process_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    results: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
