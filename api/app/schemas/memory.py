"""Response models of the ``/memory`` read API. The graph shapes are the engine's own read models
(``engine/memory/model.py``), so the API and the explorer see exactly what the graph returns;
``valid_to`` is already ``None`` for a current version there (rulings R25)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from engine.memory.model import (
    MemoryHit,
    NodeDetail,
    RevisionDiff,
    RevisionOut,
    Subgraph,
)

__all__ = [
    "CurationIn",
    "CurationOut",
    "MemoryHit",
    "NodeDetail",
    "ReingestOut",
    "RevisionDiff",
    "RevisionOut",
    "RevisionsOut",
    "SearchOut",
    "StatusOut",
    "Subgraph",
]


class StatusOut(BaseModel):
    configured: bool
    available: bool
    revision: int | None = None
    at: datetime | None = None
    phase: str | None = None
    counts: dict[str, int] = {}
    pending_ingests: int = 0
    embedding_backend: str | None = None


class SearchOut(BaseModel):
    hits: list[MemoryHit]


class RevisionsOut(BaseModel):
    revisions: list[RevisionOut]


class CurationIn(BaseModel):
    action: Literal["note", "reject_mapping", "correct_mapping", "obsolete"]
    target_uid: str = Field(max_length=512)
    reason: str = Field(min_length=1, max_length=2000)
    text: str | None = Field(None, max_length=4000)  # required for ``note``
    replacement_uid: str | None = Field(None, max_length=512)  # required for ``correct_mapping``


class CurationOut(BaseModel):
    event_id: str
    job_id: str


class ReingestOut(BaseModel):
    job_ids: list[str]
