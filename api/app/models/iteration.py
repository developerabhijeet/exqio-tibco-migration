"""Iterations of a project (sub-project 6): a numbered pass back through the phases for one or more
processes, its inputs (failures, uploaded logs), the edit sets uploaded against a build, and the
GitHub repositories connected as a source. An iteration is never deleted."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, CreatedMixin, IdMixin, fk


class Iteration(IdMixin, Base):
    __tablename__ = "iterations"
    __table_args__ = (UniqueConstraint("project_id", "n"),)

    project_id: Mapped[str] = mapped_column(fk("projects.id"), index=True)
    n: Mapped[int]  # 1, 2, 3 ... per project
    process_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)  # the processes re-entered
    kind: Mapped[str]  # initial|reentry|edits|repo
    status: Mapped[str] = mapped_column(default="open")  # services.iterations.ITERATION_STATUSES
    resume_phase: Mapped[str] = mapped_column(default="design")  # design|build|infra
    requirements: Mapped[str | None] = mapped_column(Text)  # masked, <= 20 000 characters
    baseline_build_id: Mapped[str | None] = mapped_column(fk("builds.id", ondelete="SET NULL"))
    source: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {upload_id} | {repo_id, branch, commit}
    opened_by: Mapped[str]
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)


class IterationInput(IdMixin, CreatedMixin, Base):
    __tablename__ = "iteration_inputs"

    iteration_id: Mapped[str] = mapped_column(fk("iterations.id"), index=True)
    kind: Mapped[str]  # build_failure|test_failure|parity_defect|plan_error|uploaded_log
    process_id: Mapped[str | None]  # no foreign key: inputs outlive rescans, like jobs
    ref: Mapped[str | None]  # build id, scenario run id, plan id, or file name
    title: Mapped[str]  # <= 200 characters
    text: Mapped[str] = mapped_column(Text, default="")  # masked excerpt, <= 8 000 characters
    sha256: Mapped[str | None]  # uploaded logs


class EditSet(IdMixin, CreatedMixin, Base):
    """Hand edits found in an uploaded zip (or a repository branch) against ``baseline_build_id``."""

    __tablename__ = "edit_sets"

    iteration_id: Mapped[str] = mapped_column(fk("iterations.id"), index=True)
    baseline_build_id: Mapped[str | None] = mapped_column(fk("builds.id", ondelete="SET NULL"))
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)  # [{path, change, origin, sha256}]
    root_dir: Mapped[str]  # the extracted tree
    created_by: Mapped[str]
    status: Mapped[str] = mapped_column(default="previewed")  # previewed|resumed


class ProjectRepo(IdMixin, Base):
    """A GitHub repository connected to a project as a source (an estate or an exported build)."""

    __tablename__ = "project_repos"

    project_id: Mapped[str] = mapped_column(fk("projects.id"), index=True)
    owner: Mapped[str]
    name: Mapped[str]
    default_branch: Mapped[str]
    purpose: Mapped[str]  # estate|build
    process_id: Mapped[str | None]  # for purpose "build"
    last_commit: Mapped[str | None]
    connected_by: Mapped[str]
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
