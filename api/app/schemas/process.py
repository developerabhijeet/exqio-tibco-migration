"""API models for process listings and the React Flow canvas."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ProcessSummary(BaseModel):
    id: str
    ir_id: str
    name: str
    application: str
    module: str
    package: str
    dialect: str
    source_path: str
    starter_kind: str | None
    spec_hash: str
    complexity_score: float | None
    complexity_band: str | None
    domain_id: str | None
    domain_name: str | None
    migration_status: str
    activity_count: int


class CanvasNodeData(BaseModel):
    name: str
    canonical_type: str
    native_type: str
    source_line: int | None
    config: dict[str, Any]
    rule_ids: list[str]
    finding_ids: list[str]
    group_id: str | None = None
    starter: bool = False
    unknown: bool = False


class CanvasNode(BaseModel):
    id: str
    type: str  # "activity"
    data: CanvasNodeData


class CanvasEdge(BaseModel):
    id: str
    source: str  # activity id or group id
    target: str
    kind: str  # success|conditional|otherwise|error
    expression: str | None
    label: str | None
    source_line: int | None = None


class CanvasGroup(BaseModel):
    id: str
    kind: str
    member_ids: list[str]
    config: dict[str, Any]
    synthesised: bool
    entry_ids: list[str]


class Canvas(BaseModel):
    process_id: str
    starter_id: str | None
    nodes: list[CanvasNode]
    edges: list[CanvasEdge]
    groups: list[CanvasGroup]
    fault_handlers: list[dict[str, Any]]
