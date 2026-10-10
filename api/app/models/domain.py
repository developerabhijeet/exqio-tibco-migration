"""Discovered domains and the estate graph (tab 2, Discover)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, CreatedMixin, IdMixin, fk


class Domain(IdMixin, CreatedMixin, Base):
    __tablename__ = "domains"
    __table_args__ = (UniqueConstraint("project_id", "key"),)

    project_id: Mapped[str] = mapped_column(fk("projects.id"), index=True)
    key: Mapped[str]  # engine DomainCandidate.id
    suggested_name: Mapped[str]
    name: Mapped[str]
    renamed_by: Mapped[str | None]
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    process_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)  # IR process ids
    merged_into: Mapped[str | None]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class GraphNode(Base):
    __tablename__ = "graph_nodes"
    __table_args__ = (UniqueConstraint("project_id", "node_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    project_id: Mapped[str] = mapped_column(fk("projects.id"), index=True)
    node_id: Mapped[str]  # engine Node.id, e.g. process:<id>, table:<name>
    kind: Mapped[str]
    name: Mapped[str]
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class GraphEdge(Base):
    __tablename__ = "graph_edges"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    project_id: Mapped[str] = mapped_column(fk("projects.id"), index=True)
    src: Mapped[str]
    dst: Mapped[str]
    kind: Mapped[str]
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
