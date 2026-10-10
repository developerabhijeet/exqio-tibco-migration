"""Readiness (design spec 2.10): the SME-review expectations, each with a status computed from the
presence of its evidence in the database, links to that evidence and the gap when it is not met.

Status: ``met`` (the evidence exists and says what the expectation asks), ``partial`` (evidence
exists but falls short, the gap says how) or ``missing`` (no evidence yet).
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import Settings
from api.app.models import (
    Build,
    Domain,
    InfraAsset,
    Job,
    Metric,
    ModelCall,
    Process,
    ScanReport,
    Scenario,
    ScenarioRun,
    TestResult,
)
from api.app.services.knowledge import Knowledge

EXPECTATIONS: list[tuple[str, str]] = [
    ("synthetic_estate", "Synthetic estate"),
    ("domain_entry_point", "Domain entry point"),
    ("process_canvas", "Process canvas"),
    ("compilable_dotnet", "Compilable .NET"),
    ("metrics_on_screen", "Metrics on screen"),
    ("functional_tests", "Functional tests"),
    ("swagger_parity", "Swagger: same inputs, same outputs"),
    ("azure_assets", "Azure deployment assets"),
    ("provider_choice", "Provider choice"),
    ("routing", "Routing"),
    ("knowledge_layer", "Knowledge layer"),
    ("test_data", "Test data"),
    ("tokenomics", "Tokenomics"),
]


def _ev(kind: str, ref: str, label: str) -> dict[str, str]:
    return {"kind": kind, "ref": ref, "label": label}


def _row(key: str, status: str, evidence: list[dict[str, str]], gap: str | None) -> dict[str, Any]:
    label = dict(EXPECTATIONS)[key]
    return {"key": key, "expectation": label, "status": status, "evidence": evidence,
            "gap": None if status == "met" else gap}


async def _knowledge_counts(knowledge: Knowledge, project_id: str) -> tuple[int, int] | str:
    def count() -> tuple[int, int]:
        chunks = knowledge.query("SELECT count(*) AS n FROM knowledge.chunks WHERE project_id = %s", [project_id])
        memory = knowledge.query("SELECT count(*) AS n FROM knowledge.memory_items WHERE project_id = %s",
                                 [project_id])
        return int(chunks[0]["n"]), int(memory[0]["n"])

    try:
        return await asyncio.to_thread(count)
    except Exception as exc:  # noqa: BLE001  report it as the gap, never fail the page
        return f"{type(exc).__name__}: {exc}"


async def readiness(session: AsyncSession, settings: Settings, knowledge: Knowledge,
                    project_id: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    procs = (await session.execute(select(Process).where(Process.project_id == project_id))).scalars().all()
    pids = [p.id for p in procs]
    report = await session.scalar(select(ScanReport).where(ScanReport.project_id == project_id)
                                  .order_by(ScanReport.created_at.desc()).limit(1))

    # synthetic estate
    if report is None:
        rows.append(_row("synthetic_estate", "missing", [], "no estate has been scanned"))
    else:
        ev = [_ev("report", f"/projects/{project_id}/report",
                  f"parse report: {report.files_parsed} of {report.files_found} files parsed, "
                  f"{len(procs)} processes" + (", labelled synthetic" if report.synthetic else ""))]
        if report.synthetic and report.ok:
            rows.append(_row("synthetic_estate", "met", ev, None))
        else:
            gap = ("the scanned estate is not the synthetic one" if not report.synthetic
                   else "the parse report is not clean (unknown or unexplained elements)")
            rows.append(_row("synthetic_estate", "partial", ev, gap))

    # domain entry point
    domains = (await session.execute(select(Domain).where(Domain.project_id == project_id,
                                                          Domain.merged_into.is_(None)))).scalars().all()
    if domains:
        rows.append(_row("domain_entry_point", "met",
                         [_ev("domains", f"/projects/{project_id}/domains",
                              f"{len(domains)} domains: {', '.join(sorted(d.name for d in domains))}"),
                          _ev("graph", f"/projects/{project_id}/graph", "domain graph")], None))
    else:
        rows.append(_row("domain_entry_point", "missing", [], "no domains discovered (scan the estate)"))

    # process canvas
    if procs:
        unknown = [p.name for p in procs if any(a.get("type") == "unknown"
                                                 for a in (p.ir or {}).get("activities") or [])]
        ev = [_ev("canvas", f"/processes/{p.id}/canvas", f"{p.name} canvas") for p in procs[:3]]
        if unknown:
            rows.append(_row("process_canvas", "partial", ev, f"unknown activity types in {', '.join(unknown)}"))
        else:
            rows.append(_row("process_canvas", "met", ev, None))
    else:
        rows.append(_row("process_canvas", "missing", [], "no processes parsed"))

    builds = (await session.execute(select(Build).where(Build.process_id.in_(pids) if pids else false())
                                    .order_by(Build.created_at.desc()))).scalars().all()
    finished = [b for b in builds if b.status in ("passed", "failed")]

    def step_ok(b: Build, name: str) -> bool:
        return any(s.get("name") == name and s.get("ok") for s in b.steps or [])

    # compilable .NET
    compiled = [b for b in finished if step_ok(b, "build")]
    if compiled:
        b = compiled[0]
        rows.append(_row("compilable_dotnet", "met", [_ev("build", f"/builds/{b.id}", "dotnet build log")], None))
    elif finished:
        b = finished[0]
        restore = next((s for s in b.steps or [] if s.get("name") == "restore"), {})
        why = (restore.get("log") or "").strip().splitlines()[:1]
        rows.append(_row("compilable_dotnet", "partial",
                         [_ev("build", f"/builds/{b.id}", "generated solution, build log"),
                          _ev("files", f"/builds/{b.id}/files", f"{b.id[:8]} solution files")],
                         "the solution is generated but did not compile here"
                         + (f": {why[0]}" if why else "")))
    else:
        rows.append(_row("compilable_dotnet", "missing", [], "no build has run"))

    # metrics on screen
    latest = finished[0] if finished else None
    metrics = (await session.execute(select(Metric).where(Metric.build_id == latest.id))).scalars().all() \
        if latest else []
    if metrics:
        missing = [m.name for m in metrics if m.value is None]
        ev = [_ev("metrics", f"/builds/{latest.id}", "metrics radar with formulas")]
        rows.append(_row("metrics_on_screen", "partial" if missing else "met", ev,
                         f"no value yet for {', '.join(missing)}" if missing else None))
    else:
        rows.append(_row("metrics_on_screen", "missing", [], "no build metrics yet"))

    # functional tests
    tests = (await session.execute(select(TestResult).where(TestResult.build_id == latest.id))).scalars().all() \
        if latest else []
    if tests:
        passed = sum(1 for t in tests if t.outcome == "passed")
        ev = [_ev("tests", f"/builds/{latest.id}/tests", f"{passed} of {len(tests)} tests passed")]
        if passed == len(tests):
            rows.append(_row("functional_tests", "met", ev, None))
        else:
            no_body = sum(1 for t in tests if not t.has_body)
            rows.append(_row("functional_tests", "partial", ev,
                             f"{len(tests) - passed} test(s) not passed or not run; {no_body} without a body"))
    else:
        rows.append(_row("functional_tests", "missing", [], "no tests generated yet"))

    # Swagger parity
    runs = (await session.execute(select(ScenarioRun).where(ScenarioRun.build_id.in_([b.id for b in builds]))
                                  )).scalars().all() if builds else []
    if runs:
        unexplained = sum(int((r.report or {}).get("unexplained") or 0) for r in runs)
        errors = sum(1 for r in runs if r.status == "error")
        b = next(b for b in builds if b.id == runs[0].build_id)
        ev = [_ev("parity", f"/builds/{b.id}/parity", f"{len(runs)} scenario run(s), source: TIBCO model")]
        if unexplained == 0 and errors == 0:
            rows.append(_row("swagger_parity", "met", ev, None))
        else:
            rows.append(_row("swagger_parity", "partial", ev,
                             f"{unexplained} unexplained difference(s), {errors} scenario(s) could not run"))
    else:
        scenarios = await session.scalar(select(func.count(Scenario.id)).where(Scenario.process_id.in_(pids))) \
            if pids else 0
        if scenarios:
            rows.append(_row("swagger_parity", "partial",
                             [_ev("scenarios", f"/processes/{pids[0]}/scenarios", f"{scenarios} scenario(s)")],
                             "scenarios exist but have not been run against a running service"))
        else:
            rows.append(_row("swagger_parity", "missing", [], "no scenario run yet"))

    # Azure deployment assets
    if latest is not None:
        assets = await session.scalar(select(func.count(InfraAsset.id)).where(InfraAsset.build_id == latest.id))
    else:
        assets = 0
    if assets:
        v = latest.infra_validation or {}
        ev = [_ev("infra", f"/builds/{latest.id}/infra", f"{assets} infrastructure file(s) with validation")]
        if v.get("ok"):
            rows.append(_row("azure_assets", "met", ev, None))
        else:
            gap = "validation did not fully pass"
            if v.get("registry_unreachable"):
                gap = ("terraform validate could not run: the provider registry is unreachable here; "
                       f"fmt {v.get('fmt_ok')}, terragrunt hcl fmt {v.get('hclfmt_ok')}")
            rows.append(_row("azure_assets", "partial", ev, gap))
    else:
        rows.append(_row("azure_assets", "missing", [], "no infrastructure generated yet"))

    # provider choice
    active = settings.resolved_provider()
    ev = [_ev("settings", "/settings", f"active provider: {active}")]
    if active in ("claude", "azure"):
        rows.append(_row("provider_choice", "met", ev, None))
    elif active == "fake":
        rows.append(_row("provider_choice", "partial", ev,
                         "the fake provider is active (scripted replies); configure Claude or Azure OpenAI"))
    else:
        rows.append(_row("provider_choice", "missing", ev, "no model provider configured"))

    # routing
    calls = (await session.execute(select(ModelCall).where(ModelCall.project_id == project_id))).scalars().all()
    tiers = sorted({c.tier for c in calls if c.tier})
    if calls and tiers:
        rows.append(_row("routing", "met", [_ev("history", f"/history?project_id={project_id}",
                                                f"{len(calls)} model call(s) routed to tier(s) {', '.join(tiers)}"),
                                            _ev("settings", "/settings", "routing bands")], None))
    elif active != "none":
        rows.append(_row("routing", "partial", [_ev("settings", "/settings", "routing bands")],
                         "routing is configured but no model call has been recorded for this project"))
    else:
        rows.append(_row("routing", "missing", [], "no provider, so nothing is routed"))

    # knowledge layer
    counts = await _knowledge_counts(knowledge, project_id)
    if isinstance(counts, str):
        rows.append(_row("knowledge_layer", "missing", [], f"knowledge store unavailable: {counts}"))
    else:
        chunks, memory = counts
        ev = [_ev("ask", "/ask", f"{chunks} indexed chunk(s), {memory} migration memory item(s)")]
        if chunks and memory:
            rows.append(_row("knowledge_layer", "met", ev, None))
        elif chunks or memory:
            rows.append(_row("knowledge_layer", "partial", ev,
                             "no analysed process indexed yet" if not chunks else "no migration memory yet"))
        else:
            rows.append(_row("knowledge_layer", "missing", ev, "nothing indexed for this project yet"))

    # test data
    with_scenarios = (await session.execute(select(Scenario.process_id).where(Scenario.process_id.in_(pids))
                                            .distinct())).scalars().all() if pids else []
    if with_scenarios:
        rows.append(_row("test_data", "met", [_ev("test-data", f"/processes/{pid}/test-data", "masked test rows")
                                              for pid in with_scenarios[:3]], None))
    else:
        rows.append(_row("test_data", "missing", [], "no scenarios generated yet"))

    # tokenomics
    jobs = await session.scalar(select(func.count(Job.id)).where(Job.project_id == project_id)) or 0
    priced = [c for c in calls if (c.tokens_in or c.tokens_out)]
    ev = [_ev("history", f"/history?project_id={project_id}", f"{jobs} job(s), {len(calls)} model call(s)")]
    if priced:
        rows.append(_row("tokenomics", "met", ev, None))
    elif jobs:
        rows.append(_row("tokenomics", "partial", ev, "jobs are recorded but no model call with tokens yet"))
    else:
        rows.append(_row("tokenomics", "missing", [], "nothing has run yet"))

    summary = {s: sum(1 for r in rows if r["status"] == s) for s in ("met", "partial", "missing")}
    return {"project_id": project_id, "rows": rows, "summary": summary}
