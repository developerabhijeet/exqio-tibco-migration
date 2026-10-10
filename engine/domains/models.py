"""Models for domain discovery, the estate graph and per-workflow I/O cards (tab 2, Discover)."""
from typing import Any, Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    kind: Literal["module", "table", "queue", "call", "name"]
    text: str


class DomainCandidate(BaseModel):
    id: str
    process_ids: list[str]
    evidence: list[Evidence] = Field(default_factory=list)
    suggested_name: str


class Node(BaseModel):
    id: str
    kind: Literal["domain", "application", "process", "activity", "table", "queue", "topic", "endpoint",
                  "resource"]
    name: str
    attrs: dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    src: str
    dst: str
    kind: Literal["contains", "calls", "reads", "writes", "publishes", "consumes", "exposes", "uses"]
    attrs: dict[str, Any] = Field(default_factory=dict)


class Graph(BaseModel):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class IOCard(BaseModel):
    process_id: str
    trigger: dict[str, Any] = Field(default_factory=dict)
    request_contract: dict[str, Any] = Field(default_factory=dict)
    response_contract: dict[str, Any] = Field(default_factory=dict)
    side_effects: list[dict[str, Any]] = Field(default_factory=list)
    faults: list[str] = Field(default_factory=list)
    subprocesses: list[str] = Field(default_factory=list)
