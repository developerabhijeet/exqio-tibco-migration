"""Analysis results per process: runs, rules, findings, coverage items and challenges (tab 3)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, CreatedMixin, IdMixin, fk


class AnalysisRun(IdMixin, CreatedMixin, Base):
    """One analysis pass: summary, citation verification tally and the spec hash it saw."""

    __tablename__ = "analyses"

    process_id: Mapped[str] = mapped_column(fk("processes.id"), index=True)
    job_id: Mapped[str | None]
    spec_hash: Mapped[str | None]
    provider: Mapped[str | None]
    static_only: Mapped[bool] = mapped_column(default=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    verification: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Rule(IdMixin, CreatedMixin, Base):
    """engine.analysis.models.Rule; citations kept as JSONB (activity_id, kind, text)."""

    __tablename__ = "rules"
    __table_args__ = (UniqueConstraint("process_id", "rule_key"),)

    process_id: Mapped[str] = mapped_column(fk("processes.id"), index=True)
    analysis_id: Mapped[str | None] = mapped_column(fk("analyses.id", ondelete="SET NULL"))
    rule_key: Mapped[str]  # engine Rule.id
    text: Mapped[str] = mapped_column(Text)
    original_text: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    confidence: Mapped[float] = mapped_column(default=0.0)
    status: Mapped[str] = mapped_column(default="draft")  # draft|approved|edited|rejected|unverified
    owner: Mapped[str | None]
    memory_refs: Mapped[list[str]] = mapped_column(JSONB, default=list)
    challenge: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_by: Mapped[str | None]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class Finding(IdMixin, CreatedMixin, Base):
    __tablename__ = "findings"
    __table_args__ = (UniqueConstraint("process_id", "finding_key"),)

    process_id: Mapped[str] = mapped_column(fk("processes.id"), index=True)
    analysis_id: Mapped[str | None] = mapped_column(fk("analyses.id", ondelete="SET NULL"))
    finding_key: Mapped[str]  # engine Finding.id
    severity: Mapped[str]  # high|medium|low
    text: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    source: Mapped[str]  # static|ai
    business_impact: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str | None]  # keep|fix|ask
    note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None]
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CoverageItem(IdMixin, Base):
    __tablename__ = "coverage_items"

    process_id: Mapped[str] = mapped_column(fk("processes.id"), index=True)
    analysis_id: Mapped[str | None] = mapped_column(fk("analyses.id", ondelete="CASCADE"))
    kind: Mapped[str]  # activity|transition|mapping
    ref: Mapped[str]
    covered_by: Mapped[list[str]] = mapped_column(JSONB, default=list)
    technical_reason: Mapped[str | None] = mapped_column(Text)


class Challenge(IdMixin, CreatedMixin, Base):
    """Independent Challenger verdict per rule; disagreements are flagged, never auto-resolved."""

    __tablename__ = "challenges"

    process_id: Mapped[str] = mapped_column(fk("processes.id"), index=True)
    rule_id: Mapped[str | None] = mapped_column(fk("rules.id", ondelete="CASCADE"))
    verdict: Mapped[str]  # agree|disagree
    reason: Mapped[str] = mapped_column(Text)
    model: Mapped[str]
