"""Design records per process (tab 4, Design); G3 onwards bind to ``design_hash``."""
from __future__ import annotations

from typing import Any

from sqlalchemy import Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, CreatedMixin, IdMixin, fk


class DesignRecord(IdMixin, CreatedMixin, Base):
    __tablename__ = "design_records"
    __table_args__ = (UniqueConstraint("process_id", "version"),)

    process_id: Mapped[str] = mapped_column(fk("processes.id"), index=True)
    version: Mapped[int] = mapped_column(default=1)
    job_id: Mapped[str | None]
    spec_hash: Mapped[str]
    design_hash: Mapped[str]
    record: Mapped[dict[str, Any]] = mapped_column(JSONB)  # engine DesignRecord
    narrative: Mapped[str | None] = mapped_column(Text)
