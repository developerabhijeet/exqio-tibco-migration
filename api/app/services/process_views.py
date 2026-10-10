"""Read views over a stored process: React Flow canvas, I/O card, raw XML."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.models import Application, Finding, GraphEdge, GraphNode, Process, Rule, Upload
from api.app.services.scan import upload_scan_root
from engine.domains.graph import io_card
from engine.domains.models import Edge, Graph, IOCard, Node
from engine.ir.models import Application as IRApplication
from engine.ir.models import Process as IRProcess

_LABEL_BY_KIND = {"otherwise": "otherwise", "error": "error"}


def _cited_activities(citations: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(str(c.get("activity_id")) for c in citations or [] if c.get("activity_id")))


def build_canvas(ir: dict[str, Any], rules: list[Rule], findings: list[Finding]) -> dict[str, Any]:
    """Nodes, edges and groups for React Flow. Transitions may target group ids."""
    rule_ids: dict[str, list[str]] = {}
    finding_ids: dict[str, list[str]] = {}
    for r in rules:
        for aid in _cited_activities(r.citations):
            rule_ids.setdefault(aid, []).append(r.id)
    for f in findings:
        for aid in _cited_activities(f.citations):
            finding_ids.setdefault(aid, []).append(f.id)

    starter = ir.get("starter") or {}
    starter_id = starter.get("activity_id")
    nodes = []
    for a in ir.get("activities") or []:
        nodes.append({"id": a["id"], "type": "activity", "data": {
            "name": a.get("name", a["id"]), "canonical_type": a.get("type", ""),
            "native_type": a.get("native_type", ""), "source_line": a.get("source_line"),
            "config": a.get("config") or {}, "rule_ids": rule_ids.get(a["id"], []),
            "finding_ids": finding_ids.get(a["id"], []), "group_id": a.get("group_id"),
            "starter": a["id"] == starter_id,
            "unknown": a.get("type") == "unknown"}})
    edges = []
    for t in ir.get("transitions") or []:
        expr = t.get("expression")
        label = expr if t["kind"] == "conditional" else _LABEL_BY_KIND.get(t["kind"])
        edges.append({"id": t["id"], "source": t["from_id"], "target": t["to_id"], "kind": t["kind"],
                      "expression": expr, "label": label, "source_line": t.get("source_line")})
    groups = []
    for g in ir.get("groups") or []:
        cfg = g.get("config") or {}
        groups.append({"id": g["id"], "kind": g["kind"], "member_ids": g.get("member_ids") or [],
                       "config": cfg, "synthesised": bool(cfg.get("synthesised")),
                       "entry_ids": list(cfg.get("entry_ids") or [])})
    return {"process_id": ir["id"], "starter_id": starter_id, "nodes": nodes, "edges": edges,
            "groups": groups, "fault_handlers": ir.get("fault_handlers") or []}


async def canvas_for(session: AsyncSession, proc: Process) -> dict[str, Any]:
    rules = (await session.execute(select(Rule).where(Rule.process_id == proc.id))).scalars().all()
    findings = (await session.execute(select(Finding).where(Finding.process_id == proc.id))).scalars().all()
    return build_canvas(proc.ir, list(rules), list(findings))


async def io_card_for(session: AsyncSession, proc: Process) -> IOCard:
    app_row = await session.get(Application, proc.application_id)
    siblings = (await session.execute(
        select(Process.ir).where(Process.application_id == proc.application_id))).scalars().all()
    app = IRApplication.model_validate({**app_row.ir, "processes": list(siblings)})
    nodes = (await session.execute(select(GraphNode).where(GraphNode.project_id == proc.project_id))).scalars()
    edges = (await session.execute(select(GraphEdge).where(GraphEdge.project_id == proc.project_id))).scalars()
    graph = Graph(nodes=[Node(id=n.node_id, kind=n.kind, name=n.name, attrs=n.attrs or {}) for n in nodes],
                  edges=[Edge(src=e.src, dst=e.dst, kind=e.kind, attrs=e.attrs or {}) for e in edges])
    return io_card(IRProcess.model_validate(proc.ir), app, graph)


async def source_file(session: AsyncSession, proc: Process) -> Path | None:
    """The stored source file of a process, only if it resolves inside its upload folder."""
    upload = await session.get(Upload, proc.upload_id)
    if upload is None:
        return None
    base = Path(upload.root_dir).resolve()
    path = (upload_scan_root(upload) / proc.source_path).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        return None
    return path
