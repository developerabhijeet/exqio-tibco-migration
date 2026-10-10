"""Analysis models shared by the agents, the verifier and the API (design spec section 5).

Created by Task 9 so the agents can be built first; Task 8 added
``CitationFailure``, ``VerificationResult``, ``CoverageItem`` and ``CoverageResult``.
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


class CitationFailure(BaseModel):
    rule_id: str
    citation: Citation
    reason: str


class VerificationResult(BaseModel):
    """Citation-level tally: ``verified + len(failures) == total``."""

    verified: int
    total: int
    failures: list[CitationFailure] = Field(default_factory=list)


class CoverageItem(BaseModel):
    kind: Literal["activity", "transition", "mapping"]
    ref: str
    covered_by: list[str] = Field(default_factory=list)
    technical_reason: str | None = None


class CoverageResult(BaseModel):
    covered: int
    total: int
    items: list[CoverageItem] = Field(default_factory=list)
