"""The scan job: parse an upload with the engine and persist everything tab 1 and tab 2 read.

``scan_folder`` runs in a thread. One transaction then replaces the project's previous scan
(applications, processes, domains, graph) with the new one, stores the completeness report and
updates the upload's ``files`` rows with the scanner's kind, dialect and ``duplicate_of``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from api.app.jobs.runner import JobContext
from api.app.models import (
    Application,
    Domain,
    File,
    GraphEdge,
    GraphNode,
    Process,
    ScanReport,
    Upload,
)
from engine.domains.discover import discover_domains
from engine.domains.graph import build_graph
from engine.ir.spec_hash import spec_hash
from engine.tibco_parser.scanner import ScanResult, scan_folder


def upload_scan_root(upload: Upload) -> Path:
    root = Path(upload.root_dir)
    return root / upload.scan_root if upload.scan_root else root


def relative_source(source_path: str, scan_root: Path) -> str:
    """The IR's source path relative to the scan root (posix); unchanged if it is not under it."""
    p = Path(source_path)
    if not p.is_absolute():
        return p.as_posix()
    try:
        return p.resolve().relative_to(scan_root.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


async def run_scan(ctx: JobContext, project_id: str, upload_id: str) -> dict[str, Any]:
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
    process_count = 0
    async with ctx.sessionmaker() as s:
        for model in (Process, Application, Domain, GraphNode, GraphEdge):
            await s.execute(delete(model).where(model.project_id == project_id))

        domain_rows: dict[str, Domain] = {}
        domain_of: dict[str, str] = {}
        for d in domains:
            row = Domain(project_id=project_id, key=d.id, suggested_name=d.suggested_name,
                         name=d.suggested_name, evidence=[e.model_dump(mode="json") for e in d.evidence],
                         process_ids=list(d.process_ids))
            s.add(row)
            domain_rows[d.id] = row
            for pid in d.process_ids:
                domain_of[pid] = d.id
        await s.flush()

        for app in result.applications:
            app_row = Application(project_id=project_id, upload_id=upload_id, app_key=app.id,
                                  name=app.name, dialect=app.dialect, modules=list(app.modules),
                                  ir=app.model_dump(mode="json", exclude={"processes"}))
            s.add(app_row)
            await s.flush()
            for p in app.processes:
                ir = p.model_dump(mode="json")
                dom = domain_rows.get(domain_of.get(p.id, ""))
                s.add(Process(
                    project_id=project_id, application_id=app_row.id, upload_id=upload_id,
                    domain_id=dom.id if dom else None, ir_id=p.id, name=p.name, module=p.module,
                    package=p.package, dialect=p.dialect, source_path=relative_source(p.source_path, root),
                    starter_kind=p.starter.kind if p.starter else None, ir=ir,
                    spec_hash=spec_hash(ir, [], []),
                    complexity=p.complexity.model_dump(mode="json") if p.complexity else None,
                    complexity_score=p.complexity.score if p.complexity else None,
                    complexity_band=p.complexity.band if p.complexity else None))
                process_count += 1

        for n in graph.nodes:
            s.add(GraphNode(project_id=project_id, node_id=n.id, kind=n.kind, name=n.name,
                            attrs=n.model_dump(mode="json")["attrs"]))
        for e in graph.edges:
            s.add(GraphEdge(project_id=project_id, src=e.src, dst=e.dst, kind=e.kind,
                            attrs=e.model_dump(mode="json")["attrs"]))

        s.add(ScanReport(project_id=project_id, upload_id=upload_id, job_id=ctx.job_id,
                         report=report.model_dump(mode="json"), ok=report.ok, synthetic=result.synthetic,
                         sql_files=list(result.sql_files), files_found=report.files_found,
                         files_parsed=report.files_parsed))

        files = {f.path: f for f in (await s.execute(select(File).where(File.upload_id == upload_id))).scalars()}
        for entry in result.files:
            row = files.get(entry.path)
            if row is None:
                continue
            row.kind = entry.kind
            row.dialect = entry.dialect
            row.duplicate_of = entry.duplicate_of
        await s.commit()

    await ctx.log(f"stored {len(result.applications)} applications, {process_count} processes, "
                  f"{len(domains)} domains")
    return {"upload_id": upload_id, "applications": len(result.applications), "processes": process_count,
            "domains": len(domains), "synthetic": result.synthetic, "ok": report.ok,
            "files_found": report.files_found, "files_parsed": report.files_parsed}


def scan_job(project_id: str, upload_id: str):
    async def run(ctx: JobContext) -> dict[str, Any]:
        return await run_scan(ctx, project_id, upload_id)

    return run
