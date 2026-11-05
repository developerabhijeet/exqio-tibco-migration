"""Deploy projector: a failed plan as an Error at the infra file its output names."""
from __future__ import annotations

from pydantic import BaseModel

from engine.memory.model import MemRel, ScopedDelta
from engine.memory.projectors.analysis import ProcessRef
from engine.memory.projectors.build import STACK
from engine.memory.projectors.util import error_node


class PlanInput(BaseModel):
    plan_id: str
    status: str
    output: str = ""
    infra_paths: list[str] = []


def project_deploy(ref: ProcessRef, p: PlanInput) -> ScopedDelta:
    scope = f"deploy:{ref.ir_id}"
    if p.status != "failed":
        return ScopedDelta(scope=scope)  # an empty delta closes an earlier deploy Error
    uid = f"error:deploy:{ref.ir_id}:{p.plan_id}"
    node = error_node(uid, f"deploy plan {p.plan_id} failed", "deploy", p.output,
                      artifact_type="plan", artifact_id=p.plan_id, process_id=ref.api_id)
    path = next((x for x in p.infra_paths if x in p.output), None)
    rel = (MemRel(type="FAILED_AT", src=uid, dst=f"codefile:{STACK}:{ref.ir_id}/{path}") if path
           else MemRel(type="ABOUT", src=uid, dst=f"solution:{STACK}:{ref.ir_id}"))
    return ScopedDelta(scope=scope, nodes=[node], rels=[rel])
