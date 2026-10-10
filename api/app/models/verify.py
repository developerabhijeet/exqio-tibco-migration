"""Parity verification: scenarios, scenario runs and field-level differences (tab 6, Verify)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, CreatedMixin, IdMixin, fk


class Scenario(IdMixin, CreatedMixin, Base):
    __tablename__ = "scenarios"
    __table_args__ = (UniqueConstraint("process_id", "key"),)

    process_id: Mapped[str] = mapped_column(fk("processes.id"), index=True)
    key: Mapped[str]  # engine Scenario.id
    name: Mapped[str]
    origin: Mapped[str] = mapped_column(default="generated")  # generated|sme
    created_by: Mapped[str | None]
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)  # engine Scenario (input, stubs, seed)


class ScenarioRun(IdMixin, CreatedMixin, Base):
    __tablename__ = "scenario_runs"

    build_id: Mapped[str] = mapped_column(fk("builds.id"), index=True)
    scenario_id: Mapped[str] = mapped_column(fk("scenarios.id"), index=True)
    job_id: Mapped[str | None]
    status: Mapped[str]  # match|differs|error
    expected: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    actual: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # engine ParityReport
    parity: Mapped[float | None]
    report_hash: Mapped[str | None]


class FieldDiff(IdMixin, Base):
    __tablename__ = "field_diffs"

    run_id: Mapped[str] = mapped_column(fk("scenario_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(default="field", server_default="field")  # field|side_effect
    field: Mapped[str]
    expected: Mapped[Any] = mapped_column(JSONB, nullable=True)
    actual: Mapped[Any] = mapped_column(JSONB, nullable=True)
    classification: Mapped[str]  # match|intended|defect|noise
    finding_key: Mapped[str | None]
    note: Mapped[str | None] = mapped_column(Text)
