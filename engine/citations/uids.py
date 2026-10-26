"""Memory uids of the things a phase's claims cite, for refs built without a memory search.

These are SP2's own builders, re-exported from the projectors so a ref's ``memory_uid`` can
never drift from the node the projector wrote."""
from __future__ import annotations

from engine.memory.projectors.analysis import activity_uid, chunk_uid, process_uid, slug
from engine.memory.projectors.build import method_uid
from engine.memory.projectors.design import decision_uid as decision_chunk_uid

__all__ = [
    "activity_uid", "decision_chunk_uid", "finding_chunk_uid", "method_uid", "process_uid",
    "rule_chunk_uid", "slug",
]


def rule_chunk_uid(ir_id: str, key: str) -> str:
    return chunk_uid(ir_id, "rule", key)


def finding_chunk_uid(ir_id: str, key: str) -> str:
    return chunk_uid(ir_id, "finding", key)
