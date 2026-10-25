"""Estate projector: parsed applications, domains and the estate graph as old-code nodes."""
from __future__ import annotations

import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel

from engine.domains.discover import resolve_call
from engine.domains.models import Graph
from engine.ir.models import Activity, Application, Process
from engine.memory.model import MemNode, MemRel, ScopedDelta
from engine.memory.projectors.analysis import ProcessRef

log = logging.getLogger(__name__)
_SUBST = re.compile(r"%%([^%\s]+)%%")
_EDGE_TYPES = {"contains": "CONTAINS", "calls": "CALLS", "reads": "READS", "writes": "WRITES",
               "publishes": "PUBLISHES", "consumes": "CONSUMES", "exposes": "EXPOSES",
               "uses": "USES"}


class DomainInput(BaseModel):
    """``key`` is the DomainCandidate id (estate-graph edges use ``domain:<key>``)."""

    key: str
    name: str
    suggested_name: str
    ir_process_ids: list[str] = []


def _uid(graph_id: str) -> str:
    """The memory uid of an estate-graph node id (spec section 1)."""
    if graph_id.startswith("app:"):
        return "application:" + graph_id[4:]
    if graph_id.startswith("endpoint:"):
        return "endpoint:old:" + graph_id[9:]
    return graph_id  # domain:, process:, activity:, table:, queue:, topic:, resource: match


def _clean(props: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in props.items() if v is not None and v != ""}


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _relative(source_path: str | None, root: Path | str | None) -> str | None:
    """``source_path`` relative to the scan root; the basename when it cannot be relativized."""
    if not source_path:
        return None
    posix = PurePosixPath(source_path.replace("\\", "/"))
    if root is not None:
        try:
            return posix.relative_to(PurePosixPath(str(root).replace("\\", "/"))).as_posix()
        except ValueError:
            pass
    return posix.name


def _starter_kind(p: Process, activity: Activity) -> str | None:
    return p.starter.kind if p.starter and p.starter.activity_id == activity.id else None


def project_estate(apps: list[Application], domains: list[DomainInput], graph: Graph,
                   refs: list[ProcessRef], root: Path | str | None = None) -> ScopedDelta:
    api_ids = {r.ir_id: r.api_id for r in refs}
    nodes: dict[str, MemNode] = {}
    rels: dict[str, MemRel] = {}

    def add_node(node: MemNode) -> None:
        nodes.setdefault(node.uid, node)

    def add_rel(rtype: str, src: str, dst: str, props: dict[str, Any] | None = None) -> None:
        rel = MemRel(type=rtype, src=src, dst=dst, props=props or {})  # type: ignore[arg-type]
        rels.setdefault(rel.uid, rel)

    for d in domains:
        add_node(MemNode(label="Domain", uid=f"domain:{d.key}", name=d.name,
                         props={"suggested_name": d.suggested_name}))

    for app in apps:
        add_node(MemNode(label="Application", uid=f"application:{app.id}", name=app.name,
                         props={"dialect": app.dialect, "modules": list(app.modules)}))
        for r in app.resources:  # settings are never projected
            add_node(MemNode(label="SharedResource", uid=f"resource:{r.id}", name=r.name or r.id,
                             props={"kind": r.kind}))
        for s in app.substvars:  # values are never projected
            add_node(MemNode(label="SubstVar", uid=f"substvar:{app.id}/{s.name}", name=s.name,
                             props={"type": s.type}))
        for c in app.contracts:
            add_node(MemNode(label="Contract", uid=f"contract:{c.id}", name=c.id,
                             props={"kind": c.kind, "path": _relative(c.path, root),
                                    "root_elements": list(c.root_elements)}))
        subst_names = {s.name for s in app.substvars}
        by_id = {p.id: p for p in app.processes}

        for p in app.processes:
            puid = f"process:{p.id}"
            api_id = api_ids.get(p.id)
            cite = _clean({"artifact_type": "process", "artifact_id": api_id,
                           "process_id": api_id})
            starter_kind = p.starter.kind if p.starter else None
            summary = (f"{p.name} ({app.name}/{p.module or 'default'}), "
                       f"{starter_kind or 'subprocess'} starter, {len(p.activities)} activities: "
                       + ", ".join(a.name for a in p.activities))
            add_node(MemNode(
                label="Process", uid=puid, name=p.name, text=summary,
                props=_clean({**cite, "module": p.module, "package": p.package,
                              "starter_kind": starter_kind,
                              "complexity_band": p.complexity.band if p.complexity else None,
                              "path": _relative(p.source_path, root)})))
            add_rel("CONTAINS", f"application:{app.id}", puid)

            for a in p.activities:
                auid = f"activity:{p.id}/{a.id}"
                sk = _starter_kind(p, a)
                props = {**cite, "type": a.type, "native_type": a.native_type,
                         "starter": sk is not None}
                if sk:
                    props["starter_kind"] = sk
                add_node(MemNode(label="Activity", uid=auid, name=a.name,
                                 text=f"{a.name} ({a.type}) in {p.name}", props=_clean(props)))
                add_rel("CONTAINS", puid, auid)

            for cid in p.contracts_used:
                if f"contract:{cid}" in nodes:
                    add_rel("USES", puid, f"contract:{cid}")

            configs = [a.config for a in p.activities] + ([p.starter.config] if p.starter else [])
            used = {m for cfg in configs for s in _strings(cfg) for m in _SUBST.findall(s)}
            for name in sorted(used & subst_names):
                add_rel("USES", puid, f"substvar:{app.id}/{name}")

            for c in p.calls:  # Activity -> Process
                target = resolve_call(c.target_process, by_id)
                if target:
                    add_rel("CALLS", f"activity:{p.id}/{c.activity_id}", f"process:{target}")

    # Estate graph nodes the apps do not already give: tables, queues, topics, old endpoints.
    for n in graph.nodes:
        uid = _uid(n.id)
        if n.kind == "table":
            add_node(MemNode(label="Table", uid=uid, name=n.name, props=_clean(
                {"schema": n.attrs.get("schema")})))
        elif n.kind in ("queue", "topic"):
            add_node(MemNode(label=n.kind.capitalize(), uid=uid, name=n.name))  # type: ignore[arg-type]
        elif n.kind == "endpoint":
            method = n.attrs.get("verb") or n.attrs.get("protocol", "").upper() or "HTTP"
            path = n.attrs.get("path") or n.attrs.get("operation") or n.name
            add_node(MemNode(label="Endpoint", uid=uid, name=n.name,
                             props={"method": method, "path": path, "side": "old"}))

    for e in graph.edges:
        src, dst = _uid(e.src), _uid(e.dst)
        rtype = _EDGE_TYPES.get(e.kind)
        if rtype is None:
            log.warning("estate edge kind %r has no memory relationship type; skipped", e.kind)
            continue
        if src in nodes and dst in nodes:
            add_rel(rtype, src, dst, _clean(e.attrs))
    # Estate-graph edges onto nodes outside the delta are dropped, never dangled.
    return ScopedDelta(scope="estate", nodes=list(nodes.values()), rels=list(rels.values()))
