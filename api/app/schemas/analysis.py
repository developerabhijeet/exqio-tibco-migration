"""API models for tab 2 (domains, graph) and tab 3 (analysis, rules, findings, gates, audit, Ask)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

GateName = Literal["G1", "G2", "G3", "G4", "G5", "G6"]


# ------------------------------------------------------------------ domains and graph
class DomainOut(BaseModel):
    id: str
    key: str
    name: str
    suggested_name: str
    process_ids: list[str]  # API process ids (``/processes/{id}``)
    ir_process_ids: list[str]
    evidence: list[dict[str, Any]]  # {kind: module|table|queue|call|name, text}
    renamed_by: str | None


class DomainRename(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class GraphNodeOut(BaseModel):
    id: str  # e.g. process:<ir id>, table:<name>, queue:<name>
    kind: str
    name: str
    attrs: dict[str, Any]  # process nodes add process_id, complexity_band, complexity_score,
    #                        migration_status, domain_id


class GraphEdgeOut(BaseModel):
    src: str
    dst: str
    kind: str
    attrs: dict[str, Any]


class GraphOut(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


# ------------------------------------------------------------------ analysis
class CitationOut(BaseModel):
    activity_id: str | None
    kind: str | None
    text: str | None
    verified: bool = True
    reason: str | None = None


class ChallengeOut(BaseModel):
    verdict: Literal["agree", "disagree"]
    reason: str
    model: str


class RuleOut(BaseModel):
    id: str
    rule_key: str  # R1..Rn
    text: str
    original_text: str | None
    citations: list[CitationOut]
    confidence: float
    status: Literal["draft", "approved", "edited", "rejected", "unverified"]
    owner: str | None
    memory_refs: list[str]
    challenge: ChallengeOut | None
    updated_by: str | None
    updated_at: str | None


class FindingCitationOut(BaseModel):
    activity_id: str
    kind: str
    text: str


class FindingOut(BaseModel):
    id: str
    finding_key: str  # S<n>-<process>-<k> (static) or F1..Fn (ai)
    severity: Literal["high", "medium", "low"]
    text: str
    citations: list[FindingCitationOut]
    source: Literal["static", "ai"]
    business_impact: str | None
    decision: Literal["keep", "fix", "ask"] | None
    note: str | None
    decided_by: str | None
    decided_at: str | None


class VerificationOut(BaseModel):
    verified: int
    total: int
    failures: list[dict[str, Any]]  # {rule_id: rule key, citation: {activity_id, kind, text}, reason}
    passed: bool  # no failing citation on a rule that is not rejected


class CoverageItemOut(BaseModel):
    kind: Literal["activity", "transition", "mapping"]
    ref: str
    covered_by: list[str]  # rule keys
    technical_reason: str | None


class CoverageOut(BaseModel):
    covered: int
    total: int
    complete: bool
    items: list[CoverageItemOut]


class GateOut(BaseModel):
    gate: GateName
    state: Literal["pending", "approved", "rejected", "needs_review"]
    stale: bool  # decided, but the spec hash moved since (reset on the next action, never by a GET)
    enabled: bool
    reason: str | None  # why it is not enabled
    spec_hash: str | None
    design_hash: str | None
    build_hash: str | None
    evidence_hash: str | None
    decided_by: str | None
    decided_at: str | None
    note: str | None
    reset_reason: str | None
    notes: list[str] = []  # shown with the gate, never blocking (G5: unconfirmed intended changes)


class AnalysisOut(BaseModel):
    process_id: str
    analysed: bool
    analysis_id: str | None
    job_id: str | None
    created_at: str | None
    provider: str | None  # fake|claude|azure|none
    ai_available: bool
    ai_reason: str | None
    notice: str | None  # "code-only analysis: no model configured" when no provider
    static_only: bool | None
    summary: str | None
    rules: list[RuleOut]
    findings: list[FindingOut]
    verification: VerificationOut
    coverage: CoverageOut
    spec_hash: str
    gates: list[GateOut]
    blockers: list[str]  # e.g. "unknown activity types: ..."
    ir_changed: bool  # the IR changed since the latest analysis ran: re-run analysis


class RulePatch(BaseModel):
    status: Literal["approved", "rejected", "draft"] | None = None
    text: str | None = None


class FindingPatch(BaseModel):
    decision: Literal["keep", "fix", "ask"]
    note: str | None = None


class GateDecision(BaseModel):
    state: Literal["approved", "rejected"]
    note: str | None = None


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    project_id: str | None
    process_id: str | None
    actor: str
    actor_kind: str
    action: str
    gate: str | None
    detail: dict[str, Any]


# ------------------------------------------------------------------ Ask
class AskIn(BaseModel):
    project_id: str
    question: str = Field(min_length=1, max_length=2000)


class HitOut(BaseModel):
    id: str
    kind: str  # summary|rule|finding|decision|pattern|seed|...
    process_id: str | None  # API process id for analysis chunks
    text: str
    score: float


class AnswerOut(BaseModel):
    text: str
    citations: list[HitOut]
    used_model: bool
