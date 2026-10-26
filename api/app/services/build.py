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
6. restore, build, format, test: the stack's runner (``dotnet`` for .NET); restore always runs,
   bounded to ``RESTORE_TIMEOUT_S`` on .NET; a .NET failure is annotated with the NuGet probe; a
   step that cannot run is recorded as failed with the reason (the stack's tool absent, restore
   failed), never hidden;
7. repair (ai): only when restore and build succeeded and tests fail;
8. infra: Terraform and Terragrunt assets, Dockerfile and pipelines, then their validation;
9. metrics: the fixed formulas of spec 2.5;
10. persist: build files with origin and TIBCO lineage, test results, reviews, metrics, infra
    assets and the build hash; G4 to G6 reset when decided (a new build).

The target stack is the approved design's (``DesignRecord.stack``), which must still be the
project's (``approved_design`` refuses otherwise, so the endpoint answers 409 before a build row
exists); it is stored on ``Build.stack`` and every stage goes through ``get_stack(stack)``: the
generator, the runner, the agents' language checks and prompt pack, the test inventory and the
source files the metrics count. The .NET path calls what it always called.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import Settings
from api.app.db import utcnow
from api.app.jobs.locks import ScenarioLock
from api.app.jobs.runner import JobContext
from api.app.models import (
    Build,
    BuildFile,
    DesignRecord,
    InfraAsset,
    Metric,
    Process,
    Project,
    TestResult,
)
from api.app.services import evidence
from api.app.services import gates as gate_svc
from api.app.services import iterations as iteration_svc
from api.app.services import settings as settings_svc
from api.app.services.context import engine_app, engine_findings, engine_process, engine_rules
from api.app.services.design import ai_logger
from api.app.services.knowledge import Knowledge, KnowledgeUnavailable
from api.app.services.providers import build_provider, model_call_recorder
from engine.agents.builder import BuildRejection, fill_method_bodies
from engine.agents.jsonout import JsonOutputError
from engine.agents.provider import ProviderError
from engine.agents.reviewers import REVIEWERS, TEST_REVIEWER, review_panel, reviewed_test_files
from engine.agents.test_engineer import fill_test_bodies, placeholder_methods
from engine.dotnet_gen import runner as dotnet_runner
from engine.dotnet_gen.generator import GeneratedSolution, GenFile
from engine.dotnet_gen.metrics import compute_metrics
from engine.dotnet_gen.models import DesignRecord as EngineDesign
from engine.dotnet_gen.repair import (
    RepairAttempt,
    apply_review_findings,
    compile_repair_loop,
    repair_loop,
)
from engine.dotnet_gen.runner import BuildResult, DotnetRunner, StepResult, TestRun
from engine.dotnet_gen.tests_gen import branch_transitions
from engine.dotnet_gen.trx import BRANCH_SUFFIX, RULES_SUFFIX, SCENARIO_SUFFIX, classify
from engine.infra_gen.generator import generate_infra
from engine.infra_gen.validators import validate_infra
from engine.parity.models import Scenario as EngineScenario
from engine.stacks import get_stack
from engine.stacks.base import Mirrors, StackGenerator, StackRunner
from engine.stacks.testindex import INDEX_PATH, IndexEntry

STAGES = ["design check", "generate", "tests", "test engineer", "builder", "review", "restore", "build", "format", "test",
          "repair", "infra", "infra validate", "metrics", "persist"]
RUNTIMES = ("container_app", "aks")
LOG_LIMIT = 20000
RESET_REASON = "new build"
NO_DOTNET = ("dotnet not found on PATH or in .tools: install the .NET 10 SDK (Settings shows the "
             "toolchains)")
TOOLCHAIN_OFF = ("not run: the build toolchain is switched off (BUILD_TOOLCHAIN=false). The solution "
                 "was generated, filled and reviewed; restore, build, format and test did not run, "
                 "so nothing here says whether it compiles or passes.")
RESTORE_TIMEOUT_S = 120
NUGET_DOWN = ("NuGet unreachable: api.nuget.org did not answer and there is no offline cache at "
              ".tools/nuget-cache, so `dotnet restore` of the Api, Infrastructure and Tests projects cannot "
              "succeed here. Run `make nuget-cache` on a machine with NuGet access and copy .tools/nuget-cache "
              "across.")
# the generated TestDatabase starts a Testcontainers Postgres when TEST_DATABASE_URL is absent; these
# are the ways that fails without Docker (no daemon, no socket, no image pull)
_DOCKER_DOWN = re.compile(r"DockerUnavailableException|Docker is either not running|Cannot connect to the Docker "
                          r"daemon|docker\.sock|Testcontainers\.\S*Exception|pull access denied|toomanyrequests",
                          re.IGNORECASE)
_FACT = re.compile(r'\[Fact\(DisplayName = (?P<lit>"(?:[^"\\]|\\.)*")\)\]')
_METHOD = re.compile(r"public (?:async )?Task (?P<name>\w+)\(\)")


class BuildRefused(Exception):
    """The build cannot start (HTTP 409): G3 not approved, no design record, or the design is for
    another stack than the project's (or for one this install cannot build)."""


def stack_mismatch(design_stack: str, project_stack: str) -> str:
    return (f"the approved design is for {design_stack}, the project now targets {project_stack}: "
            "re-run Design")


def find_dotnet() -> str | None:
    """The ``dotnet`` binary the build uses (the cached ``detect_toolchains``, else ``PATH``);
    blocking, so the job calls it through ``asyncio.to_thread``; tests monkeypatch it."""
    return settings_svc.tool_path("dotnet") or shutil.which("dotnet")  # tool_path never raises


def run_restore(root: Path, dotnet: str) -> StepResult:
    """``dotnet restore`` of the solution, bounded to ``RESTORE_TIMEOUT_S`` (tests monkeypatch it)."""
    return DotnetRunner(root, dotnet=dotnet, timeout_s=RESTORE_TIMEOUT_S).restore()


def nuget_available() -> bool:
    return nuget_probe()[0]


def nuget_probe(timeout_s: float = 5.0, source: str | None = None) -> tuple[bool, str]:
    """``(reachable, what was checked)``: the offline cache, else ``GET`` of the package source.

    The source is the GMF mirror (``NUGET_SOURCE``); without one it is dotnet's own first enabled
    source, so a mirrored install never contacts a public feed."""
    import urllib.error
    import urllib.request

    if os.environ.get("EXIQO_NUGET_CACHE") or dotnet_runner.NUGET_CACHE.is_dir():
        return True, f"offline cache {os.environ.get('EXIQO_NUGET_CACHE') or dotnet_runner.NUGET_CACHE}"
    mirror = Settings().nuget_source if source is None else source
    url = dotnet_runner.nuget_index_url(mirror, find_dotnet())
    if url is None:
        return False, "no NuGet package source is configured (set NUGET_SOURCE)"
    if dotnet_runner.is_local_source(url):
        return True, f"local package source {url}"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            return resp.status == 200, f"GET {url}: HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        # A mirror that answers 401/403 is reachable; restore reports the credential problem.
        return exc.code < 500 and bool(mirror.strip()), f"GET {url}: HTTP {exc.code}"
    except ValueError as exc:
        return False, f"NUGET_SOURCE {url!r} is neither an existing folder nor an http(s) URL: {exc}"
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        return False, f"GET {url} failed: {exc}"


def toolchain() -> dict[str, Any]:
    """Every detected tool (the cached ``detect_toolchains``); blocking, call it off the loop."""
    return settings_svc.detect_toolchains()


def mirrors(settings: Settings) -> Mirrors:
    """The package mirrors a generated project restores from (GMF's; empty = the tool's default)."""
    return Mirrors(nuget=settings.nuget_source, maven=settings.maven_mirror_url,
                   goproxy=settings.goproxy, pypi=settings.uv_index_url)


class ToolchainMissing(RuntimeError):
    """The worker lacks a tool a non-.NET stack needs; the build job fails before generating."""


def missing_tools(stack: StackGenerator) -> list[str]:
    """The tools of ``stack.required_tools`` this worker does not have (.NET: ``find_dotnet``).
    Blocking (detection), so the job calls it through ``asyncio.to_thread``."""
    if stack.key == "dotnet":
        return [] if find_dotnet() else ["dotnet"]
    tools = toolchain()
    return [t for t in stack.required_tools if not (tools.get(t) or {}).get("present")]


def toolchain_missing_message(stack: StackGenerator, tools: list[str]) -> str:
    return (f"the {stack.key} toolchain is missing on this worker: {', '.join(tools)}; install it "
            "(Settings shows the toolchains) and build again")


def _tail(text: str, limit: int = LOG_LIMIT) -> str:
    text = text or ""
    return text if len(text) <= limit else "..." + text[-limit:]


def docker_down(run: TestRun) -> bool:
    """True when the test log or a failed case says Testcontainers could not reach Docker."""
    texts = [run.log or ""] + [c.message or "" for c in run.cases if c.outcome != "passed"]
    return any(_DOCKER_DOWN.search(t) for t in texts)


def _step(name: str, ok: bool, log: str, started: float, *, skipped: bool = False) -> dict[str, Any]:
    return {"name": name, "ok": ok, "skipped": skipped, "log": _tail(log),
            "duration_ms": int((time.monotonic() - started) * 1000)}


async def approved_design(session: AsyncSession, process: Process,
                          enabled: list[str] | None = None) -> DesignRecord:
    """The design record G3 approved (by its design hash); ``BuildRefused`` otherwise, also when
    its stack is not the project's, is not implemented, or is not in ``enabled`` (this install's
    ``enabled_stack_list``; ``None`` skips that check)."""
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
    design_stack = (design.record or {}).get("stack") or "dotnet"
    project = await session.get(Project, process.project_id)
    project_stack = project.target_stack if project is not None and project.target_stack else "dotnet"
    if design_stack != project_stack:
        raise BuildRefused(stack_mismatch(design_stack, project_stack))
    try:
        get_stack(design_stack)
    except KeyError as exc:
        raise BuildRefused(f"the {design_stack} stack cannot be built on this install: {exc.args[0]}") from exc
    if enabled is not None and design_stack not in enabled:
        raise BuildRefused(f"the {design_stack} stack cannot be built on this install: "
                           "not enabled on this install (stacks.enabled)")
    return design


def build_job(build_id: str, settings: Settings, knowledge: Knowledge, scenario_lock: ScenarioLock):
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


def _resync_from_disk(solution: GeneratedSolution) -> int:
    """Read formatted files back into the solution and return how many changed.

    The formatter rewrites files on disk; the build persists ``solution.files``. Without this the
    stored code would differ from the code that was formatted, compiled and tested."""
    changed = 0
    for i, f in enumerate(solution.files):
        path = solution.root / f.path
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if text != f.content:
            solution.files[i] = f.model_copy(update={"content": text})
            changed += 1
    return changed


# ------------------------------------------------------------------ static test inventory
def _cs_literal(lit: str) -> str:
    try:
        return json.loads(lit)
    except ValueError:
        return lit.strip('"')


def test_inventory(files: list[GenFile], app_name: str, stack: str = "dotnet") -> list[dict[str, Any]]:
    """Every generated ``[Fact]`` with its rule, branch or scenario id and whether it has a body.

    Other stacks carry the same facts in ``.exiqo/test-index.json`` (written with the project). The
    index says what the generator wrote; ``has_body`` of a rule or branch test is read again from its
    file (the Test Engineer fills bodies after the index was written), the index's own value kept
    only when the file is not among ``files``."""
    out: list[dict[str, Any]] = []
    if stack != "dotnet":
        texts = {f.path: f.content for f in files}
        for f in files:
            if f.path != INDEX_PATH:
                continue
            for e in (IndexEntry.model_validate(d) for d in json.loads(f.content)):
                has_body = e.has_body
                if e.kind in ("rule", "branch") and e.path in texts:
                    try:
                        has_body = not placeholder_methods(texts[e.path], stack, [e])
                    except KeyError:  # a stack not registered on this install: the index's word
                        pass
                out.append({"name": e.name, "display_name": e.display_name or e.name, "kind": e.kind,
                            "rule_key": e.rule_id, "ref": e.branch_id or e.scenario_id, "file": e.path or None,
                            "has_body": has_body, "outcome": "not_run", "message": None, "duration_ms": None})
        return out
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


async def undesigned_calls(sessionmaker: Any, proc_row: str, process: Any) -> list[str]:
    """Names of the sub-processes this process calls that have no design record.

    Those are the ones the generator stubs and a model invents; the ones with a design are
    generated from their own TIBCO IR."""
    targets = {c.target_process for c in process.calls if c.target_process}
    if not targets:
        return []
    async with sessionmaker() as s:
        row = await s.get(Process, proc_row)
        siblings = (await s.execute(select(Process).where(Process.project_id == row.project_id))).scalars().all()
        designed = set((await s.execute(select(DesignRecord.process_id))).scalars())
    by_name = {p.name: p for p in siblings}
    out: list[str] = []
    for target in sorted(targets):
        name = target.rsplit(".", 1)[-1]
        sibling = by_name.get(name)
        if sibling is None or sibling.id not in designed:
            out.append(name)
    return out


def call_closure(app: Any, process: Any) -> list[str]:
    """Engine ids of every sub-process this one calls, transitively, caller excluded.

    A process and the processes it calls are one migration unit: the callee's TIBCO source is in
    the same estate, so generating it is deterministic. Leaving it out makes the generator emit a
    throwing stub, and since the first call of this process is its audit step, every request then
    ends as a 503 that has nothing to do with the code under review. Targets with no process in
    this application are left out; ``undesigned_calls`` still reports those."""
    by_id = {p.id: p for p in app.processes}
    by_name = {p.name: p for p in app.processes}
    seen: set[str] = {process.id}
    queue = [process]
    out: list[str] = []
    while queue:
        current = queue.pop(0)
        for call in current.calls:
            target = call.target_process or ""
            found = by_id.get(target) or by_name.get(target.rsplit(".", 1)[-1])
            if found is None or found.id in seen:
                continue
            seen.add(found.id)
            out.append(found.id)
            queue.append(found)
    return out


# ------------------------------------------------------------------ the job
async def run_build(ctx: JobContext, build_id: str, settings: Settings, knowledge: Knowledge,
                    scenario_lock: ScenarioLock) -> dict[str, Any]:
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
        design_row = await approved_design(s, row, settings.enabled_stack_list)
        design = EngineDesign.model_validate(design_row.record)
        stack = get_stack(design.stack)  # approved_design refused a stack this install cannot build
        await ctx.log(f"G3 approved; design record v{design_row.version} (design hash "
                      f"{design_row.design_hash[:12]}, spec hash {design_row.spec_hash[:12]}); "
                      f"target stack {stack.key} ({stack.label})")
        app = await engine_app(s, row)
        proc = engine_process(app, row)
        rules = await engine_rules(s, process_id)
        findings = await engine_findings(s, process_id)
        # Review findings the engineer kept on the last build: this run applies them, which is how
        # a review panel verdict reaches the code. Rejected and undecided ones are left alone.
        previous_build = await latest_for(s, process_id)
        carried_reviews = kept_findings(previous_build) if previous_build and previous_build.id != build_id else []
        build.design_id = design_row.id
        build.iteration = await iteration_svc.job_iteration(s, ctx.payload, process_id)
        # what the iteration brought in (compiler errors, failing tests, logs, requirements): hints
        iteration_ctx = await iteration_svc.design_context(s, process_id, build.iteration)
        build.spec_hash = row.spec_hash
        build.design_hash = design_row.design_hash
        build.stack = stack.key
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
    lang = stack.language
    # X14a: the worker checks its own toolchain when the build starts (the API pods of a split
    # install have none). A non-.NET stack without its tools fails here, naming them, before any
    # model is paid to write code nothing could compile; .NET keeps building and says "dotnet not
    # found" on each step it skips.
    if stack.key != "dotnet" and settings.build_toolchain:
        absent = await asyncio.to_thread(missing_tools, stack)
        if absent:
            raise ToolchainMissing(toolchain_missing_message(stack, absent))

    # 2. generate (code)
    await ctx.stage(STAGES[1])
    await ctx.log(scenario_note)
    for n in notes:
        await ctx.log(n)
    # The process and everything it calls are generated together, from their own TIBCO source. Only
    # the process under review carries an approved design record; the rest get a deterministic
    # design skeleton (generate_solution builds one for any id without a given design), which is
    # code reading the IR, not a model inventing anything.
    closure = call_closure(app, proc)
    if closure:
        await ctx.log(f"generating {len(closure)} called sub-process(es) from their TIBCO source: "
                      f"{', '.join(sorted(i.rsplit('.', 1)[-1] for i in closure))}")
    solution: GeneratedSolution = await asyncio.to_thread(
        stack.generate, app, [proc.id, *closure], {proc.id: design}, {proc.id: rules},
        {proc.id: findings}, scenarios, out_dir, mirrors(settings))
    agent_files = [f for f in solution.files if f.origin == "agent" and not lang.is_test_path(f.path)]
    await ctx.log(f"solution {solution.name}: {len(solution.files)} file(s) in {out_dir}; "
                  f"{len(agent_files)} production file(s) with bodies left to the Code Builder")
    # A build generates the one process it is given. A sub-process it calls that has no design
    # record of its own is emitted as a stub for the Code Builder to invent, even though its TIBCO
    # definition is parsed and sitting in the estate. That invention is where this build will go
    # wrong if it goes wrong, so say it here rather than let it arrive as a 503 ten minutes later.
    stubbed = await undesigned_calls(ctx.sessionmaker, proc_row=process_id, process=proc)
    if stubbed:
        await ctx.log(f"{len(stubbed)} called sub-process(es) have no approved design and are "
                      f"generated as stubs the Code Builder has to invent: {', '.join(stubbed)}. "
                      f"Design them and build again for code derived from their TIBCO source.")

    # 3. tests first (code)
    await ctx.stage(STAGES[2])
    inventory = test_inventory(solution.files, solution.name, stack.key)
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
        # Same scope as the Code Builder: the called processes have rule and branch tests of their
        # own, and a placeholder body is a test that fails by construction.
        te_by_id = {p.id: p for p in app.processes}
        te_targets = [(proc, design)] + [
            (te_by_id[pid], solution.designs[pid]) for pid in solution.designs
            if pid != proc.id and pid in te_by_id]
        written = []
        for target_proc, target_design in te_targets:
            rules_for = approved_rules if target_proc.id == proc.id else []
            scenarios_for = scenarios if target_proc.id == proc.id else []
            try:
                written += await fill_test_bodies(solution, target_proc, rules_for, scenarios_for,
                                                  target_design, te_memory, provider, te_rejections,
                                                  warnings=te_warnings, stack=stack.key,
                                                  iteration=iteration_ctx if target_proc.id == proc.id else None)
            except (ProviderError, JsonOutputError) as exc:
                await ctx.log(f"Test Engineer failed on {target_proc.name}: {exc}", tag="ai")
        _apply(solution, written)
        te_filled = sorted(f.path for f in written)
        inventory = test_inventory(solution.files, solution.name, stack.key)
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
        # Every process in the solution, not only the one under review: a called sub-process is
        # generated from its own TIBCO source but still has bodies to fill, and an unfilled mapping
        # throws at runtime, which the caller's catchAll turns into a 503 with an empty body. The
        # process under review goes first so its rejections lead the log.
        by_id = {p.id: p for p in app.processes}
        targets = [(proc, design)] + [
            (by_id[pid], solution.designs[pid]) for pid in solution.designs
            if pid != proc.id and pid in by_id]
        if len(targets) > 1:
            await ctx.log(f"filling bodies for {len(targets)} process(es): "
                          f"{', '.join(p.name for p, _ in targets)}", tag="ai")
        filled = []
        for target_proc, target_design in targets:
            # only the process under review has approved rules; a called one is filled from its IR
            rules_for = approved_rules if target_proc.id == proc.id else []
            try:
                filled += await fill_method_bodies(solution, target_design, target_proc, rules_for,
                                                   memory_items, provider, rejections, stack=stack.key,
                                                   iteration=iteration_ctx if target_proc.id == proc.id else None)
            except (ProviderError, JsonOutputError) as exc:
                await ctx.log(f"Code Builder failed on {target_proc.name}: {exc}", tag="ai")
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
                                       reviewers=panel, stack=stack.key)
            reviewers_run = ([*REVIEWERS] if filled_paths else []) + (
                [TEST_REVIEWER] if reviewed_test_files(solution, proc, stack.key) else [])
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
    dotnet_steps = settings.build_toolchain
    is_dotnet = stack.key == "dotnet"
    dotnet = (await asyncio.to_thread(find_dotnet) if dotnet_steps else None) if is_dotnet else None
    # the generated tests read TEST_DATABASE_URL; the runner's child environment is an allowlist, so
    # it is passed explicitly (the repair loop's re-runs use the same runner and get it too)
    test_db = settings.test_database_url.strip()
    # Line coverage is measured over the files generated for this process. The solution also holds
    # the shared runtime (the Service Bus adapter the test host swaps for an in-memory bus, the
    # TIBCO value helpers, masking), which a test of this workflow cannot reach; counting it
    # answers a question nobody asked at G4.
    if is_dotnet:
        scope = {f.path for f in solution.files if f.process_id == proc.id and f.path.startswith("src/")}
    else:
        scope = {f.path for f in solution.files if f.process_id == proc.id
                 and f.path.endswith(stack.source_suffixes) and not lang.is_test_path(f.path)}
    test_env = {"TEST_DATABASE_URL": test_db} if test_db else None
    runner: StackRunner | DotnetRunner | None
    if is_dotnet:
        runner = DotnetRunner(solution.root, dotnet=dotnet, coverage_scope=scope, env=test_env) if dotnet else None
    else:  # the tools were checked before generating
        runner = stack.runner(solution.root, env=test_env, coverage_scope=scope) if dotnet_steps else None
    no_tool = NO_DOTNET  # only .NET reaches a step without its toolchain
    if not dotnet_steps:
        await ctx.log(TOOLCHAIN_OFF)
    elif runner is not None and not test_db:
        await ctx.log(evidence.TESTDB_UNSET)
    build_result: BuildResult | None = None
    test_run: TestRun | None = None
    format_ok = False

    await ctx.stage(STAGES[6])
    t0 = time.monotonic()
    if not dotnet_steps:
        restore = StepResult(ok=False, log=TOOLCHAIN_OFF)
    elif runner is None:
        restore = StepResult(ok=False, log=no_tool)
    elif not is_dotnet:
        restore = await asyncio.to_thread(runner.restore)
    else:
        restore = await asyncio.to_thread(run_restore, solution.root, dotnet)
        if not restore.ok:
            reachable, probe = await asyncio.to_thread(nuget_probe, 5.0, settings.nuget_source)
            note = NUGET_DOWN if (not reachable or dotnet_runner.nuget_unreachable(restore.log)) else \
                "restore failed with the package source reachable: see the log above"
            restore = StepResult(ok=False, log=f"{restore.log.rstrip()}\n\n{note}\nprobe: {probe}")
    steps.append(_step("restore", restore.ok, restore.log, t0, skipped=not dotnet_steps))
    await ctx.log("restore " + ("passed" if restore.ok else f"failed: {restore.log.strip().splitlines()[-1]}"))

    await ctx.stage(STAGES[7])
    t0 = time.monotonic()
    compile_notes: list[str] = []
    if runner is not None and restore.ok:
        build_result = await asyncio.to_thread(runner.build)
        # A solution that does not compile used to end the run here: the repair loop below needs a
        # compiling solution, so the one failure it could never answer was the compiler. Give the
        # errors to the model first; a diff is kept only when it reduces them.
        if not build_result.ok and provider is not None:
            await ctx.log(f"compile repair: {len(build_result.errors)} error(s); asking for a fix", tag="ai")

            async def on_compile_attempt(a: RepairAttempt) -> None:
                await ctx.log(f"compile repair attempt {a.n}: {'applied' if a.applied else 'rejected'}"
                              + (f": {a.reason}" if a.reason else "")
                              + (f"; failing: {', '.join(a.failing[:5])}" if a.failing else ""), tag="ai")

            cr = await compile_repair_loop(runner, solution, proc, approved_rules, provider,
                                           recorder=on_compile_attempt, stack=stack.key,
                                           iteration=iteration_ctx)
            before_errors = len(build_result.errors)
            build_result = cr.final
            compile_notes = [(f"compile repair: {len(cr.attempts)} attempt(s), "
                              f"{sum(1 for a in cr.attempts if a.applied)} applied; "
                              f"{before_errors} error(s) before, {len(build_result.errors)} after")]
            compile_notes += [f"  attempt {a.n}: {'applied' if a.applied else 'rejected'}"
                              + (f" ({a.reason})" if a.reason else "") for a in cr.attempts]
            await ctx.log(f"compile repair: {len(cr.attempts)} attempt(s); the solution "
                          f"{'compiles now' if build_result.ok else 'still does not compile'}", tag="ai")
        build_log = "\n".join([*compile_notes, build_result.log]) if compile_notes else build_result.log
        steps.append(_step("build", build_result.ok, build_log, t0))
        await ctx.log(f"build {'passed' if build_result.ok else 'failed'}: {build_result.warnings} warning(s), "
                      f"{len(build_result.errors)} error(s)")
    else:
        why = TOOLCHAIN_OFF if not dotnet_steps else (no_tool if runner is None else "not run: restore failed")
        steps.append(_step("build", False, why, t0, skipped=True))
        await ctx.log(f"build {why}")

    await ctx.stage(STAGES[8])
    t0 = time.monotonic()
    if runner is None:
        why = TOOLCHAIN_OFF if not dotnet_steps else no_tool
        steps.append(_step("format", False, why, t0, skipped=True))
        await ctx.log(f"format {why}")
    else:
        whitespace = not restore.ok
        if is_dotnet:
            mode = "dotnet format whitespace --folder (no restore needed)" if whitespace else "dotnet format"
        else:
            mode = f"{stack.key} formatter" + (" (whitespace only: restore failed)" if whitespace else "")
        # Format the generated code before judging it. Templates and model-written bodies are not
        # laid out the way dotnet format would lay them out, so checking without formatting first
        # only ever reported that fact as a failure.
        written = await asyncio.to_thread(runner.format_write, whitespace)
        changed = _resync_from_disk(solution)
        if changed:
            await ctx.log(f"{mode}: formatted {changed} file(s)")
        fmt = await asyncio.to_thread(runner.format_check, whitespace)
        format_ok = fmt.ok
        log = (f"{mode}: formatted {changed} file(s)\n"
               f"{mode} --verify-no-changes: {'passed' if fmt.ok else 'failed'}\n{fmt.log}"
               + ("" if written.ok else f"\nthe formatter itself reported a problem:\n{written.log}"))
        steps.append(_step("format", fmt.ok, log, t0))
        await ctx.log(f"{mode}: {'passed' if fmt.ok else 'failed'}")

    await ctx.stage(STAGES[9])
    t0 = time.monotonic()
    if runner is not None and build_result is not None and build_result.ok:
        test_run = await asyncio.to_thread(runner.test)
        passed = sum(1 for c in test_run.cases if c.outcome == "passed")
        test_log = test_run.log
        if not test_run.ok and not test_db and docker_down(test_run):
            test_log = f"{test_log.rstrip()}\n\n{evidence.TESTDB_UNSET}"  # read back by the G4 reason
        steps.append(_step("test", test_run.ok, test_log, t0))
        await ctx.log(f"tests: {passed} of {len(test_run.cases)} passed"
                      + (f", line coverage {test_run.coverage_line:.1%}" if test_run.coverage_line is not None else ""))
    else:
        why = TOOLCHAIN_OFF if not dotnet_steps else (no_tool if runner is None else "not run: the solution did not compile")
        steps.append(_step("test", False, why, t0, skipped=True))
        await ctx.log(f"tests {why}")

    # 7. repair loop (ai)
    await ctx.stage(STAGES[10])
    repair: dict[str, Any] | None = None
    if runner is None or not restore.ok or build_result is None or not build_result.ok:
        await ctx.log("repair loop not run: it works from failing tests, so it needs a solution that "
                      "compiles; the compile repair above is what answers a build failure", tag="ai")
    elif test_run is not None and test_run.ok:
        await ctx.log("repair loop not needed: every test passed", tag="ai")
    elif provider is None:
        await ctx.log("repair loop skipped: no model configured", tag="ai")
    else:
        async def on_attempt(a: RepairAttempt) -> None:
            await ctx.log(f"repair attempt {a.n}: {'applied' if a.applied else 'rejected'}"
                          + (f": {a.reason}" if a.reason else "") + f"; failing: {', '.join(a.failing[:5])}",
                          tag="ai")

        result = await repair_loop(runner, solution, proc, approved_rules, provider, recorder=on_attempt,
                                   stack=stack.key, iteration=iteration_ctx)
        repair = result.model_dump(mode="json")
        test_run = result.final
        if not result.attempts and not result.final.cases:
            await ctx.log("repair loop not run: the test host produced no results, so the test "
                          "project did not build; there are no failing tests to repair", tag="ai")
        else:
            await ctx.log(f"repair loop: {len(result.attempts)} attempt(s); tests now "
                          f"{'pass' if result.final.ok else 'still fail'}", tag="ai")
    # 7b. apply the review findings the engineer kept (ai)
    review_applied = False
    if carried_reviews and provider is not None and runner is not None and build_result is not None \
            and build_result.ok:
        await ctx.log(f"applying {len(carried_reviews)} review finding(s) kept on the last build", tag="ai")

        async def on_review_attempt(a: RepairAttempt) -> None:
            await ctx.log(f"review apply attempt {a.n}: {'applied' if a.applied else 'rejected'}"
                          + (f": {a.reason}" if a.reason else ""), tag="ai")

        applied = await apply_review_findings(runner, solution, proc, approved_rules, carried_reviews,
                                              provider, recorder=on_review_attempt, stack=stack.key)
        review_applied = applied.applied > 0
        test_run = applied.final
        await ctx.log(f"review findings: {applied.applied} of {len(applied.attempts)} attempt(s) applied; "
                      f"tests {'pass' if applied.final.ok else 'still fail'}", tag="ai")
    elif carried_reviews:
        await ctx.log(f"{len(carried_reviews)} kept review finding(s) not applied: they need a model and a "
                      f"compiling solution", tag="ai")

    inventory = test_inventory(solution.files, solution.name, stack.key)  # has_body from the final files

    # 8. infrastructure (code)
    await ctx.stage(STAGES[11])
    infra_files = await asyncio.to_thread(generate_infra, app, design, solution.root, solution.name,
                                          runtime=runtime, stack=stack.key)
    await ctx.log(f"{len(infra_files)} infrastructure file(s): Terraform modules, Terragrunt live config "
                  f"({runtime}), Dockerfile, pipelines")
    await ctx.stage(STAGES[12])
    tools = await asyncio.to_thread(toolchain)
    validation = await asyncio.to_thread(validate_infra, solution.root, (tools.get("terraform") or {}).get("path"),
                                         (tools.get("terragrunt") or {}).get("path"),
                                         {"TF_CLI_CONFIG_FILE": settings.tf_cli_config_file}
                                         if settings.tf_cli_config_file else None,
                                         init=settings.infra_validate_init,
                                         init_timeout_s=settings.terraform_init_timeout_s)
    await ctx.log(f"terraform fmt -check: {validation.fmt_ok}; module validate: "
                  f"{sum(1 for m in validation.modules if m.validate_ok)} of {len(validation.modules)}; "
                  f"terragrunt hcl fmt --check: {validation.hclfmt_ok}; ok: {validation.ok}")
    for note in validation.notes:
        await ctx.log(note)

    # 9. metrics (code)
    await ctx.stage(STAGES[13])
    source_files = sum(1 for f in solution.files if f.path.endswith(stack.source_suffixes))
    br = build_result or BuildResult(ok=False, files=source_files, log="not built")
    tr = test_run or TestRun(ok=False, log="not run")
    infra_ok = validation.ok if validation.tools_present and any(validation.tools_present.values()) else None
    metrics = compute_metrics(rules, len(branch_transitions(proc)), tr, br, format_ok, infra_ok, process=proc.id,
                              source_files=None if is_dotnet else source_files)
    if is_dotnet and runner is not None and not restore.ok:
        metrics.details["standards_adherence"] += (" (reduced check: dotnet format whitespace --folder only, "
                                                   "because restore failed; analyzers and style rules were not run)")
    await ctx.log("metrics: " + "; ".join(f"{k} {v}" for k, v in metrics.details.items()))

    # 10. persist (code)
    await ctx.stage(STAGES[14])
    tests = merge_results(inventory, test_run)
    layout = {"projects": {kind: {"name": p.name, "folder": p.folder}
                           for kind, p in stack.layout(solution.name).projects.items()}}
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
        if review_applied and previous_build is not None:
            # close the loop on the previous build: a kept finding now says which build acted on it
            prev = await s.get(Build, previous_build.id)
            if prev is not None:
                prev.reviews = [{**r, "applied_in": build_id} if r.get("decision") == "keep" else dict(r)
                                for r in (prev.reviews or [])]
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
                     "standards_adherence", "compile_ok", "warnings", "infra_ok",
                     "conversion_accuracy"):
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
        b.builder = {"filled": filled_paths, "rejections": [_rejection(r) for r in rejections],
                     "provider": choice.name, "ai_available": provider is not None,
                     "reviewers": reviewers_run,
                     "test_engineer": {"filled": te_filled, "warnings": te_warnings,
                                       "rejections": [_rejection(r) for r in te_rejections]}}
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



def _rejection(r: Any) -> dict[str, Any]:
    """One rejected reply, as the build record stores it: why, and what was rejected."""
    if hasattr(r, "model_dump"):
        return r.model_dump()
    out = {"path": r.path, "attempt": r.attempt, "reason": r.reason}
    body = getattr(r, "body", "")
    if body:
        out["body"] = body
    return out

def _asset_validation(path: str, vjson: dict[str, Any]) -> dict[str, Any]:
    """The part of the validation that concerns one asset (its module, or the formatting checks)."""
    m = re.match(r"infra/modules/([^/]+)/", path)
    if m:
        module = next((x for x in vjson.get("modules") or [] if x.get("name") == m.group(1)), None)
        return {"fmt_ok": vjson.get("fmt_ok"), "module": module}
    if path.startswith("infra/live/"):
        return {"hclfmt_ok": vjson.get("hclfmt_ok")}
    return {}


# ---------------------------------------------------------------- review findings: keep or reject
async def decide_review_finding(session: AsyncSession, build: Build, index: int,
                                decision: str | None, note: str | None, user: str) -> list[dict[str, Any]]:
    """Record a verdict on one review finding and return the whole list.

    The findings live in the build's ``reviews`` JSONB, written once when the build ran. A verdict
    is stored beside the finding rather than in a table of its own because it belongs to that
    build: a later build runs its own review panel and starts undecided. ``keep`` is what the next
    build acts on; ``reject`` is remembered so the same finding is not put to the engineer twice."""
    findings = [dict(r) for r in (build.reviews or [])]
    if index < 0 or index >= len(findings):
        raise IndexError(index)
    row = findings[index]
    if decision is None:
        for key in ("decision", "decided_by", "decided_at", "note"):
            row.pop(key, None)
    else:
        row["decision"] = decision
        row["decided_by"] = user
        row["decided_at"] = datetime.now(UTC).isoformat()
        if note:
            row["note"] = note
        else:
            row.pop("note", None)
    build.reviews = findings  # a new list, so SQLAlchemy sees the JSONB change
    await session.commit()
    return findings


async def decide_all_review_findings(session: AsyncSession, build: Build, decision: str | None,
                                     only_undecided: bool, user: str) -> list[dict[str, Any]]:
    """One verdict across every review finding, for a panel with dozens of them.

    ``only_undecided`` leaves verdicts somebody already gave alone, which is what makes this safe
    to press after working through a few by hand. A finding already applied by a later build keeps
    its record: that one is history, not a decision still open."""
    findings = [dict(r) for r in (build.reviews or [])]
    stamp = datetime.now(UTC).isoformat()
    for row in findings:
        if row.get("applied_in"):
            continue
        if only_undecided and row.get("decision"):
            continue
        if decision is None:
            for key in ("decision", "decided_by", "decided_at", "note"):
                row.pop(key, None)
        else:
            row["decision"] = decision
            row["decided_by"] = user
            row["decided_at"] = stamp
    build.reviews = findings
    await session.commit()
    return findings


def kept_findings(build: Build | None) -> list[dict[str, Any]]:
    """The findings an engineer asked the next build to act on."""
    return [dict(r) for r in ((build.reviews if build else None) or []) if r.get("decision") == "keep"]


# The exception every un-migrated sub-process throws (engine.dotnet_gen.generator).
SUBPROCESS_NOT_MIGRATED = re.compile(
    r"SubprocessNotMigratedException[^\n]*?|subprocess (?P<target>[\w.]+) was not migrated")


def blocking_subprocess(message: str | None) -> str | None:
    """The sub-process that stopped this test, or None when the test really failed.

    A caller whose sub-process was never migrated throws before reaching anything this build
    produced, so the test did not fail: it never ran. The name comes out of the one exception the
    generator emits for that case, so this never has to guess from prose a model wrote."""
    if not message or "SubprocessNotMigrated" not in message and "was not migrated" not in message:
        return None
    m = re.search(r"subprocess (?P<target>[\w.]+) was not migrated", message)
    return (m.group("target").rsplit(".", 1)[-1] if m else "an un-migrated sub-process")


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
            "runtime": build.runtime, "stack": build.stack or "dotnet", "iteration": build.iteration,
            "build_hash": build.build_hash, "spec_hash": build.spec_hash,
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
