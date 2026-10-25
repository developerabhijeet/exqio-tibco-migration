"""The memory graph's model: closed label and type sets, the delta and revision models, content
hashing and the write-path masking every stored string passes through."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel

from engine.agents.provider import mask_sensitive

NODE_LABELS: tuple[str, ...] = (
    "Application", "Process", "Activity", "Contract", "SharedResource", "SubstVar", "Table",
    "Queue", "Topic", "Endpoint", "Domain", "Solution", "CodeProject", "CodeFile", "Class",
    "Method", "ConfigKey", "MessagingEntity", "Test", "Chunk", "Memory", "GateDecision", "Error",
    "Curation",
)
NodeLabel = Literal[
    "Application", "Process", "Activity", "Contract", "SharedResource", "SubstVar", "Table",
    "Queue", "Topic", "Endpoint", "Domain", "Solution", "CodeProject", "CodeFile", "Class",
    "Method", "ConfigKey", "MessagingEntity", "Test", "Chunk", "Memory", "GateDecision", "Error",
    "Curation",
]

REL_TYPES: tuple[str, ...] = (
    "CONTAINS", "CALLS", "READS", "WRITES", "PUBLISHES", "CONSUMES", "EXPOSES", "USES",
    "MIGRATED_TO", "TESTS", "FAILED_AT", "ABOUT",
)
RelType = Literal[
    "CONTAINS", "CALLS", "READS", "WRITES", "PUBLISHES", "CONSUMES", "EXPOSES", "USES",
    "MIGRATED_TO", "TESTS", "FAILED_AT", "ABOUT",
]

# Labels that carry a 384-dimension `embedding` and have a vector index.
EMBEDDED_LABELS: tuple[str, ...] = ("Chunk", "Memory", "Process", "Activity", "Class", "Method")
# Labels searched as knowledge (retrieval's chunk and memory halves).
KNOWLEDGE_LABELS: tuple[str, ...] = ("Chunk", "Memory")

# The three groups of spec section 1. `Endpoint` belongs to both code groups: its `side`
# property says which one a given endpoint is.
OLD_LABELS: tuple[str, ...] = (
    "Application", "Process", "Activity", "Contract", "SharedResource", "SubstVar", "Table",
    "Queue", "Topic", "Endpoint", "Domain",
)
NEW_LABELS: tuple[str, ...] = (
    "Solution", "CodeProject", "CodeFile", "Class", "Method", "ConfigKey", "MessagingEntity",
    "Test", "Endpoint",
)
KNOWLEDGE_GROUP: tuple[str, ...] = ("Chunk", "Memory", "GateDecision", "Error", "Curation")

# String properties never masked: identifiers and closed-vocabulary values.
IDENTITY_KEYS = frozenset(
    {"uid", "vid", "project_id", "scope", "content_hash", "text_hash", "stack", "side", "kind"}
)

# Properties the write path sets itself; a projector's `props` may not carry them.
RESERVED_NODE_KEYS = frozenset(
    {"uid", "vid", "project_id", "scope", "content_hash", "text_hash", "valid_from", "valid_to",
     "phase", "name", "text", "embedding"}
)
RESERVED_REL_KEYS = frozenset(
    {"uid", "project_id", "scope", "content_hash", "valid_from", "valid_to", "phase"}
)

Phase = Literal[
    "discover", "analysis", "design", "build", "verify", "deploy", "gate", "decision", "seed",
    "curation", "migration", "reingest",
]


class KnowledgeError(Exception):
    """A write was refused (raw XML, unknown kind) or the store is inconsistent."""


# Raw TIBCO process markup is never embedded or stored as knowledge: summaries and rules are
# written in prose, and the XML stays in the parsed model.
_RAW_XML = re.compile(r"<\s*/?\s*(?:bpws|pd|tibex)\s*:", re.IGNORECASE)


def guard_text(text: str) -> None:
    if not text or not text.strip():
        raise KnowledgeError("empty text cannot be stored")
    if _RAW_XML.search(text):
        raise KnowledgeError(
            "raw TIBCO process XML (bpws:/pd:/tibex: markup) is never embedded; "
            "store a prose summary instead"
        )


class MemNode(BaseModel):
    label: NodeLabel
    uid: str
    name: str
    text: str = ""
    props: dict[str, Any] = {}
    embedding: list[float] | None = None  # preset vector (the migration); never hashed


class MemRel(BaseModel):
    type: RelType
    src: str
    dst: str
    props: dict[str, Any] = {}

    @property
    def uid(self) -> str:
        return f"{self.type}:{self.src}->{self.dst}"


class ScopedDelta(BaseModel):
    scope: str
    nodes: list[MemNode] = []
    rels: list[MemRel] = []


class RevisionMeta(BaseModel):
    project_id: str
    phase: Phase
    ingest_key: str
    actor: str
    source_job_id: str | None = None
    ingest_job_id: str | None = None
    iteration: int = 1


class ApplyResult(BaseModel):
    revision: int | None
    skipped: bool = False
    added: int = 0
    changed: int = 0
    removed: int = 0
    reattached: int = 0
    embedded: int = 0
    dangling: int = 0
    refused: int = 0


def side_of(label: str, props: dict[str, Any]) -> Literal["old", "new", "knowledge"]:
    if label == "Endpoint":
        side = props.get("side")
        if side not in ("old", "new"):
            raise ValueError(f"an Endpoint needs props['side'] 'old' or 'new', got {side!r}")
        return side
    if label in KNOWLEDGE_GROUP:
        return "knowledge"
    if label in NEW_LABELS:
        return "new"
    if label in OLD_LABELS:
        return "old"
    raise ValueError(f"unknown node label {label!r}")


def _sha(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def node_hash(n: MemNode) -> str:
    return _sha({"label": n.label, "name": n.name, "text": n.text, "props": n.props})


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def rel_hash(r: MemRel) -> str:
    return _sha({"type": r.type, "src": r.src, "dst": r.dst, "props": r.props})


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_sensitive(value)
    if isinstance(value, list):
        return [_mask_value(v) for v in value]
    return value


def _mask_props(props: dict[str, Any]) -> dict[str, Any]:
    return {k: v if k in IDENTITY_KEYS else _mask_value(v) for k, v in props.items()}


def mask_node(n: MemNode) -> tuple[MemNode, bool]:
    """A copy of ``n`` with every non-identity string masked; ``True`` when its text was raw
    TIBCO XML, which is then dropped (``""``)."""
    text, refused = n.text, False
    if text.strip():  # guard_text also refuses blank text, which is not a refusal here
        try:
            guard_text(text)
        except KnowledgeError:
            text, refused = "", True
    masked = n.model_copy(update={
        "name": mask_sensitive(n.name),
        "text": mask_sensitive(text),
        "props": _mask_props(n.props),
    })
    return masked, refused


def mask_rel(r: MemRel) -> MemRel:
    return r.model_copy(update={"props": _mask_props(r.props)})
