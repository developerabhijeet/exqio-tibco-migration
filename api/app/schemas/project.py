"""API models for projects, uploads, files and the scan report."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: datetime


class UploadPathIn(BaseModel):
    path: str = Field(min_length=1)


class FileEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    path: str
    kind: str
    dialect: str | None = None
    sha256: str
    size: int
    duplicate_of: str | None = None


class UploadOut(BaseModel):
    upload_id: str
    scan_root: str
    files: list[FileEntryOut]


class ScanIn(BaseModel):
    upload_id: str | None = None  # default: the project's latest upload


class ReportOut(BaseModel):
    """engine CompletenessReport plus the scan's synthetic flag and SQL files."""

    upload_id: str
    job_id: str | None
    synthetic: bool
    sql_files: list[str]
    files_found: int
    files_parsed: int
    failures: list[dict[str, Any]]
    unknown_activity_types: list[dict[str, Any]]
    unknown_elements: list[dict[str, Any]]
    explained: list[dict[str, Any]]
    ok: bool
    created_at: datetime
