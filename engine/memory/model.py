"""The memory graph's model: closed label and type sets, the delta and revision models, content
hashing and the write-path masking every stored string passes through."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from engine.agents.provider import mask_sensitive
from engine.memory.diff import DiffSet

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
     "closed_in", "phase", "name", "text", "embedding"}
)
RESERVED_REL_KEYS = frozenset(
    {"uid", "project_id", "scope", "content_hash", "valid_from", "valid_to", "closed_in", "phase"}
)

# `valid_to` of a current version (rulings R25): never null, so current-version lookups seek an
# index (Neo4j indexes skip null properties). Read paths map it to None at every boundary.
OPEN_VALID_TO = 2**62

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


# ------------------------------------------------------------------ read models
# Stored properties a read does not return in `props`: bookkeeping, or carried by a field of
# its own. `closed_in` (= valid_to of a closed version) exists only so "closed at revision X"
# seeks an index.
_NODE_BOOKKEEPING = RESERVED_NODE_KEYS | {"side"}
_REL_BOOKKEEPING = RESERVED_REL_KEYS

Side = Literal["old", "new", "knowledge"]
EdgeStatus = Literal["generated", "corrected", "rejected"]
EDGE_STATUSES: tuple[str, ...] = ("generated", "corrected", "rejected")
# Curation actions whose target (a MIGRATED_TO uid) reads as rejected.
REJECTING_ACTIONS: tuple[str, ...] = ("reject_mapping", "correct_mapping")


def outward_valid_to(valid_to: int) -> int | None:
    """``valid_to`` as the outside sees it: None while the version is current (rulings R25)."""
    return None if valid_to >= OPEN_VALID_TO else valid_to


def public_node_props(stored: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in stored.items() if k not in _NODE_BOOKKEEPING}


def public_rel_props(stored: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in stored.items() if k not in _REL_BOOKKEEPING}


class NodeOut(BaseModel):
    uid: str
    label: NodeLabel
    name: str
    side: Side
    props: dict[str, Any]
    valid_from: int
    valid_to: int | None
    curated: bool = False  # the ABOUT-target of a current Curation
    obsolete: bool = False  # a Memory targeted by a current obsolete Curation
    text: str = ""

    @classmethod
    def of(cls, label: str, stored: dict[str, Any], *, curated: bool = False,
           obsolete: bool = False) -> NodeOut:
        return cls(uid=stored["uid"], label=label, name=stored.get("name") or "",
                   side=stored["side"], props=public_node_props(stored),
                   valid_from=stored["valid_from"], valid_to=outward_valid_to(stored["valid_to"]),
                   curated=curated, obsolete=obsolete, text=stored.get("text") or "")


class EdgeOut(BaseModel):
    uid: str
    type: RelType
    src: str
    dst: str
    props: dict[str, Any]
    status: EdgeStatus | None = None

    @classmethod
    def of(cls, rel_type: str, src: str, dst: str, stored: dict[str, Any], *,
           rejected: bool = False) -> EdgeOut:
        """A MIGRATED_TO reads ``rejected`` when a current curation rejected or corrected it,
        else its own ``status``; other types carry no status."""
        status = None
        if rel_type == "MIGRATED_TO":
            own = stored.get("status")
            status = "rejected" if rejected else (own if own in EDGE_STATUSES else None)
        return cls(uid=stored["uid"], type=rel_type, src=src, dst=dst,
                   props=public_rel_props(stored), status=status)


class Subgraph(BaseModel):
    nodes: list[NodeOut]
    edges: list[EdgeOut]
    truncated: bool
    total: int


class VersionOut(BaseModel):
    valid_from: int
    valid_to: int | None
    phase: str
    actor: str | None
    at: datetime | None
    props: dict[str, Any]
    changed_keys: list[str]  # against the previous version (none for the first)


class LineageOut(BaseModel):
    edge: EdgeOut
    other: NodeOut
    direction: Literal["in", "out"]


class NodeDetail(BaseModel):
    node: NodeOut
    versions: list[VersionOut]  # newest first
    about: list[NodeOut]  # knowledge ABOUT the node, newest first
    lineage: list[LineageOut]
    curations: list[NodeOut]


class RevisionOut(BaseModel):
    n: int
    phase: str
    scopes: list[str]
    actor: str
    at: datetime
    job_id: str | None = None  # Neo4j does not store null properties
    iteration: int
    added: int
    changed: int
    removed: int


class RevisionDiff(BaseModel):
    revision: RevisionOut
    nodes: DiffSet
    relationships: DiffSet
