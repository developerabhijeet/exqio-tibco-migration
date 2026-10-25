"""Event projector: SME decisions and curations (rows of ``memory_events``) as Memory and
Curation nodes."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from engine.memory.model import MemNode, MemRel, ScopedDelta
from engine.memory.projectors.util import clean


class EventInput(BaseModel):
    id: str
    kind: Literal["decision", "curation"]
    action: str | None = None
    text: str = ""
    context: dict[str, Any] = {}
    target_uid: str | None = None
    replacement_uid: str | None = None
    actor: str
    created_at: datetime


def _tail(uid: str) -> str:
    return uid.rsplit("/", 1)[-1]


def _mapping_ends(rel_uid: str) -> tuple[str, str]:
    """``(src, dst)`` of a ``MIGRATED_TO:<src>-><dst>`` relationship uid."""
    head, _, rest = rel_uid.partition(":")
    src, arrow, dst = rest.partition("->")
    if head != "MIGRATED_TO" or not arrow:
        raise ValueError(f"not a MIGRATED_TO relationship uid: {rel_uid!r}")
    return src, dst


def _memory(e: EventInput, uid: str, kind: str, name: str, text: str, source: str) -> MemNode:
    return MemNode(label="Memory", uid=uid, name=name, text=text, props=clean({
        "kind": kind, "source": source, "created_by": e.actor,
        "created_at": e.created_at.isoformat(),
        "context": json.dumps(e.context, sort_keys=True, default=str),
        "artifact_type": "memory", "artifact_id": e.id, "process_id": e.context.get("process_id")}))


def project_event(e: EventInput, target_label: str | None,
                  target_name: str | None) -> ScopedDelta:
    if e.kind == "decision":
        node = _memory(e, e.id, str(e.context.get("kind", "decision")), e.text[:80], e.text, "sme")
        about = e.context.get("about_uid")
        rels = [MemRel(type="ABOUT", src=e.id, dst=about)] if about else []
        return ScopedDelta(scope=f"decision:{e.id}", nodes=[node], rels=rels)

    action, target, reason = e.action or "", e.target_uid or "", str(e.context.get("reason", ""))
    if action not in ("note", "reject_mapping", "correct_mapping", "obsolete"):
        raise ValueError(f"unknown curation action {action!r}")
    if not target:
        raise ValueError(f"curation {action} needs a target_uid")
    if action == "correct_mapping" and not e.replacement_uid:
        raise ValueError("curation correct_mapping needs a replacement_uid")
    cuid = f"curation:{e.id}"
    mapping = action in ("reject_mapping", "correct_mapping")
    source, old_dst = _mapping_ends(target) if mapping else (target, "")
    subject = (f"{target_label} " if target_label else "") + (target_name or _tail(source))
    curation = MemNode(
        label="Curation", uid=cuid, name=f"{action} {subject}".strip(),
        text=f"{action} on {subject} by {e.actor}: {reason}" if reason else
        f"{action} on {subject} by {e.actor}",
        props=clean({"action": action, "target_uid": target,
                      "replacement_uid": e.replacement_uid, "actor": e.actor, "reason": reason,
                      "at": e.created_at.isoformat(), "artifact_type": "curation",
                      "artifact_id": e.id, "process_id": e.context.get("process_id")}))
    nodes, rels = [curation], [MemRel(type="ABOUT", src=cuid, dst=source)]
    muid = f"memory:curation:{e.id}"
    if action == "note":
        nodes.append(_memory(e, muid, "note", e.text[:80], e.text, "curation"))
    elif mapping:
        activity = target_name or _tail(source)
        if action == "reject_mapping":
            text = f"Do not map `{activity}` to `{_tail(old_dst)}`: {reason}"
        else:
            text = f"Map `{activity}` to `{_tail(e.replacement_uid or '')}`: {reason}"
            rels.append(MemRel(type="MIGRATED_TO", src=source, dst=e.replacement_uid or "",
                               props={"status": "corrected", "curated_by": e.actor}))
        nodes.append(_memory(e, muid, "mapping", text[:80], text, "curation"))
    if len(nodes) > 1:
        rels.append(MemRel(type="ABOUT", src=muid, dst=source))
    return ScopedDelta(scope=f"curation:{e.id}", nodes=nodes, rels=rels)
