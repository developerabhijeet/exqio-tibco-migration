"""The scan job: parse an upload with the engine and persist everything tab 1 and tab 2 read.

``scan_folder`` runs in a thread. Every stored payload (IR, report, domain evidence, graph) is
first made relative to the scan root, so nothing stored carries the server's upload path and
the spec hash of an unchanged process is the same whichever upload it came from.

A rescan upserts by key in one transaction, so ids survive and downstream rows (rules,
findings, gates, designs, builds) stay attached: applications by ``app_key``, domains by
``key`` (an SME rename is kept), processes by ``ir_id``. A process whose spec hash moved gets
the new hash and an audit line ``rescanned: spec hash changed <old> -> <new>``; gates go stale
through that hash (Task 18 resets them). Processes, applications and domains that disappeared
are deleted, each removed process with an audit line. The graph is replaced. The upload's
``files`` rows take the scanner's kind, dialect and ``duplicate_of``.

The spec hash of a process that already has decisions covers its approved rules and finding
decisions too (``services.gates.current_spec_hash``), so an unchanged IR keeps its hash. A
domain the SME merged into another keeps sending its processes to that domain. After the
transaction the estate graph is also loaded into the knowledge store (schema ``knowledge``)
for dependency questions; a failure there is logged, not fatal.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from api.app.jobs.runner import JobContext
from api.app.models import (
    Application,
    AuditLog,
    Domain,
    File,
    GraphEdge,
    GraphNode,
    Process,
    ScanReport,
    Upload,
)
from api.app.services.gates import hash_decisions, hash_rules, load_findings, load_rules
from api.app.services.knowledge import Knowledge, KnowledgeUnavailable
from engine.domains.discover import discover_domains
from engine.domains.graph import build_graph
from engine.domains.models import Graph
from engine.ir.spec_hash import spec_hash
from engine.tibco_parser.scanner import ScanResult, scan_folder

SYSTEM = "system"


def upload_scan_root(upload: Upload) -> Path:
    root = Path(upload.root_dir)
    return root / upload.scan_root if upload.scan_root else root


def relativise(obj: Any, root: Path) -> Any:
    """``obj`` (JSON-like) with every occurrence of the scan root made relative to it.

    ``<root>/a/b`` becomes ``a/b`` wherever it appears in a string (paths, ``path#xpath``
    references, error texts); the bare root becomes ``.``.
    """
    prefixes = sorted({str(root), str(root.resolve())}, key=len, reverse=True)
    patterns = [(re.compile(re.escape(p) + "/"), "") for p in prefixes]  # <root>/x -> x
    patterns += [(re.compile(re.escape(p) + r"(?![\w.-])"), ".") for p in prefixes]  # <root> -> .

    def fix(text: str) -> str:
        if not any(p in text for p in prefixes):
            return text
        for pattern, repl in patterns:
            text = pattern.sub(repl, text)
        return text

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            return fix(value)
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    return walk(obj)


def _audit(project_id: str, process_id: str | None, action: str, detail: dict[str, Any]) -> AuditLog:
    return AuditLog(project_id=project_id, process_id=process_id, actor=SYSTEM, actor_kind=SYSTEM,
                    action=action, detail=detail)


async def run_scan(ctx: JobContext, project_id: str, upload_id: str,
                   knowledge: Knowledge | None = None) -> dict[str, Any]:
    async with ctx.sessionmaker() as s:
        upload = await s.get(Upload, upload_id)
        if upload is None or upload.project_id != project_id:
            raise ValueError(f"upload {upload_id} not found in project {project_id}")
        root = upload_scan_root(upload)

    await ctx.stage("scan")
    await ctx.log(f"scanning {upload.file_count} files in upload {upload_id}")
    result: ScanResult = await asyncio.to_thread(scan_folder, root)
    report = result.report
    await ctx.log(f"parsed {report.files_parsed} of {report.files_found} files; "
                  f"{len(report.failures)} failures, {len(report.unknown_activity_types)} unknown "
                  f"activity types, {len(report.unknown_elements)} unknown elements")
    for app in result.applications:
        await ctx.log(f"application {app.id} ({app.dialect}): {len(app.processes)} processes, "
                      f"modules {', '.join(app.modules) or '-'}")
    if result.synthetic:
        await ctx.log("SYNTHETIC.txt found: this estate is synthetic")

    await ctx.stage("discover")
    domains = await asyncio.to_thread(discover_domains, result.applications)
    graph = await asyncio.to_thread(build_graph, result.applications, domains)
    await ctx.log(f"{len(domains)} candidate domains; graph of {len(graph.nodes)} nodes and "
                  f"{len(graph.edges)} edges")

    await ctx.stage("persist")
    counts = await _persist(ctx, project_id, upload_id, root, result, domains, graph)
    await ctx.log(f"stored {len(result.applications)} applications, {counts['processes']} processes "
                  f"({counts['added']} new, {counts['changed']} with a new spec hash, "
                  f"{counts['removed']} removed), {len(domains)} domains")
    if knowledge is not None:
        try:
            await knowledge.load_graph(project_id, Graph.model_validate(relativise(
                graph.model_dump(mode="json"), root)))
            await ctx.log("estate graph loaded into the knowledge store")
        except KnowledgeUnavailable as exc:
            await ctx.log(f"knowledge store not updated: {exc}")
    return {"upload_id": upload_id, "applications": len(result.applications), "domains": len(domains),
            "synthetic": result.synthetic, "ok": report.ok, "files_found": report.files_found,
            "files_parsed": report.files_parsed, **counts}


async def _persist(ctx: JobContext, project_id: str, upload_id: str, root: Path, result: ScanResult,
                   domains: list, graph: Any) -> dict[str, int]:
    added = changed = removed = total = 0
    async with ctx.sessionmaker() as s:
        # domains: upsert by key, keep an SME rename
        old_domains = {d.key: d for d in (await s.execute(
            select(Domain).where(Domain.project_id == project_id))).scalars()}
        domain_rows: dict[str, Domain] = {}
        domain_of: dict[str, str] = {}
        for d in domains:
            evidence = relativise([e.model_dump(mode="json") for e in d.evidence], root)
            row = old_domains.pop(d.id, None)
            if row is None:
                row = Domain(project_id=project_id, key=d.id, name=d.suggested_name)
                s.add(row)
            elif row.renamed_by is None:
                row.name = d.suggested_name
            row.suggested_name = d.suggested_name
            row.evidence = evidence
            row.process_ids = list(d.process_ids)
            domain_rows[d.id] = row
            for pid in d.process_ids:
                domain_of[pid] = d.id
        for gone in old_domains.values():
            await s.delete(gone)  # processes.domain_id is ON DELETE SET NULL
        await s.flush()
        live_domain_ids = {r.id for r in domain_rows.values()}

        old_apps = {a.app_key: a for a in (await s.execute(
            select(Application).where(Application.project_id == project_id))).scalars()}
        old_procs = {p.ir_id: p for p in (await s.execute(
            select(Process).where(Process.project_id == project_id))).scalars()}
        seen_apps: set[str] = set()
        for app in result.applications:
            app_ir = relativise(app.model_dump(mode="json", exclude={"processes"}), root)
            app_row = old_apps.get(app.id)
            if app_row is None:
                app_row = Application(project_id=project_id, app_key=app.id)
                s.add(app_row)
            app_row.upload_id = upload_id
            app_row.name = app.name
            app_row.dialect = app.dialect
            app_row.modules = list(app.modules)
            app_row.ir = app_ir
            seen_apps.add(app.id)
            await s.flush()

            for p in app.processes:
                ir = relativise(p.model_dump(mode="json"), root)
                dom = domain_rows.get(domain_of.get(p.id, ""))
                if dom is not None and dom.merged_into in live_domain_ids:
                    dom_id = dom.merged_into
                else:
                    dom_id = dom.id if dom else None
                row = old_procs.pop(p.id, None)
                if row is None:
                    new_hash = spec_hash(ir, [], [])
                else:
                    new_hash = spec_hash(ir, hash_rules(await load_rules(s, row.id)),
                                         hash_decisions(await load_findings(s, row.id)))
                if row is None:
                    row = Process(project_id=project_id, ir_id=p.id)
                    s.add(row)
                    added += 1
                elif row.spec_hash != new_hash:
                    changed += 1
                    s.add(_audit(project_id, row.id, f"rescanned: spec hash changed {row.spec_hash} -> {new_hash}",
                                 {"ir_id": p.id, "old": row.spec_hash, "new": new_hash, "job_id": ctx.job_id}))
                row.application_id = app_row.id
                row.upload_id = upload_id
                row.domain_id = dom_id
                row.name = p.name
                row.module = p.module
                row.package = p.package
                row.dialect = p.dialect
                row.source_path = ir["source_path"]
                row.starter_kind = p.starter.kind if p.starter else None
                row.ir = ir
                row.spec_hash = new_hash
                row.complexity = ir.get("complexity")
                row.complexity_score = p.complexity.score if p.complexity else None
                row.complexity_band = p.complexity.band if p.complexity else None
                total += 1

        for ir_id, row in old_procs.items():
            removed += 1
            s.add(_audit(project_id, row.id, f"rescanned: process removed {ir_id}",
                         {"ir_id": ir_id, "spec_hash": row.spec_hash, "job_id": ctx.job_id}))
            await s.delete(row)
        await s.flush()
        for key, app_row in old_apps.items():
            if key not in seen_apps:
                await s.delete(app_row)

        await s.execute(delete(GraphNode).where(GraphNode.project_id == project_id))
        await s.execute(delete(GraphEdge).where(GraphEdge.project_id == project_id))
        g = relativise(graph.model_dump(mode="json"), root)
        s.add_all(GraphNode(project_id=project_id, node_id=n["id"], kind=n["kind"], name=n["name"],
                            attrs=n["attrs"]) for n in g["nodes"])
        s.add_all(GraphEdge(project_id=project_id, src=e["src"], dst=e["dst"], kind=e["kind"],
                            attrs=e["attrs"]) for e in g["edges"])

        report = result.report
        s.add(ScanReport(project_id=project_id, upload_id=upload_id, job_id=ctx.job_id,
                         report=relativise(report.model_dump(mode="json"), root), ok=report.ok,
                         synthetic=result.synthetic, sql_files=list(result.sql_files),
                         files_found=report.files_found, files_parsed=report.files_parsed))

        files = {f.path: f for f in (await s.execute(select(File).where(File.upload_id == upload_id))).scalars()}
        for entry in result.files:
            row = files.get(entry.path)
            if row is None:
                continue
            row.kind = entry.kind
            row.dialect = entry.dialect
            row.duplicate_of = entry.duplicate_of
        await s.commit()
    return {"processes": total, "added": added, "changed": changed, "removed": removed}


def scan_job(project_id: str, upload_id: str, knowledge: Knowledge | None = None):
    async def run(ctx: JobContext) -> dict[str, Any]:
        return await run_scan(ctx, project_id, upload_id, knowledge)

    return run
