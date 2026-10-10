"""Design record models (design spec sections 2.4 and 7): the deterministic backbone of tab 4.

Every TIBCO step maps to a C# file, class and method by code (``origin="code"``) before any
model writes a word; rows whose body needs a model are marked ``origin="agent"``.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProjectKind = Literal["api", "core", "infrastructure", "tests"]
Origin = Literal["code", "agent"]


class MappingRow(BaseModel):
    activity_id: str
    activity_name: str
    canonical_type: str
    pattern: str
    project: str
    file: str  # relative to the project folder
    class_name: str
    method: str
    rule_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    origin: Origin = "code"


class ProjectLayout(BaseModel):
    name: str
    kind: ProjectKind
    files: list[str] = Field(default_factory=list)  # relative to the project folder


class SolutionLayout(BaseModel):
    name: str  # the solution file is ``<name>.sln``
    projects: list[ProjectLayout] = Field(default_factory=list)


class ContractMap(BaseModel):
    contract_id: str
    record_names: list[str] = Field(default_factory=list)
    namespace: str = ""  # <App>.Core.Contracts.<Module>
    file: str = ""  # Contracts/<Module>/<Stem>.cs, relative to the Core project


class ConfigMap(BaseModel):
    substvar: str
    key: str
    secret: bool = False


class IgnoredConfig(BaseModel):
    substvar: str
    reason: str


class MessagingEntity(BaseModel):
    kind: Literal["queue", "topic", "subscription"]
    name: str
    direction: Literal["in", "out"]


class ResilienceMap(BaseModel):
    resource_id: str
    timeout_ms: int | None = None
    retry: dict[str, Any] | None = None
    breaker: dict[str, Any] | None = None


class SequenceStep(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    label: str


class Decision(BaseModel):
    title: str
    choice: str
    alternatives: list[str] = Field(default_factory=list)
    reason: str


class OpenQuestion(BaseModel):
    question: str
    recommendation: str


class ExpectedDifference(BaseModel):
    finding_id: str
    description: str


class DesignRecord(BaseModel):
    process_id: str
    spec_hash: str
    solution: SolutionLayout
    mapping: list[MappingRow] = Field(default_factory=list)
    contracts: list[ContractMap] = Field(default_factory=list)
    config: list[ConfigMap] = Field(default_factory=list)
    config_ignored: list[IgnoredConfig] = Field(default_factory=list)
    messaging: list[MessagingEntity] = Field(default_factory=list)
    resilience: list[ResilienceMap] = Field(default_factory=list)
    sequence: list[SequenceStep] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    expected_differences: list[ExpectedDifference] = Field(default_factory=list)
    narrative: str | None = None
    design_hash: str = ""


# Fields the designer agent may write after the skeleton; they never move the hash.
HASH_EXCLUDED: frozenset[str] = frozenset({"narrative", "decisions", "open_questions", "design_hash"})


def compute_design_hash(record: DesignRecord) -> str:
    """SHA-256 over the record's canonical JSON, excluding ``HASH_EXCLUDED``.

    The hash covers what code decides (process id, spec hash, solution, mapping, contracts, config,
    messaging, resilience, sequence, expected differences); the designer's narrative, decisions and
    open questions are advice on top of it and do not move it.
    """
    payload = record.model_dump(mode="json", exclude=set(HASH_EXCLUDED))
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
