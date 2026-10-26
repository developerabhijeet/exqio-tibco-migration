"""Remote objects the connectors created for a process (spec section 2): a pushed branch, a pull
request, a Confluence page, a Jira issue, an attached Figma frame. One row per local item, so a
second publish updates the remote object instead of creating another."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, CreatedMixin, IdMixin, fk

OBJECT_TYPES = ("branch", "pull_request", "confluence_page", "jira_issue", "figma_frame")


class ConnectorLink(IdMixin, CreatedMixin, Base):
    __tablename__ = "connector_links"
    __table_args__ = (UniqueConstraint("kind", "object_type", "process_id", "local_ref"),)

    project_id: Mapped[str] = mapped_column(fk("projects.id"), index=True)
    process_id: Mapped[str] = mapped_column(fk("processes.id"), index=True)
    kind: Mapped[str]  # github|atlassian|figma
    object_type: Mapped[str]  # OBJECT_TYPES
    local_ref: Mapped[str]  # build id, finding key, question index, parity diff id, frame node id
    remote_id: Mapped[str]
    remote_url: Mapped[str] = mapped_column(default="")
    remote_version: Mapped[int | None]  # Confluence page version
    # the remote object is gone (a Jira 404): a re-publish skips it unless asked to create again
    stale: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_by: Mapped[str]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
