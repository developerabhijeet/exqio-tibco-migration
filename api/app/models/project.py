"""Projects, uploads, uploaded files, applications and scan reports (tab 1, Upload)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, CreatedMixin, IdMixin, fk


class Project(IdMixin, CreatedMixin, Base):
    __tablename__ = "projects"

    name: Mapped[str]


class Upload(IdMixin, CreatedMixin, Base):
    """One upload: a zip rebuilt entry by entry, or a copy of a server folder."""

    __tablename__ = "uploads"

    project_id: Mapped[str] = mapped_column(fk("projects.id"), index=True)
    source_kind: Mapped[str]  # zip | path
    source_name: Mapped[str]  # uploaded file name or server path
    root_dir: Mapped[str]  # absolute storage folder: data/uploads/<project>/<upload_id>
    scan_root: Mapped[str] = mapped_column(default="")  # relative folder scanned ("" or the single top folder)
    file_count: Mapped[int] = mapped_column(default=0)
    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0)


class File(IdMixin, Base):
    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("upload_id", "path"),)

    project_id: Mapped[str] = mapped_column(fk("projects.id"), index=True)
    upload_id: Mapped[str] = mapped_column(fk("uploads.id"), index=True)
    path: Mapped[str]  # posix path relative to the scan root
    kind: Mapped[str]
    dialect: Mapped[str | None]
    sha256: Mapped[str]
    size: Mapped[int] = mapped_column(BigInteger)
    duplicate_of: Mapped[str | None]  # set by the scan for packaged copies


class Application(IdMixin, CreatedMixin, Base):
    """A parsed application; ``ir`` is the engine Application without its processes."""

    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("project_id", "app_key"),)

    project_id: Mapped[str] = mapped_column(fk("projects.id"), index=True)
    upload_id: Mapped[str] = mapped_column(fk("uploads.id"))
    app_key: Mapped[str]  # engine Application.id
    name: Mapped[str]
    dialect: Mapped[str]
    modules: Mapped[list[str]] = mapped_column(JSONB, default=list)
    ir: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ScanReport(IdMixin, CreatedMixin, Base):
    """The CompletenessReport of one scan plus the synthetic flag and SQL file list."""

    __tablename__ = "scan_reports"
    __table_args__ = (Index(None, "project_id", "created_at"),)

    project_id: Mapped[str] = mapped_column(fk("projects.id"))
    upload_id: Mapped[str] = mapped_column(fk("uploads.id"))
    job_id: Mapped[str | None]
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)
    ok: Mapped[bool]
    synthetic: Mapped[bool] = mapped_column(default=False)
    sql_files: Mapped[list[str]] = mapped_column(JSONB, default=list)
    files_found: Mapped[int] = mapped_column(default=0)
    files_parsed: Mapped[int] = mapped_column(default=0)
