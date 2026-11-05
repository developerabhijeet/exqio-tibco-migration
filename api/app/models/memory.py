"""Decisions and curations recorded for the migration memory (the Neo4j graph ingests them)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, IdMixin, fk

EVENT_KINDS = ("decision", "curation")


def memory_event_id() -> str:
    return f"mem_{uuid.uuid4().hex[:12]}"


class MemoryEvent(IdMixin, Base):
    """One human decision or curation. ``context`` carries ``reason`` (curations) and what the
    event is about; curations name ``target_uid`` (and ``replacement_uid`` for a corrected mapping)."""

    __tablename__ = "memory_events"
    __table_args__ = (
        CheckConstraint("kind IN ('decision','curation')", name="kind"),
        Index("ix_memory_events_project_id_created_at", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=memory_event_id)
    project_id: Mapped[str] = mapped_column(Text, fk("projects.id"))
    kind: Mapped[str] = mapped_column(Text)  # decision|curation
    action: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    target_uid: Mapped[str | None] = mapped_column(Text)
    replacement_uid: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
