"""Gate projector: one GateDecision uid per gate; each decision is a new version of it."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from engine.memory.model import MemNode, MemRel, ScopedDelta
from engine.memory.projectors.analysis import ProcessRef
from engine.memory.projectors.estate import _clean


class GateInput(BaseModel):
    gate: str
    state: str
    actor: str
    note: str = ""
    decided_at: datetime | str | None = None
    spec_hash: str | None = None
    design_hash: str | None = None
    build_hash: str | None = None
    evidence_hash: str | None = None
    gate_row_id: str | None = None


def project_gate(ref: ProcessRef, g: GateInput) -> ScopedDelta:
    uid = f"gate:{ref.ir_id}:{g.gate}"
    decided = g.decided_at.isoformat() if isinstance(g.decided_at, datetime) else g.decided_at
    node = MemNode(
        label="GateDecision", uid=uid, name=f"{g.gate} {g.state}",
        text=f"{g.gate} {g.state} by {g.actor}" + (f": {g.note}" if g.note else ""),
        props=_clean({"gate": g.gate, "state": g.state, "actor": g.actor, "note": g.note,
                      "decided_at": decided, "spec_hash": g.spec_hash,
                      "design_hash": g.design_hash, "build_hash": g.build_hash,
                      "evidence_hash": g.evidence_hash, "artifact_type": "gate",
                      "artifact_id": g.gate_row_id, "process_id": ref.api_id}))
    return ScopedDelta(scope=f"gate:{ref.ir_id}:{g.gate}", nodes=[node],
                       rels=[MemRel(type="ABOUT", src=uid, dst=f"process:{ref.ir_id}")])
