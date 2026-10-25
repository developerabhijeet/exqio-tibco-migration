"""Analysis projector: a process's summary, rules and findings as Chunks ABOUT it."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from engine.memory.model import MemNode, MemRel, ScopedDelta


class ProcessRef(BaseModel):
    ir_id: str
    api_id: str
    name: str


class ChunkInput(BaseModel):
    kind: Literal["summary", "rule", "finding"]
    key: str  # "summary", the rule key or the finding key
    text: str
    activity_ids: list[str] = []


def slug(text: str) -> str:
    """Lowercase ``[a-z0-9]+`` runs joined by ``-``, at most 60 characters."""
    return "-".join(re.findall(r"[a-z0-9]+", text.lower()))[:60].strip("-")


def chunk_uid(ir_id: str, kind: str, key: str) -> str:
    return f"chunk:{ir_id}:summary" if kind == "summary" else f"chunk:{ir_id}:{kind}:{key}"


def project_analysis(ref: ProcessRef, chunks: list[ChunkInput]) -> ScopedDelta:
    nodes: dict[str, MemNode] = {}
    rels: dict[str, MemRel] = {}
    process_uid = f"process:{ref.ir_id}"
    for c in chunks:
        uid = chunk_uid(ref.ir_id, c.kind, c.key)
        nodes[uid] = MemNode(
            label="Chunk", uid=uid, name=c.kind if c.kind == "summary" else f"{c.kind} {c.key}",
            text=c.text,
            props={"kind": c.kind, "artifact_type": "analysis", "artifact_id": ref.api_id,
                   "process_id": ref.api_id})
        targets = [process_uid] + [f"activity:{ref.ir_id}/{a}" for a in c.activity_ids]
        for dst in targets:
            rel = MemRel(type="ABOUT", src=uid, dst=dst)
            rels[rel.uid] = rel
    return ScopedDelta(scope=f"analysis:{ref.ir_id}", nodes=list(nodes.values()),
                       rels=list(rels.values()))
