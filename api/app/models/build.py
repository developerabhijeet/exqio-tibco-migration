"""Builds of the generated .NET solution, their files, test results and metrics (tab 5, Build)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, CreatedMixin, IdMixin, fk


class Build(IdMixin, CreatedMixin, Base):
    __tablename__ = "builds"

    process_id: Mapped[str] = mapped_column(fk("processes.id"), index=True)
    design_id: Mapped[str | None] = mapped_column(fk("design_records.id", ondelete="SET NULL"))
    job_id: Mapped[str | None]
    # must be one of engine.infra_gen.generator's runtimes; "container_apps" (plural) is rejected
    runtime: Mapped[str] = mapped_column(default="container_app")
    status: Mapped[str] = mapped_column(default="queued")  # queued|running|passed|failed
    spec_hash: Mapped[str | None]
    design_hash: Mapped[str | None]
    build_hash: Mapped[str | None]
    output_dir: Mapped[str | None]
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)  # [{name, ok, log}]
    repair: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reviews: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    run_port: Mapped[int | None]
    run_pid: Mapped[int | None]
    infra_validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # engine InfraValidation
    builder: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {filled: [path], rejections: [...]}
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BuildFile(IdMixin, Base):
    __tablename__ = "build_files"
    __table_args__ = (UniqueConstraint("build_id", "path"),)

    build_id: Mapped[str] = mapped_column(fk("builds.id"), index=True)
    path: Mapped[str]
    origin: Mapped[str]  # code|agent|ai|infra (agent: a body left for a model; ai: filled by one)
    process_id: Mapped[str | None]  # IR process id of a per-process file; None for shared files
    tibco_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    sha256: Mapped[str]
    size: Mapped[int] = mapped_column(BigInteger, default=0)


class TestResult(IdMixin, Base):
    __tablename__ = "test_results"
    __test__ = False  # not a pytest class

    build_id: Mapped[str] = mapped_column(fk("builds.id"), index=True)
    name: Mapped[str]  # <namespace>.<class>.<method>, as TRX reports it
    display_name: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str | None]  # rule|branch|scenario|other
    rule_key: Mapped[str | None]
    ref: Mapped[str | None]  # branch transition id or scenario id
    file: Mapped[str | None]
    outcome: Mapped[str]  # passed|failed|skipped|not_run
    duration_ms: Mapped[float | None]
    message: Mapped[str | None] = mapped_column(Text)
    has_body: Mapped[bool] = mapped_column(default=True)


class Metric(IdMixin, Base):
    __tablename__ = "metrics"
    __table_args__ = (UniqueConstraint("build_id", "name"),)

    build_id: Mapped[str] = mapped_column(fk("builds.id"), index=True)
    name: Mapped[str]
    value: Mapped[float | None]
    unit: Mapped[str | None]
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
