"""Analysis models shared by the agents, the verifier and the API (design spec section 5).

Created by Task 9 so the agents can be built first; Task 8 extends this file
with ``VerificationResult``, ``CoverageItem`` and ``CoverageResult``.
"""
from typing import Literal

from pydantic import BaseModel, Field

CitationKind = Literal["sql", "expression", "condition", "config", "schema"]


class Citation(BaseModel):
    activity_id: str
    kind: CitationKind
    text: str


class Challenge(BaseModel):
    verdict: Literal["agree", "disagree"]
    reason: str
    model: str


class Rule(BaseModel):
    id: str
    text: str
    citations: list[Citation]
    confidence: float
    status: Literal["draft", "approved", "edited", "rejected", "unverified"] = "draft"
    challenge: Challenge | None = None
    memory_refs: list[str] = Field(default_factory=list)
    owner: str | None = None


class Finding(BaseModel):
    id: str
    severity: Literal["high", "medium", "low"]
    text: str
    citations: list[Citation]
    source: Literal["static", "ai"]
    decision: Literal["keep", "fix", "ask"] | None = None
    note: str | None = None
    business_impact: str | None = None
