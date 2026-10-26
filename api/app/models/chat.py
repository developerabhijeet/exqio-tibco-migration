"""ExiQoian conversations: private to their owner, per project."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from api.app.db import Base, IdMixin, fk


class ChatConversation(IdMixin, Base):
    __tablename__ = "chat_conversations"
    __table_args__ = (Index("ix_chat_conversations_project_id_owner_updated_at", "project_id", "owner", "updated_at"),)

    project_id: Mapped[str] = mapped_column(Text, fk("projects.id"))
    owner: Mapped[str] = mapped_column(Text)  # the principal's actor
    title: Mapped[str] = mapped_column(Text, default="", server_default="")  # first question, masked, 80 chars
    persona: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(IdMixin, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_conversation_id_created_at", "conversation_id", "created_at"),)

    conversation_id: Mapped[str] = mapped_column(Text, fk("chat_conversations.id"))
    role: Mapped[str] = mapped_column(Text)  # user|assistant
    text: Mapped[str] = mapped_column(Text)  # masked; an assistant message holds CitedText.text
    cited: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # CitedText, assistant only
    verdict: Mapped[str | None] = mapped_column(Text)  # answer|intro|off_topic|injection|not_covered|unavailable
    persona: Mapped[str | None] = mapped_column(Text)
    used_model: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sql_text("false"))
    provider: Mapped[str | None] = mapped_column(Text)
    rule: Mapped[str | None] = mapped_column(Text)  # the guard rule that decided, if any
    dropped_sources: Mapped[int] = mapped_column(Integer, default=0, server_default=sql_text("0"))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
