"""Design projector: the narrative and each decision of a DesignRecord as Chunks ABOUT the process."""
from __future__ import annotations

from engine.dotnet_gen.models import DesignRecord
from engine.memory.model import MemNode, MemRel, ScopedDelta
from engine.memory.projectors.analysis import ProcessRef, slug


def project_design(ref: ProcessRef, design: DesignRecord, design_id: str) -> ScopedDelta:
    def chunk(uid: str, name: str, text: str) -> MemNode:
        return MemNode(label="Chunk", uid=uid, name=name, text=text,
                       props={"kind": "design", "artifact_type": "design",
                              "artifact_id": design_id, "process_id": ref.api_id})

    nodes: list[MemNode] = []
    if design.narrative and design.narrative.strip():
        nodes.append(chunk(f"chunk:{ref.ir_id}:design:narrative", "design narrative",
                           design.narrative))
    used: set[str] = set()
    for d in design.decisions:
        base = slug(d.title) or "decision"
        key, n = base, 1
        while key in used:  # two decisions with one title must not share a uid
            n += 1
            key = f"{base[:55]}-{n}"
        used.add(key)
        alternatives = ", ".join(d.alternatives) if d.alternatives else "none"
        nodes.append(chunk(f"chunk:{ref.ir_id}:design:decision:{key}", d.title,
                           f"{d.title}: {d.choice}. Alternatives: {alternatives}. "
                           f"Reason: {d.reason}"))
    process_uid = f"process:{ref.ir_id}"
    return ScopedDelta(scope=f"design:{ref.ir_id}", nodes=nodes,
                       rels=[MemRel(type="ABOUT", src=n.uid, dst=process_uid) for n in nodes])
