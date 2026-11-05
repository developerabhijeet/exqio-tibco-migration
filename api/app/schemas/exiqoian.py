"""Request and response models of the ExiQoian chat API."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from engine.citations.model import CitedText
from engine.exiqoian.personas import PersonaKey


class PersonaOut(BaseModel):
    key: PersonaKey
    label: str


class MemoryState(BaseModel):
    configured: bool
    available: bool


class ProfileOut(BaseModel):
    personas: list[PersonaOut]  # the personas the caller holds, in order
    default_persona: PersonaKey
    suggestions: dict[str, list[str]]  # persona key -> three starter questions
    provider: str  # "none" when no model is configured
    memory: MemoryState


class ConversationIn(BaseModel):
    persona: PersonaKey | None = None


class ConversationOut(BaseModel):
    id: str
    title: str
    persona: PersonaKey
    updated_at: datetime


class ConversationsOut(BaseModel):
    conversations: list[ConversationOut]


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    persona: PersonaKey | None = None


class ChatMessageOut(BaseModel):
    id: str
    role: str
    text: str
    cited: CitedText | None = None
    verdict: str | None = None
    persona: PersonaKey | None = None
    used_model: bool = False
    created_at: datetime


class ConversationDetailOut(BaseModel):
    id: str
    title: str
    persona: PersonaKey
    messages: list[ChatMessageOut]
