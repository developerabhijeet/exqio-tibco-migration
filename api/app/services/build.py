"""Tab 5, Build: the build job and its views (design spec 2.5, 7).

Order, each stage logged ``code`` or ``ai``:

1. design check: G3 approved and its design record present (else the job fails; the endpoint
   answers 409 before queueing);
2. generate: the solution, by code, from the IR and the approved design record, under
   ``DATA_DIR/builds/<build id>/``;
3. tests: written by the generator before any body (one per approved rule, branch and scenario);
   the static inventory says which have a body;
3b. test engineer (ai): writes the rule and branch test bodies the generator left as
   ``Assert.Fail("no test body ...")``; plain code checks each body (``check_test_body``); the
   inventory is recomputed after this stage and again after the repair loop, so ``has_body``
   reflects the final files; accepted bodies whose expected value appears nowhere in the rule,
   citation, branch or fixtures are kept as ``builder.test_engineer.warnings`` for a person;
4. builder (ai): the Code Builder fills the bodies code left (skipped, with a log line, without a
   model);
5. review (ai): equivalence, security and standards reviewers over the agent-written code, and
   the test reviewer over the rule and branch tests; it runs when the Code Builder filled a file
   or the Test Engineer wrote a test (then only the test reviewer runs: no production reviewer
   is asked about placeholder code). Test findings are stored with the others (reviewer
   ``test``);
6. restore, build, format, test: ``dotnet``; restore always runs, bounded to
   ``RESTORE_TIMEOUT_S``; a failure is annotated with the NuGet probe; a step that cannot run is
   recorded as failed with the reason (``dotnet`` absent, restore failed), never hidden;
7. repair (ai): only when restore and build succeeded and tests fail;
8. infra: Terraform and Terragrunt assets, Dockerfile and pipelines, then their validation;
9. metrics: the fixed formulas of spec 2.5;
10. persist: build files with origin and TIBCO lineage, test results, reviews, metrics, infra
    assets and the build hash; G4 to G6 reset when decided (a new build).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import Settings
from api.app.db import utcnow
from api.app.jobs.runner import JobContext
from api.app.models import Build, BuildFile, DesignRecord, InfraAsset, Metric, Process, TestResult
from api.app.services import evidence
from api.app.services import gates as gate_svc
from api.app.services.context import engine_app, engine_findings, engine_process, engine_rules
from api.app.services.design import ai_logger
from api.app.services.knowledge import Knowledge, KnowledgeUnavailable
from api.app.services.providers import build_provider, model_call_recorder
from engine.agents.builder import BuildRejection, fill_method_bodies
from engine.agents.jsonout import JsonOutputError
from engine.agents.provider import ProviderError
from engine.agents.reviewers import REVIEWERS, TEST_REVIEWER, review_panel, reviewed_test_files
from engine.agents.test_engineer import fill_test_bodies
from engine.dotnet_gen import runner as dotnet_runner
from engine.dotnet_gen.generator import (
    GeneratedSolution,
    GenFile,
    generate_solution,
    solution_layout,
)
from engine.dotnet_gen.metrics import compute_metrics
from engine.dotnet_gen.models import DesignRecord as EngineDesign
from engine.dotnet_gen.repair import RepairAttempt, repair_loop
from engine.dotnet_gen.runner import BuildResult, DotnetRunner, StepResult, TestRun
from engine.dotnet_gen.tests_gen import branch_transitions
from engine.dotnet_gen.trx import BRANCH_SUFFIX, RULES_SUFFIX, SCENARIO_SUFFIX, classify
from engine.infra_gen.generator import generate_infra
from engine.infra_gen.validators import validate_infra
from engine.parity.models import Scenario as EngineScenario

STAGES = ["design check", "generate", "tests", "test engineer", "builder", "review", "restore", "build", "format", "test",
          "repair", "infra", "infra validate", "metrics", "persist"]
RUNTIMES = ("container_app", "aks")
LOG_LIMIT = 20000
RESET_REASON = "new build"
NO_DOTNET = ("dotnet not found on PATH or in .tools: install the .NET 10 SDK (Settings shows the "
             "toolchains)")
RESTORE_TIMEOUT_S = 120
NUGET_DOWN = ("NuGet unreachable: api.nuget.org did not answer and there is no offline cache at "
              ".tools/nuget-cache, so `dotnet restore` of the Api, Infrastructure and Tests projects cannot "
              "succeed here. Run `make nuget-cache` on a machine with NuGet access and copy .tools/nuget-cache "
              "across.")
_FACT = re.compile(r'\[Fact\(DisplayName = (?P<lit>"(?:[^"\\]|\\.)*")\)\]')
_METHOD = re.compile(r"public (?:async )?Task (?P<name>\w+)\(\)")


class BuildRefused(Exception):
    """The build cannot start (HTTP 409): G3 not approved or no design record."""


def find_dotnet() -> str | None:
    """The ``dotnet`` binary the build uses (``scripts/toolchain.detect()``); tests monkeypatch it."""
    return dotnet_runner.find_dotnet()


def run_restore(root: Path, dotnet: str) -> StepResult:
    """``dotnet restore`` of the solution, bounded to ``RESTORE_TIMEOUT_S`` (tests monkeypatch it)."""
    return DotnetRunner(root, dotnet=dotnet, timeout_s=RESTORE_TIMEOUT_S).restore()


def nuget_available() -> bool:
    return nuget_probe()[0]


def nuget_probe(timeout_s: float = 5.0) -> tuple[bool, str]:
    """``(reachable, what was checked)``: the offline cache, else ``GET`` of the nuget.org index."""
    import urllib.error
    import urllib.request

    if os.environ.get("EXIQO_NUGET_CACHE") or dotnet_runner.NUGET_CACHE.is_dir():
        return True, f"offline cache {os.environ.get('EXIQO_NUGET_CACHE') or dotnet_runner.NUGET_CACHE}"
    url = "https://api.nuget.org/v3/index.json"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            return resp.status == 200, f"GET {url}: HTTP {resp.status}"
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        return False, f"GET {url} failed: {exc}"


def toolchain() -> dict[str, Any]:
    return dotnet_runner._toolchain_detect()


def _tail(text: str, limit: int = LOG_LIMIT) -> str:
    text = text or ""
    return text if len(text) <= limit else "..." + text[-limit:]


def _step(name: str, ok: bool, log: str, started: float, *, skipped: bool = False) -> dict[str, Any]:
    return {"name": name, "ok": ok, "skipped": skipped, "log": _tail(log),
            "duration_ms": int((time.monotonic() - started) * 1000)}


async def approved_design(session: AsyncSession, process: Process) -> DesignRecord:
    """The design record G3 approved (by its design hash); ``BuildRefused`` otherwise."""
    rows = await gate_svc.gate_rows(session, process.id)
    g3 = rows.get("G3")
    if g3 is None or g3.state != "approved":
        raise BuildRefused("G3 not approved: the architect must approve the design record first")
    q = select(DesignRecord).where(DesignRecord.process_id == process.id)
    if g3.design_hash:
        q = q.where(DesignRecord.design_hash == g3.design_hash)
    design = await session.scalar(q.order_by(DesignRecord.version.desc()).limit(1))
    if design is None:
        raise BuildRefused("the design record G3 approved is not stored")
    if design.spec_hash != process.spec_hash:
        raise BuildRefused("the approved design record is stale: the spec hash changed since G3")
    return design


def build_job(build_id: str, settings: Settings, knowledge: Knowledge, scenario_lock: asyncio.Lock):
    async def run(ctx: JobContext) -> dict[str, Any]:
        try:
            return await run_build(ctx, build_id, settings, knowledge, scenario_lock)
        except Exception as exc:
            async with ctx.sessionmaker() as s:
                b = await s.get(Build, build_id)
                if b is not None and b.status in ("queued", "running"):
                    b.status = "failed"
                    b.error = f"{type(exc).__name__}: {exc}"
                    b.finished_at = utcnow()
                    await s.commit()
            raise

    return run


def _apply(solution: GeneratedSolution, files: list[GenFile]) -> None:
    """Write agent-written files to disk and replace them in ``solution.files``."""
    by_path = {f.path: f for f in files}
    for f in files:
        (solution.root / f.path).write_text(f.content, encoding="utf-8", newline="\n")
    solution.files = [by_path.get(f.path, f) for f in solution.files]


# ------------------------------------------------------------------ static test inventory
def _cs_literal(lit: str) -> str:
    try:
        return json.loads(lit)
    except ValueError:
        return lit.strip('"')


def test_inventory(files: list[GenFile], app_name: str) -> list[dict[str, Any]]:
    """Every generated ``[Fact]`` with its rule, branch or scenario id and whether it has a body."""
    out: list[dict[str, Any]] = []
    for f in files:
        name = Path(f.path).stem
        if not f.path.startswith("tests/") or not name.endswith((RULES_SUFFIX, BRANCH_SUFFIX, SCENARIO_SUFFIX)):
            continue
        text = f.content
        for m in _FACT.finditer(text):
            mm = _METHOD.search(text, m.end())
            if mm is None:
                continue
            end = text.find("\n    }", mm.end())
            body = text[mm.end(): end if end != -1 else len(text)]
            display = _cs_literal(m.group("lit"))
            rule_id, branch_id, scenario_id = classify(f"{app_name}.Tests.{name}", mm.group("name"), display)
            kind = "rule" if rule_id else "branch" if branch_id else "scenario" if scenario_id else "other"
            out.append({"name": f"{app_name}.Tests.{name}.{mm.group('name')}", "display_name": display,
                        "kind": kind, "rule_key": rule_id, "ref": branch_id or scenario_id, "file": f.path,
                        "has_body": "no test body" not in body, "outcome": "not_run", "message": None,
                        "duration_ms": None})
    return out


def merge_results(inventory: list[dict[str, Any]], run: TestRun | None) -> list[dict[str, Any]]:
    if run is None:
        return inventory
    by_name = {c.name: c for c in run.cases}
    out = []
    for t in inventory:
        c = by_name.pop(t["name"], None)
        if c is not None:
            t = {**t, "outcome": c.outcome, "message": c.message or None, "duration_ms": c.duration_ms,
                 "has_body": t["has_body"] and "no test body" not in (c.message or "")}
        out.append(t)
    for c in by_name.values():
        kind = "rule" if c.rule_id else "branch" if c.branch_id else "scenario" if c.scenario_id else "other"
        out.append({"name": c.name, "display_name": c.display_name, "kind": kind, "rule_key": c.rule_id,
                    "ref": c.branch_id or c.scenario_id, "file": None,
                    "has_body": "no test body" not in (c.message or ""), "outcome": c.outcome,
                    "message": c.message or None, "duration_ms": c.duration_ms})
    return out


# ------------------------------------------------------------------ file lineage
def tibco_ref(f: GenFile, design: EngineDesign, layout: dict[str, Any], ir: dict[str, Any],
              source_path: str) -> dict[str, Any] | None:
    folders = {v["name"]: v["folder"] for v in layout["projects"].values()}
    rows = [r for r in design.mapping if f"{folders.get(r.project, r.project)}/{r.file}" == f.path]
    acts = {a.get("id"): a for a in ir.get("activities") or []}
    ids = sorted({r.activity_id for r in rows} | ({f.activity_id} if f.activity_id else set()))
    if not ids and f.process_id is None:
        return None
    return {"process_id": f.process_id, "source_path": source_path, "activity_ids": ids,
            "source_lines": {i: (acts.get(i) or {}).get("source_line") for i in ids if i in acts},
            "rows": [{"activity_id": r.activity_id, "activity_name": r.activity_name, "pattern": r.pattern,
                      "class_name": r.class_name, "method": r.method, "rule_ids": r.rule_ids,
                      "finding_ids": r.finding_ids, "origin": r.origin} for r in rows]}


def infra_kind(path: str) -> str:
    if path.endswith(".tf"):
        return "terraform"
    if path.endswith(".hcl"):
        return "terragrunt"
    if Path(path).name in ("Dockerfile", ".dockerignore"):
        return "dockerfile"
    if path.startswith(".github/") or path.endswith("azure-pipelines.yml"):
        return "pipeline"
    return "other"


# ------------------------------------------------------------------ the job
async def run_build(ctx: JobContext, build_id: str, settings: Settings, knowledge: Knowledge,
                    scenario_lock: asyncio.Lock) -> dict[str, Any]:
    from api.app.services import verify as verify_svc

    async with ctx.sessionmaker() as s:
        build = await s.get(Build, build_id)
        if build is None:
            raise ValueError(f"build {build_id} not found")
        build.status = "running"
        await s.commit()
        row = await s.get(Process, build.process_id)
        runtime = build.runtime
        project_id = row.project_id
        process_id = row.id
        await ctx.stage(STAGES[0])
        design_row = await approved_design(s, row)
        design = EngineDesign.model_validate(design_row.record)
        await ctx.log(f"G3 approved; design record v{design_row.version} (design hash "
                      f"{design_row.design_hash[:12]}, spec hash {design_row.spec_hash[:12]})")
        app = await engine_app(s, row)
        proc = engine_process(app, row)
        rules = await engine_rules(s, process_id)
        findings = await engine_findings(s, process_id)
        build.design_id = design_row.id
        build.spec_hash = row.spec_hash
        build.design_hash = design_row.design_hash
        await s.commit()
        try:
            srows, notes, fresh = await verify_svc.ensure_scenarios(s, settings, row, scenario_lock)
            scenarios = [EngineScenario.model_validate(r.data) for r in srows]
            scenario_note = (f"{len(scenarios)} parity scenario(s) "
                             f"{'generated' if fresh else 'from the cache'} for the scenario tests")
        except verify_svc.ScenarioDBUnavailable as exc:
            scenarios, notes = [], []
            scenario_note = f"no parity scenarios (scenario database unavailable: {exc}); scenario tests are empty"
    out_dir = settings.builds_dir / build_id
    approved_rules = [r for r in rules if r.status in ("approved", "edited")]

    # 2. generate (code)
    await ctx.stage(STAGES[1])
    await ctx.log(scenario_note)
    for n in notes:
        await ctx.log(n)
    solution: GeneratedSolution = await asyncio.to_thread(
        generate_solution, app, [proc.id], {proc.id: design}, {proc.id: rules}, {proc.id: findings},
        scenarios, out_dir)
    agent_files = [f for f in solution.files if f.origin == "agent" and not f.path.startswith("tests/")]
    await ctx.log(f"solution {solution.name}: {len(solution.files)} file(s) in {out_dir}; "
                  f"{len(agent_files)} production file(s) with bodies left to the Code Builder")

    # 3. tests first (code)
    await ctx.stage(STAGES[2])
    inventory = test_inventory(solution.files, solution.name)
    counts = {k: sum(1 for t in inventory if t["kind"] == k) for k in ("rule", "branch", "scenario")}
    no_body = [t for t in inventory if not t["has_body"]]
    await ctx.log(f"tests written before any body: {counts['rule']} rule, {counts['branch']} branch, "
                  f"{counts['scenario']} scenario test(s); {len(no_body)} without a body"
                  + (f" ({', '.join(sorted({t['rule_key'] or t['ref'] or t['name'] for t in no_body})[:8])})"
                     if no_body else ""))

    # provider for the ai stages
    choice = build_provider(settings, process=proc)
    provider = choice.provider
    if provider is not None:
        provider.recorder = model_call_recorder(
            ctx.sessionmaker, job_id=ctx.job_id, project_id=project_id, process_id=process_id,
            primary_name=provider.primary.name, on_record=ai_logger(ctx))

    # 3b. test engineer (ai)
    await ctx.stage(STAGES[3])
    te_rejections: list[BuildRejection] = []
    te_warnings: list[dict[str, str]] = []
    te_filled: list[str] = []
    placeholders = [t for t in inventory if not t["has_body"] and t["kind"] in ("rule", "branch")]
    if provider is None:
        await ctx.log("skipped: no model configured, the rule and branch tests keep their placeholders", tag="ai")
    elif not placeholders:
        await ctx.log("nothing to write: every rule and branch test already has a body", tag="ai")
    else:
        try:
            te_memory = await knowledge.retrieve_memory(project_id, f"{proc.name} tests")
        except KnowledgeUnavailable as exc:
            te_memory = []
            await ctx.log(f"migration memory unavailable: {exc}")
        try:
            written = await fill_test_bodies(solution, proc, approved_rules, scenarios, design, te_memory,
                                             provider, te_rejections, warnings=te_warnings)
        except (ProviderError, JsonOutputError) as exc:
            written = []
            await ctx.log(f"Test Engineer failed: {exc}", tag="ai")
        _apply(solution, written)
        te_filled = sorted(f.path for f in written)
        inventory = test_inventory(solution.files, solution.name)
        still = [t for t in inventory if not t["has_body"] and t["kind"] in ("rule", "branch")]
        await ctx.log(f"Test Engineer wrote {len(placeholders) - len(still)} of {len(placeholders)} rule and branch "
                      f"test bodies in {len(te_filled)} file(s); {len(te_rejections)} body(ies) rejected by the "
                      "code checks", tag="ai")
        for r in te_rejections:
            await ctx.log(f"rejected {r.path} (attempt {r.attempt}): {r.reason}", tag="ai")
        await ctx.log(f"Test Engineer: {len(te_warnings)} expected-value warning(s) for a person to check",
                      tag="ai")

    # 4. builder (ai)
    await ctx.stage(STAGES[4])
    filled_paths: list[str] = []
    rejections: list[BuildRejection] = []
    if provider is None:
        await ctx.log("skipped: no model configured, the bodies stay as placeholders", tag="ai")
    elif not agent_files:
        await ctx.log("nothing to fill: every body was generated by code", tag="ai")
    else:
        memory_items: list[Any] = []
        try:
            memory_items = await knowledge.retrieve_memory(project_id, f"{proc.name} mapping patterns")
        except KnowledgeUnavailable as exc:
            await ctx.log(f"migration memory unavailable: {exc}")
        try:
            filled = await fill_method_bodies(solution, design, proc, approved_rules, memory_items, provider,
                                              rejections)
        except (ProviderError, JsonOutputError) as exc:
            filled = []
            await ctx.log(f"Code Builder failed: {exc}", tag="ai")
        _apply(solution, filled)
        filled_paths = sorted(f.path for f in filled)
        await ctx.log(f"Code Builder filled {len(filled_paths)} file(s); {len(rejections)} reply(ies) rejected "
                      "by the code checks", tag="ai")
        for r in rejections:
            await ctx.log(f"rejected {r.path} (attempt {r.attempt}): {r.reason}", tag="ai")

    # 5. review panel (ai)
    await ctx.stage(STAGES[5])
    reviews: list[dict[str, Any]] = []
    reviewers_run: list[str] = []
    if provider is None:
        await ctx.log("skipped: no model configured", tag="ai")
    elif not filled_paths and not te_filled:
        await ctx.log("skipped: no agent-written code or tests to review", tag="ai")
    else:
        # without code the builder filled there is nothing for the production reviewers
        panel = [*REVIEWERS, TEST_REVIEWER] if filled_paths else [TEST_REVIEWER]
        if not filled_paths:
            await ctx.log("no agent-written production code: only the test reviewer runs, over the rule and "
                          "branch tests", tag="ai")
        try:
            found = await review_panel(solution, design, approved_rules, findings, provider, process=proc,
                                       reviewers=panel)
            reviewers_run = ([*REVIEWERS] if filled_paths else []) + (
                [TEST_REVIEWER] if reviewed_test_files(solution, proc) else [])
            reviews = [r.model_dump() for r in found]
            triage = {t: sum(1 for r in found if r.triage == t) for t in ("patch", "defer", "decision")}
            tested = sum(1 for r in found if r.reviewer == TEST_REVIEWER)
            await ctx.log(f"review panel ({', '.join(reviewers_run) or 'no reviewer had files'}): {len(found)} "
                          f"finding(s), {tested} on tests: {triage['patch']} patch, {triage['defer']} defer, "
                          f"{triage['decision']} decision needed", tag="ai")
        except (ProviderError, JsonOutputError) as exc:
            await ctx.log(f"review panel failed: {exc}", tag="ai")

    # 6. restore, build, format, test (code)
    steps: list[dict[str, Any]] = []
    dotnet = find_dotnet()
    runner = DotnetRunner(solution.root, dotnet=dotnet) if dotnet else None
    build_result: BuildResult | None = None
    test_run: TestRun | None = None
    format_ok = False

    await ctx.stage(STAGES[6])
    t0 = time.monotonic()
    if runner is None:
        restore = StepResult(ok=False, log=NO_DOTNET)
    else:
        restore = await asyncio.to_thread(run_restore, solution.root, dotnet)
        if not restore.ok:
            reachable, probe = await asyncio.to_thread(nuget_probe)
            note = NUGET_DOWN if (not reachable or dotnet_runner.nuget_unreachable(restore.log)) else \
                "restore failed with the package source reachable: see the log above"
            restore = StepResult(ok=False, log=f"{restore.log.rstrip()}\n\n{note}\nprobe: {probe}")
    steps.append(_step("restore", restore.ok, restore.log, t0))
    await ctx.log("restore " + ("passed" if restore.ok else f"failed: {restore.log.strip().splitlines()[-1]}"))

    await ctx.stage(STAGES[7])
    t0 = time.monotonic()
    if runner is not None and restore.ok:
        build_result = await asyncio.to_thread(runner.build)
        steps.append(_step("build", build_result.ok, build_result.log, t0))
        await ctx.log(f"build {'passed' if build_result.ok else 'failed'}: {build_result.warnings} warning(s), "
                      f"{len(build_result.errors)} error(s)")
    else:
        why = NO_DOTNET if runner is None else "not run: restore failed"
        steps.append(_step("build", False, why, t0, skipped=True))
        await ctx.log(f"build {why}")

    await ctx.stage(STAGES[8])
    t0 = time.monotonic()
    if runner is None:
        steps.append(_step("format", False, NO_DOTNET, t0, skipped=True))
        await ctx.log(f"format {NO_DOTNET}")
    else:
        whitespace = not restore.ok
        fmt = await asyncio.to_thread(runner.format_check, whitespace)
        format_ok = fmt.ok
        mode = "dotnet format whitespace --folder (no restore needed)" if whitespace else "dotnet format"
        log = f"{mode} --verify-no-changes: {'passed' if fmt.ok else 'failed'}\n{fmt.log}"
        steps.append(_step("format", fmt.ok, log, t0))
        await ctx.log(f"{mode}: {'passed' if fmt.ok else 'failed'}")

    await ctx.stage(STAGES[9])
    t0 = time.monotonic()
    if runner is not None and build_result is not None and build_result.ok:
        test_run = await asyncio.to_thread(runner.test)
        passed = sum(1 for c in test_run.cases if c.outcome == "passed")
        steps.append(_step("test", test_run.ok, test_run.log, t0))
        await ctx.log(f"tests: {passed} of {len(test_run.cases)} passed"
                      + (f", line coverage {test_run.coverage_line:.1%}" if test_run.coverage_line is not None else ""))
    else:
        why = NO_DOTNET if runner is None else "not run: the solution did not compile"
        steps.append(_step("test", False, why, t0, skipped=True))
        await ctx.log(f"tests {why}")

    # 7. repair loop (ai)
    await ctx.stage(STAGES[10])
    repair: dict[str, Any] | None = None
    if runner is None or not restore.ok or build_result is None or not build_result.ok:
        await ctx.log("repair loop not run: it needs a restored, compiling solution", tag="ai")
    elif test_run is not None and test_run.ok:
        await ctx.log("repair loop not needed: every test passed", tag="ai")
    elif provider is None:
        await ctx.log("repair loop skipped: no model configured", tag="ai")
    else:
        async def on_attempt(a: RepairAttempt) -> None:
            await ctx.log(f"repair attempt {a.n}: {'applied' if a.applied else 'rejected'}"
                          + (f": {a.reason}" if a.reason else "") + f"; failing: {', '.join(a.failing[:5])}",
                          tag="ai")

        result = await repair_loop(runner, solution, proc, approved_rules, provider, recorder=on_attempt)
        repair = result.model_dump(mode="json")
        test_run = result.final
        await ctx.log(f"repair loop: {len(result.attempts)} attempt(s); tests now "
                      f"{'pass' if result.final.ok else 'still fail'}", tag="ai")
    inventory = test_inventory(solution.files, solution.name)  # has_body from the final files

    # 8. infrastructure (code)
    await ctx.stage(STAGES[11])
    infra_files = await asyncio.to_thread(generate_infra, app, design, solution.root, solution.name,
                                          runtime=runtime)
    await ctx.log(f"{len(infra_files)} infrastructure file(s): Terraform modules, Terragrunt live config "
                  f"({runtime}), Dockerfile, pipelines")
    await ctx.stage(STAGES[12])
    tools = await asyncio.to_thread(toolchain)
    validation = await asyncio.to_thread(validate_infra, solution.root, (tools.get("terraform") or {}).get("path"),
                                         (tools.get("terragrunt") or {}).get("path"))
    await ctx.log(f"terraform fmt -check: {validation.fmt_ok}; module validate: "
                  f"{sum(1 for m in validation.modules if m.validate_ok)} of {len(validation.modules)}; "
                  f"terragrunt hcl fmt --check: {validation.hclfmt_ok}; ok: {validation.ok}")
    for note in validation.notes:
        await ctx.log(note)

    # 9. metrics (code)
    await ctx.stage(STAGES[13])
    br = build_result or BuildResult(ok=False, files=sum(1 for f in solution.files if f.path.endswith(".cs")),
                                     log="not built")
    tr = test_run or TestRun(ok=False, log="not run")
    infra_ok = validation.ok if validation.tools_present and any(validation.tools_present.values()) else None
    metrics = compute_metrics(rules, len(branch_transitions(proc)), tr, br, format_ok, infra_ok, process=proc.id)
    if runner is not None and not restore.ok:
        metrics.details["standards_adherence"] += (" (reduced check: dotnet format whitespace --folder only, "
                                                   "because restore failed; analyzers and style rules were not run)")
    await ctx.log("metrics: " + "; ".join(f"{k} {v}" for k, v in metrics.details.items()))

    # 10. persist (code)
    await ctx.stage(STAGES[14])
    tests = merge_results(inventory, test_run)
    layout = solution_layout(solution.name)
    all_files: list[tuple[GenFile, str]] = []
    for f in solution.files:
        origin = "ai" if f.path in filled_paths or f.path in te_filled else f.origin
        all_files.append((f, origin))
    for f in infra_files:
        all_files.append((GenFile(path=f.path, content=f.content), "infra"))
    digest = hashlib.sha256()
    file_rows = []
    for f, origin in sorted(all_files, key=lambda x: x[0].path):
        raw = f.content.encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        digest.update(f.path.encode("utf-8") + b"\0" + sha.encode("ascii") + b"\0")
        file_rows.append((f, origin, sha, len(raw)))
    build_hash = digest.hexdigest()
    compile_ok = build_result is not None and build_result.ok
    tests_ok = bool(tests) and all(t["outcome"] == "passed" for t in tests)
    status = "passed" if compile_ok and tests_ok else "failed"
    async with ctx.sessionmaker() as s:
        b = await s.get(Build, build_id)
        process = await s.get(Process, process_id)
        for f, origin, sha, size in file_rows:
            ref = None if origin == "infra" else tibco_ref(f, design, layout, process.ir, process.source_path)
            s.add(BuildFile(build_id=build_id, path=f.path, origin=origin, sha256=sha, size=size,
                            process_id=f.process_id, tibco_ref=ref))
        for t in tests:
            s.add(TestResult(build_id=build_id, name=t["name"], display_name=t["display_name"], kind=t["kind"],
                             rule_key=t["rule_key"], ref=t["ref"], file=t["file"], outcome=t["outcome"],
                             duration_ms=t["duration_ms"], message=t["message"], has_body=t["has_body"]))
        values = metrics.model_dump()
        for name in ("rule_carry_forward", "test_pass_rate", "branch_coverage", "code_coverage",
                     "standards_adherence", "compile_ok", "warnings", "infra_ok"):
            v = values[name]
            unit = "bool" if isinstance(v, bool) or name in ("compile_ok", "infra_ok") else (
                "count" if name == "warnings" else "ratio")
            detail_key = {"compile_ok": "compile", "infra_ok": "infra_validity", "warnings": "compile"}.get(name, name)
            s.add(Metric(build_id=build_id, name=name, value=None if v is None else float(v), unit=unit,
                         data={"formula": metrics.formulas.get(detail_key), "detail": metrics.details.get(detail_key)}))
        vjson = validation.model_dump(mode="json")
        for f in infra_files:
            s.add(InfraAsset(build_id=build_id, path=f.path, kind=infra_kind(f.path),
                             sha256=hashlib.sha256(f.content.encode("utf-8")).hexdigest(),
                             validation=_asset_validation(f.path, vjson)))
        b.steps = steps
        b.reviews = reviews
        b.repair = repair
        b.builder = {"filled": filled_paths, "rejections": [r.model_dump() if hasattr(r, "model_dump") else
                                                            {"path": r.path, "attempt": r.attempt, "reason": r.reason}
                                                            for r in rejections],
                     "provider": choice.name, "ai_available": provider is not None,
                     "reviewers": reviewers_run,
                     "test_engineer": {"filled": te_filled, "warnings": te_warnings, "rejections": [
                         {"path": r.path, "attempt": r.attempt, "reason": r.reason} for r in te_rejections]}}
        b.infra_validation = vjson
        b.output_dir = str(solution.root)
        b.build_hash = build_hash
        b.status = status
        b.finished_at = utcnow()
        rows = await gate_svc.gate_rows(s, process_id)
        reset = await gate_svc.reset_gates(s, process, rows, "G4", RESET_REASON, gate_svc.SYSTEM,
                                           gate_svc.SYSTEM, {"build_id": build_id, "build_hash": build_hash})
        gate_svc.audit(s, process, gate_svc.SYSTEM, "build finished", actor_kind=gate_svc.SYSTEM,
                       detail={"build_id": build_id, "status": status, "build_hash": build_hash})
        await s.commit()
    await ctx.log(f"stored {len(file_rows)} file(s), {len(tests)} test result(s), {len(reviews)} review "
                  f"finding(s); build hash {build_hash[:12]}; status {status}"
                  + (f"; gates reset: {', '.join(reset)}" if reset else ""))
    return {"build_id": build_id, "status": status, "build_hash": build_hash, "compile_ok": compile_ok,
            "tests": len(tests), "tests_passed": sum(1 for t in tests if t["outcome"] == "passed"),
            "restore_ok": restore.ok, "infra_ok": validation.ok, "gates_reset": reset,
            "provider": choice.name}


def _asset_validation(path: str, vjson: dict[str, Any]) -> dict[str, Any]:
    """The part of the validation that concerns one asset (its module, or the formatting checks)."""
    m = re.match(r"infra/modules/([^/]+)/", path)
    if m:
        module = next((x for x in vjson.get("modules") or [] if x.get("name") == m.group(1)), None)
        return {"fmt_ok": vjson.get("fmt_ok"), "module": module}
    if path.startswith("infra/live/"):
        return {"hclfmt_ok": vjson.get("hclfmt_ok")}
    return {}


# ------------------------------------------------------------------ views
def metrics_view(rows: list[Metric]) -> dict[str, Any] | None:
    if not rows:
        return None
    out: dict[str, Any] = {"formulas": {}, "details": {}}
    for m in rows:
        if m.unit == "bool":
            out[m.name] = None if m.value is None else bool(m.value)
        elif m.unit == "count":
            out[m.name] = None if m.value is None else int(m.value)
        else:
            out[m.name] = m.value
        out["formulas"][m.name] = (m.data or {}).get("formula")
        out["details"][m.name] = (m.data or {}).get("detail")
    return out


async def build_view(session: AsyncSession, build: Build) -> dict[str, Any]:
    metrics = (await session.execute(select(Metric).where(Metric.build_id == build.id))).scalars().all()
    process = await session.get(Process, build.process_id)
    gates = await gate_svc.gates_view(session, process) if process else []
    return {"id": build.id, "process_id": build.process_id, "job_id": build.job_id, "status": build.status,
            "runtime": build.runtime, "build_hash": build.build_hash, "spec_hash": build.spec_hash,
            "design_hash": build.design_hash, "design_id": build.design_id,
            "created_at": build.created_at.isoformat() if build.created_at else None,
            "finished_at": build.finished_at.isoformat() if build.finished_at else None,
            "error": build.error, "metrics": metrics_view(list(metrics)), "steps": build.steps or [],
            "repair": build.repair, "builder": build.builder, "infra_validation": build.infra_validation,
            "review_findings": {name: [r for r in build.reviews or [] if r.get("reviewer") == name]
                                for name in (build.builder or {}).get("reviewers") or []},
            "run": {"port": build.run_port, "pid": build.run_pid},
            "g4": next((g for g in gates if g["gate"] == "G4"), None)}


def file_tree(paths: list[dict[str, Any]]) -> dict[str, Any]:
    root: dict[str, Any] = {"name": "", "path": "", "type": "dir", "children": []}
    index: dict[str, dict[str, Any]] = {"": root}
    for f in sorted(paths, key=lambda x: x["path"]):
        parts = f["path"].split("/")
        for i in range(1, len(parts)):
            p = "/".join(parts[:i])
            if p not in index:
                node = {"name": parts[i - 1], "path": p, "type": "dir", "children": []}
                index["/".join(parts[:i - 1])]["children"].append(node)
                index[p] = node
        index["/".join(parts[:-1])]["children"].append(
            {"name": parts[-1], "path": f["path"], "type": "file", "origin": f["origin"]})
    return root


def file_out(f: BuildFile) -> dict[str, Any]:
    return {"path": f.path, "origin": f.origin, "process_id": f.process_id, "size": f.size, "sha256": f.sha256,
            "tibco_ref": f.tibco_ref}


async def latest_for(session: AsyncSession, process_id: str) -> Build | None:
    return await evidence.latest_build(session, process_id, finished=False)
