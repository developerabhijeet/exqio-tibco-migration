"""Jobs, their streamed events, and every model call (cost and routing history)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, CreatedMixin, IdMixin, fk


class Job(IdMixin, CreatedMixin, Base):
    """No foreign keys on project/process: job history outlives rescans."""

    __tablename__ = "jobs"

    kind: Mapped[str]  # scan|discover|analyse|eval|design|generate|build|test|infra|verify|plan
    status: Mapped[str] = mapped_column(default="queued", index=True)  # queued|running|done|failed
    stage: Mapped[str | None]
    project_id: Mapped[str | None] = mapped_column(index=True)
    process_id: Mapped[str | None] = mapped_column(index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (UniqueConstraint("job_id", "seq"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[str] = mapped_column(fk("jobs.id"))
    seq: Mapped[int]
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event: Mapped[str]  # log|stage|done|error
    tag: Mapped[str | None]  # code|ai
    text: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class ModelCall(IdMixin, CreatedMixin, Base):
    __tablename__ = "model_calls"

    job_id: Mapped[str | None] = mapped_column(fk("jobs.id", ondelete="SET NULL"), index=True)
    project_id: Mapped[str | None] = mapped_column(index=True)
    process_id: Mapped[str | None]
    stage: Mapped[str | None]  # analyst|risk|challenger|designer|builder|reviewer|namer|ask|eval
    provider: Mapped[str]
    model: Mapped[str]
    tier: Mapped[str | None]
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    tokens_cached: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[float | None]
    latency_ms: Mapped[float | None]
    ok: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str | None] = mapped_column(Text)
    fallback_from: Mapped[str | None]
    trace_id: Mapped[str | None]
