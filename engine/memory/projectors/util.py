"""Helpers shared by the projectors."""
from __future__ import annotations

from typing import Any

from engine.memory.model import MemNode

MESSAGE_MAX = 500


def clean(props: dict[str, Any]) -> dict[str, Any]:
    """``props`` without None or empty-string values (Neo4j stores no nulls)."""
    return {k: v for k, v in props.items() if v is not None and v != ""}


def error_node(uid: str, name: str, kind: str, message: str, *, artifact_type: str,
               artifact_id: str | None, process_id: str, **props: Any) -> MemNode:
    """An Error node; its message is cut to ``MESSAGE_MAX`` characters. It carries no time: the
    Revision does (rulings R27)."""
    message = message[:MESSAGE_MAX]
    return MemNode(label="Error", uid=uid, name=name, text=message or name, props=clean({
        "kind": kind, "message": message, "artifact_type": artifact_type,
        "artifact_id": artifact_id, "process_id": process_id, **props}))
