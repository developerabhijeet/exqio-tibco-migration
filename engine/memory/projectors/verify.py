"""Verify projector: parity per scenario as a finding Chunk and an Error per defect."""
from __future__ import annotations

from pydantic import BaseModel

from engine.memory.model import MemNode, MemRel, ScopedDelta
from engine.memory.projectors.analysis import ProcessRef
from engine.memory.projectors.build import MESSAGE_MAX


class DefectInput(BaseModel):
    field: str
    expected: str = ""
    actual: str = ""
    finding_key: str = ""
    note: str = ""


class RunInput(BaseModel):
    run_id: str
    scenario_key: str
    status: str
    parity: float | None = None
    defects: list[DefectInput] = []


class ParityInput(BaseModel):
    build_id: str
    runs: list[RunInput] = []


def project_verify(ref: ProcessRef, p: ParityInput, starter_method_uid: str | None) -> ScopedDelta:
    nodes: dict[str, MemNode] = {}
    rels: dict[str, MemRel] = {}
    process_uid = f"process:{ref.ir_id}"

    def add_rel(rtype: str, src: str, dst: str) -> None:
        rel = MemRel(type=rtype, src=src, dst=dst)  # type: ignore[arg-type]
        rels.setdefault(rel.uid, rel)

    for run in p.runs:
        parity = "n/a" if run.parity is None else f"{run.parity:g}"
        cuid = f"chunk:{ref.ir_id}:verify:{run.scenario_key}"
        nodes[cuid] = MemNode(
            label="Chunk", uid=cuid, name=f"verify {run.scenario_key}",
            text=f"Scenario {run.scenario_key}: {run.status}, parity {parity}%",
            props={"kind": "finding", "artifact_type": "verify_run", "artifact_id": run.run_id,
                   "process_id": ref.api_id})
        add_rel("ABOUT", cuid, process_uid)
        for d in run.defects:
            euid = f"error:parity:{ref.ir_id}:{run.scenario_key}/{d.field}"
            message = (f"{d.field}: expected {d.expected!r}, actual {d.actual!r}"
                       + (f". {d.note}" if d.note else ""))[:MESSAGE_MAX]
            props = {"kind": "parity", "message": message, "artifact_type": "verify_run",
                     "artifact_id": run.run_id, "process_id": ref.api_id}
            if d.finding_key:
                props["finding_key"] = d.finding_key
            nodes[euid] = MemNode(label="Error", uid=euid, name=f"parity {d.field}",
                                  text=message, props=props)
            add_rel("ABOUT", euid, process_uid)
            if starter_method_uid:
                add_rel("FAILED_AT", euid, starter_method_uid)
    return ScopedDelta(scope=f"verify:{ref.ir_id}", nodes=list(nodes.values()),
                       rels=list(rels.values()))
