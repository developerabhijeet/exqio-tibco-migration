"""API models for iterations (sub-project 6): opening one (re-entering Design), reading it, ending it."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from api.app.services.edits import FileChange
from api.app.services.failures import FailureItem
from api.app.services.iterations import MAX_REQUIREMENTS

MAX_NOTE = 2000


class IterationOpen(BaseModel):
    """``include``: keys of the preview's items to keep (``null`` keeps them all, ``[]`` none)."""

    model_config = ConfigDict(extra="forbid")

    requirements: str | None = Field(default=None, max_length=MAX_REQUIREMENTS)
    include: list[str] | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=MAX_NOTE)


class IterationNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=MAX_NOTE)


class IterationPreview(BaseModel):
    items: list[FailureItem]


class IterationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    n: int
    process_ids: list[str]
    kind: str
    status: str
    resume_phase: str
    requirements: str | None
    baseline_build_id: str | None
    source: dict[str, Any] | None
    opened_by: str
    opened_at: datetime
    closed_at: datetime | None
    note: str | None
    job_id: str | None = None  # the design job an opened iteration queued


class IterationInputOut(BaseModel):
    """An input without its text (read it at ``/iterations/{id}/inputs/{input_id}``)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    process_id: str | None
    ref: str | None
    title: str
    sha256: str | None
    created_at: datetime


class TimelineEntry(BaseModel):
    """A design record, a build or a gate decision of the iteration, oldest first."""

    kind: str  # design | build | gate
    at: datetime | None
    process_id: str | None
    id: str
    iteration: int | None = None
    summary: str
    decisions: list[str] = []  # a design record's decision titles (read-only view of an earlier iteration)


class IterationDetail(IterationOut):
    inputs: list[IterationInputOut]
    timeline: list[TimelineEntry]


class InputText(BaseModel):
    text: str


class EditSetOut(BaseModel):
    """An uploaded edit set as previewed: what changed against the baseline build and where the
    iteration resumes (``POST /edit-sets/{id}/resume``)."""

    id: str
    iteration: int
    iteration_id: str
    baseline_build_id: str | None
    resume_phase: str
    status: str
    files: list[FileChange]
    counts: dict[str, int]


class EditDiff(BaseModel):
    baseline: str
    edited: str


class EditResumed(BaseModel):
    """The job the resume queued: a ``build`` (with ``build_id``) or a ``design``."""

    job_id: str
    kind: str
    edit_set_id: str
    build_id: str | None = None
