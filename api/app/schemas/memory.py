"""Response models of the ``/memory`` read API. The graph shapes are the engine's own read models
(``engine/memory/model.py``), so the API and the explorer see exactly what the graph returns;
``valid_to`` is already ``None`` for a current version there (rulings R25)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from engine.memory.model import (
    MemoryHit,
    NodeDetail,
    RevisionDiff,
    RevisionOut,
    Subgraph,
)

__all__ = ["MemoryHit", "NodeDetail", "RevisionDiff", "RevisionOut", "RevisionsOut", "SearchOut",
           "StatusOut", "Subgraph"]


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
