"""A memory search hit to a ``CiteRef`` (spec section 5): SP2's ``Citation`` phase to the UI
phase, its artifact to the target the chip opens."""
from __future__ import annotations

import re

from engine.agents.provider import mask_sensitive
from engine.citations.model import LABEL_MAX, PREVIEW_MAX, CiteRef, CiteTarget, UiPhase, cut
from engine.memory.model import MemoryHit

MEMORY_PHASE_TO_UI: dict[str, UiPhase] = {
    "discover": "discover",
    "analysis": "validate",
    "decision": "validate",
    "design": "design",
    "build": "build",
    "verify": "verify",
    "deploy": "deploy",
    "seed": "memory",
    "curation": "memory",
    "migration": "memory",
    "reingest": "memory",
}
# The step each gate guards; a gate decision's phase is its gate's step (uid `gate:<ir>:<G>`).
GATE_STEP: dict[str, UiPhase] = {
    "G1": "validate", "G2": "validate", "G3": "design", "G4": "build", "G5": "verify",
    "G6": "deploy",
}

CITE_KINDS = frozenset({
    "rule", "finding", "summary", "decision", "activity", "process", "code", "test", "error",
    "gate", "pattern", "note", "evidence",
})
# The kind a label always reads as; knowledge labels (Chunk, Memory, Curation) use their own
# `kind` when it is a cite kind, else the fallback.
_LABEL_KIND = {
    "Activity": "activity", "Process": "process", "CodeFile": "code", "Class": "code",
    "Method": "code", "Test": "test", "Error": "error", "GateDecision": "gate",
}
_KNOWLEDGE_KIND = {"Chunk": "evidence", "Memory": "note", "Curation": "note"}
_FILE_LABELS = frozenset({"CodeFile", "Class", "Method"})
# Labels whose uid carries a qualifier before the IR id (`method:<stack>:<ir>/...`,
# `error:<source>:<ir>:...`).
_QUALIFIED_LABELS = frozenset({"CodeFile", "Class", "Method", "Test", "Error"})
# The step an artifact type opens when the hit is not an activity, file or memory.
_ARTIFACT_STEP: dict[str, UiPhase] = {
    "process": "discover", "analysis": "validate", "design": "design", "build": "build",
    "build_file": "build", "verify_run": "verify", "plan": "deploy",
}
_GATE_UID = re.compile(r"^gate:[^:]+:(G[1-6])$")


def _ir_id(hit: MemoryHit) -> str | None:
    rest = hit.uid.split(":", 1)[1] if ":" in hit.uid else ""
    if hit.label in _QUALIFIED_LABELS:
        rest = rest.split(":", 1)[1] if ":" in rest else ""
    ir = re.split(r"[:/]", rest, maxsplit=1)[0]
    return ir or None


def _gate_step(hit: MemoryHit) -> UiPhase | None:
    m = _GATE_UID.match(hit.uid)
    gate = m.group(1) if m else hit.props.get("gate")
    return GATE_STEP.get(gate) if isinstance(gate, str) else None


def _phase(hit: MemoryHit) -> UiPhase:
    phase = hit.citation.phase
    if phase == "gate":
        return _gate_step(hit) or "validate"
    return MEMORY_PHASE_TO_UI.get(phase, "memory")


def _kind(hit: MemoryHit) -> str:
    if hit.label in _LABEL_KIND:
        return _LABEL_KIND[hit.label]
    if hit.kind in CITE_KINDS:
        return hit.kind
    return _KNOWLEDGE_KIND.get(hit.label, "evidence")


def _target(hit: MemoryHit, phase: UiPhase, process_ids: dict[str, str]) -> CiteTarget:
    c = hit.citation
    ir = _ir_id(hit)
    process_id = (process_ids.get(ir) if ir else None) or c.process_id
    if hit.label == "Activity":
        activity = hit.uid.split("/", 1)[1] if "/" in hit.uid else None
        return CiteTarget(kind="activity", process_id=process_id, activity_id=activity)
    if c.artifact_type in ("memory", "curation"):
        return CiteTarget(kind="memory", uid=c.uid)
    if (hit.label in _FILE_LABELS or c.artifact_type == "build_file") and c.path:
        return CiteTarget(kind="file", path=c.path, process_id=process_id)
    if c.artifact_type == "gate":
        step = phase if phase != "memory" else "validate"
    elif hit.label == "Process":
        step = "discover"
    else:
        step = _ARTIFACT_STEP.get(c.artifact_type, phase if phase != "memory" else "discover")
    return CiteTarget(kind="step", step=step, process_id=process_id)


def cite_ref(hit: MemoryHit, ref_id: str, process_ids: dict[str, str]) -> CiteRef:
    """``process_ids`` maps an IR id to its API process id."""
    phase = _phase(hit)
    name = re.split(r"[:/]", hit.uid)[-1]
    return CiteRef(
        id=ref_id,
        phase=phase,
        kind=_kind(hit),
        label=cut(mask_sensitive(f"{hit.label} {name}"), LABEL_MAX),
        preview=cut(mask_sensitive(hit.text), PREVIEW_MAX),
        memory_uid=hit.citation.uid,
        revision=hit.citation.revision,
        target=_target(hit, phase, process_ids),
    )
