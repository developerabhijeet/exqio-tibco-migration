"""Expert rules: a project's TIBCO modernisation guidance (uploaded as CSV, or the bundled GMF set),
injected into the system prompt of the phases each rule's primary agent covers. Not the business
rules extracted per process (``analysis.Rule``)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, CreatedMixin, IdMixin, fk


class ExpertRule(IdMixin, CreatedMixin, Base):
    __tablename__ = "expert_rules"
    __table_args__ = (UniqueConstraint("project_id", "rule_id"),)

    project_id: Mapped[str] = mapped_column(fk("projects.id"), index=True)
    rule_id: Mapped[str]  # TIB-001
    domain: Mapped[str] = mapped_column(default="")
    name: Mapped[str] = mapped_column(Text)
    severity: Mapped[str]  # Critical|High|Medium|Low
    guidance: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text, default="")
    pass_criteria: Mapped[str] = mapped_column(Text, default="")
    primary_agent: Mapped[str] = mapped_column(default="")
    enforcement: Mapped[str] = mapped_column(Text, default="")
    applicability: Mapped[str] = mapped_column(default="")
    version: Mapped[str] = mapped_column(default="")
    rule_status: Mapped[str] = mapped_column(default="")
    owner: Mapped[str] = mapped_column(default="")
    last_reviewed: Mapped[str] = mapped_column(default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    source: Mapped[str] = mapped_column(default="upload")  # upload|default
    uploaded_by: Mapped[str | None]
    #: the last uses, newest last: [{phase, at, ref}] (at most ``services.expert_rules.APPLIED_KEEP``)
    applied: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
