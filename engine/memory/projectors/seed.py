"""Seed projector: the shipped TIBCO to .NET patterns as Memory nodes."""
from __future__ import annotations

from typing import Any

from engine.knowledge.memory import pattern_text
from engine.memory.model import MemNode, ScopedDelta


def project_seed(patterns: list[dict[str, Any]]) -> ScopedDelta:
    nodes = [MemNode(label="Memory", uid=f"seed:{p['id']}", name=p["title"],
                     text=pattern_text(p),
                     props={"kind": p["kind"], "source": "seed", "created_by": "system",
                            "artifact_type": "memory"})
             for p in patterns]
    return ScopedDelta(scope="seed", nodes=nodes)
