"""Offline stand-ins for the memory retriever (ExiQoian engine tests)."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from engine.memory.model import KNOWLEDGE_LABELS, Citation, MemoryHit


def hit(uid: str, label: str, phase: str, score: float, *, text: str = "some text",
        kind: str | None = None, artifact_type: str | None = None, process_id: str | None = None,
        path: str | None = None, props: dict | None = None) -> MemoryHit:
    """A hit whose Citation is built like the graph builds it (``Citation.of``)."""
    stored = {"uid": uid, "phase": phase, "valid_from": 1, "artifact_type": artifact_type,
              "process_id": process_id, "path": path}
    return MemoryHit(uid=uid, label=label, kind=kind, text=text, score=score, keyword_score=score,
                     vector_score=0.0, citation=Citation.of(label, stored), props=props or {})


@dataclass
class RetrieveCall:
    project_id: str
    query: str
    labels: tuple[str, ...]
    k: int
    min_terms: int
    include_obsolete: bool


@dataclass
class FakeRetriever:
    """Records every call; returns the hits whose label is asked for, best score first, up to k."""
    hits: list[MemoryHit]
    calls: list[RetrieveCall] = field(default_factory=list)

    async def retrieve(self, project_id: str, query: str, *, labels: Sequence[str] = KNOWLEDGE_LABELS,
                       k: int = 8, min_terms: int = 2, include_obsolete: bool = False,
                       **_: object) -> list[MemoryHit]:
        self.calls.append(RetrieveCall(project_id, query, tuple(labels), k, min_terms, include_obsolete))
        found = sorted((h for h in self.hits if h.label in labels), key=lambda h: -h.score)
        return found[:k]
