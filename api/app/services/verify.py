"""Tab 6, Verify: scenarios, test data, the running .NET service, the verify job and the parity report.

Scenario database: one Postgres database per project, ``exiqo_scn_<project>_test`` on the
``DATABASE_URL`` server (created when missing; the ``_test`` suffix lets ``ScenarioDB.reset`` drop
and reseed its ``gmf`` schema), seeded from the estate's ``Database/*.sql``. ``SCENARIO_DATABASE_URL``
points every project at one database instead.

Scenarios (``GET /processes/{id}/scenarios``) come from ``generate_scenarios`` (schema, enumerations,
branch predicates, seed rows) with the expected result filled by the parity evaluator
(``fill_expected``, "derived from the TIBCO model"), and are cached in ``scenarios``. SME scenarios
are evaluated the same way.

Verify (job): for every scenario the database is reset to the seed plus the scenario's seed rows,
the scenario goes to the running service (REST starters: an HTTP call with a development token;
JMS starters: ``POST /_bus/publish`` with the selector's properties, then ``GET /_bus/messages``
and the database rows), and ``compare`` classifies every field: match, intended (a finding decided
Fix touches the field's lineage), noise (reviewed rules: timestamps, generated ids) or defect.
``service_called`` side effects are not compared: the running service calls the real endpoints,
not the evaluator's stubs.
"""
from __future__ import annotations

import asyncio
import json
import re
import socket
import time
import urllib.parse
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import Settings
from api.app.db import psycopg_url, utcnow
from api.app.jobs.runner import JobContext
from api.app.models import Build, FieldDiff, Process, Scenario, ScenarioRun
from api.app.services import evidence
from api.app.services import gates as gate_svc
from api.app.services.context import engine_app, engine_findings, engine_process
from engine.domains.discover import activity_sql, sql_tables
from engine.dotnet_gen.generator import is_sensitive_name, solution_layout
from engine.dotnet_gen.runner import DotnetRunner, ServiceHandle, find_dotnet
from engine.dotnet_gen.tests_gen import TestContext, _request
from engine.ir.models import Application as IRApplication
from engine.ir.models import Process as IRProcess
from engine.parity.compare import NoiseRule, build_lineage, compare
from engine.parity.db import ScenarioDB
from engine.parity.evaluator import EvaluationError, Evaluator
from engine.parity.models import (
    ExpectedResult,
    Fault,
    ParityReport,
    SeedRow,
    SideEffect,
    StubResponse,
)
from engine.parity.models import Scenario as EngineScenario
from engine.parity.scenarios import generate_scenarios, took_link
from engine.parity.seed import SeedData, estate_root
from engine.parity.stubs import StubRegistry, WireMockStubs

MODEL_LABEL = "expected output derived from the TIBCO model"
TEST_DATA_NOTE = ("Rows generated from the schemas, enumerations and branch predicates, with masking applied "
                  "to fields tagged sensitive (SSN, account numbers). Production test data is a separate "
                  "workstream.")
COMPARED_SIDE_EFFECTS = ("row_written", "message_published")
VERIFIER_SUBJECT = "exiqo-verifier"
RESET_REASON = "new verify run"

DEFAULT_NOISE: list[NoiseRule] = [
    NoiseRule(path_glob="*_at", reason="timestamp column: the database clock differs per run"),
    NoiseRule(path_glob="*At", reason="timestamp field: the wall clock differs per run"),
    NoiseRule(path_glob="*Time", reason="time field: the wall clock differs per run"),
    NoiseRule(path_glob="*timestamp*", reason="timestamp: the wall clock differs per run"),
    NoiseRule(path_glob="*Timestamp*", reason="timestamp: the wall clock differs per run"),
]
_ISO_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_SQL_CLOCK = re.compile(r"(?i)^\s*(current_timestamp|current_date|current_time|localtimestamp|now\(\))\s*$")
_SELECTOR = re.compile(r"(\w+)\s*=\s*'([^']*)'")


class ScenarioDBUnavailable(Exception):
    """The scenario database cannot be created or reached (HTTP 503)."""


class ServiceNotRunning(Exception):
    """The build's service is not running (HTTP 409)."""


# ------------------------------------------------------------------ scenario database
def scenario_dsn(settings: Settings, project_id: str) -> str:
    """``postgresql://`` DSN of the project's scenario database."""
    if settings.scenario_database_url:
        return psycopg_url(settings.scenario_database_url)
    info = conninfo_to_dict(psycopg_url(settings.database_url))
    info["dbname"] = f"exiqo_scn_{project_id}_test"
    return make_conninfo(**{k: v for k, v in info.items() if v is not None})


def scenario_db_name(settings: Settings, project_id: str) -> str | None:
    return conninfo_to_dict(scenario_dsn(settings, project_id)).get("dbname")


def npgsql_connection_string(dsn: str) -> str:
    info = conninfo_to_dict(dsn)
    parts = {"Host": info.get("host") or "localhost", "Port": info.get("port") or "5432",
             "Database": info.get("dbname") or "", "Username": info.get("user") or "",
             "Password": info.get("password") or ""}
    return ";".join(f"{k}={v}" for k, v in parts.items() if v != "")


def ensure_database(dsn: str, admin_dsn: str) -> bool:
    """Create the scenario database when it does not exist; True when it was created."""
    name = conninfo_to_dict(dsn).get("dbname") or ""
    if not name.endswith("_test"):
        raise ScenarioDBUnavailable(f"scenario database {name!r} must end with '_test' (its gmf schema is "
                                    "dropped and reseeded)")
    try:
        with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as conn:
            if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", [name]).fetchone():
                return False
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            return True
    except psycopg.Error as exc:
        raise ScenarioDBUnavailable(f"cannot create the scenario database {name}: {exc}".strip()) from exc


def estate_sql(app: IRApplication) -> tuple[str, str]:
    """``(schema SQL, seed SQL)`` of the estate (``Database/*.sql``)."""
    root = estate_root(app)
    if root is None:
        raise ScenarioDBUnavailable("the estate has no SQL files to seed the scenario database")
    schema, seed = "", ""
    for rel in app.sql_files:
        text = (root / rel).read_text(encoding="utf-8")
        if re.search(r"create\s+table", text, re.IGNORECASE):
            schema += text + "\n"
        if re.search(r"insert\s+into", text, re.IGNORECASE):
            seed += text + "\n"
    return schema, seed


def stubs_for(app: IRApplication) -> StubRegistry:
    root = estate_root(app)
    folder = root / "Stubs" if root is not None else None
    return WireMockStubs.from_folder(folder) if folder is not None and folder.is_dir() else StubRegistry()


def open_db(settings: Settings, project_id: str) -> ScenarioDB:
    dsn = scenario_dsn(settings, project_id)
    if not settings.scenario_database_url:
        ensure_database(dsn, psycopg_url(settings.database_url))
    return ScenarioDB(dsn)


def reset_db(db: ScenarioDB, app: IRApplication, seed_rows: list[SeedRow] | None = None) -> None:
    schema, seed = estate_sql(app)
    try:
        db.reset(schema, seed, seed_rows)
    except (psycopg.Error, ValueError) as exc:
        raise ScenarioDBUnavailable(f"cannot seed the scenario database: {exc}") from exc


# ------------------------------------------------------------------ scenarios
def _evaluate_sync(settings: Settings, project_id: str, app: IRApplication,
                   scenarios: list[EngineScenario]) -> tuple[list[EngineScenario], list[str]]:
    """Fill ``expected`` for each scenario (a fresh seed per call); scenarios that miss their link
    are dropped and every failure is reported, never hidden."""
    notes: list[str] = []
    db = open_db(settings, project_id)
    try:
        reset_db(db, app)
        evaluator = Evaluator(app, db, stubs_for(app))
        kept: list[EngineScenario] = []

        async def fill() -> None:
            for sc in scenarios:
                try:
                    sc.expected = await evaluator.run(sc.process_id, sc)
                except EvaluationError as exc:
                    notes.append(f"{sc.id}: the evaluator could not run it: {exc}")
                    continue
                for key, response in evaluator.last_stub_responses.items():
                    sc.stub_responses.setdefault(key, response)
                if took_link(sc):
                    kept.append(sc)
                else:
                    notes.append(f"{sc.id}: dropped, its run did not take its link")

        asyncio.run(fill())
        return kept, notes
    finally:
        db.close()


def _generate_sync(settings: Settings, project_id: str, app: IRApplication,
                   proc: IRProcess) -> tuple[list[EngineScenario], list[str]]:
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        seed = SeedData.for_app(app, proc)
        generated = generate_scenarios(proc, app.contracts, app, seed)
    return _evaluate_sync(settings, project_id, app, generated)


def scenario_out(row: Scenario) -> dict[str, Any]:
    data = row.data or {}
    return {"id": row.id, "key": row.key, "name": row.name, "kind": data.get("kind"), "origin": row.origin,
            "created_by": row.created_by, "input": data.get("input"),
            "stub_responses": data.get("stub_responses") or {}, "seed_state": data.get("seed_state") or [],
            "expected": data.get("expected"), "source": "model",
            "created_at": row.created_at.isoformat() if row.created_at else None}


async def load_scenarios(session: AsyncSession, process_id: str) -> list[Scenario]:
    return list((await session.execute(select(Scenario).where(Scenario.process_id == process_id)
                                       .order_by(Scenario.created_at, Scenario.key))).scalars().all())


async def ensure_scenarios(session: AsyncSession, settings: Settings, proc: Process, lock: asyncio.Lock,
                           *, refresh: bool = False) -> tuple[list[Scenario], list[str], bool]:
    """Cached scenarios of a process, generated (and cached) when there are none.

    Returns ``(rows, notes, generated_now)``. ``refresh`` regenerates the generated ones; SME
    scenarios are kept.
    """
    rows = await load_scenarios(session, proc.id)
    if rows and not refresh and any(r.origin == "generated" for r in rows):
        return rows, [], False
    async with lock:
        rows = await load_scenarios(session, proc.id)
        if rows and not refresh and any(r.origin == "generated" for r in rows):
            return rows, [], False
        app = await engine_app(session, proc)
        ip = engine_process(app, proc)
        generated, notes = await asyncio.to_thread(_generate_sync, settings, proc.project_id, app, ip)
        # upsert by key, so earlier verify runs keep pointing at their scenario
        existing = {r.key: r for r in rows if r.origin == "generated"}
        for sc in generated:
            row = existing.pop(sc.id, None)
            if row is None:
                session.add(Scenario(process_id=proc.id, key=sc.id, name=sc.name, origin="generated",
                                     data=sc.model_dump(mode="json")))
            else:
                row.name, row.data = sc.name, sc.model_dump(mode="json")
        for gone in existing.values():  # no longer generated (the IR changed)
            await session.delete(gone)
        await session.commit()
    return await load_scenarios(session, proc.id), notes, True


async def add_sme_scenario(session: AsyncSession, settings: Settings, proc: Process, lock: asyncio.Lock,
                           user: str, name: str, data: dict[str, Any], stub_responses: dict[str, Any],
                           seed_state: list[dict[str, Any]]) -> tuple[Scenario, list[str]]:
    app = await engine_app(session, proc)
    ip = engine_process(app, proc)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "scenario"
    key = f"{ip.name}:sme:{slug}"
    if await session.scalar(select(Scenario.id).where(Scenario.process_id == proc.id, Scenario.key == key)):
        raise ValueError(f"a scenario named {name!r} already exists for this process")
    sc = EngineScenario(id=key, process_id=ip.id, name=name, kind="sme", input=data, source="sme",
                        stub_responses={k: StubResponse.model_validate(v) for k, v in stub_responses.items()},
                        seed_state=[SeedRow.model_validate(s) for s in seed_state])
    async with lock:
        filled, notes = await asyncio.to_thread(_evaluate_sync, settings, proc.project_id, app, [sc])
    if not filled:
        raise EvaluationError("; ".join(notes) or "the evaluator could not run the scenario")
    row = Scenario(process_id=proc.id, key=key, name=name, origin="sme", created_by=user,
                   data=filled[0].model_dump(mode="json"))
    session.add(row)
    gate_svc.audit(session, proc, user, "scenario added", detail={"scenario": key})
    await session.commit()
    return row, notes


# ------------------------------------------------------------------ test data
def mask_value(value: Any) -> Any:
    text = "" if value is None else str(value)
    if not text:
        return value
    digits = re.sub(r"\D", "", text)
    if re.fullmatch(r"\d{3}-\d{2}-\d{4}", text):
        return f"***-**-{text[-4:]}"
    tail = digits[-4:] if len(digits) >= 4 else ""
    return f"****{tail}" if tail else "****"


def mask_doc(doc: Any, masked: set[str], path: str = "") -> Any:
    if isinstance(doc, dict):
        out = {}
        for k, v in doc.items():
            p = f"{path}.{k}" if path else str(k)
            if is_sensitive_name(str(k)) and not isinstance(v, dict | list):
                out[k] = mask_value(v)
                masked.add(str(k))
            else:
                out[k] = mask_doc(v, masked, p)
        return out
    if isinstance(doc, list):
        return [mask_doc(v, masked, path) for v in doc]
    return doc


async def test_data(session: AsyncSession, settings: Settings, proc: Process,
                    lock: asyncio.Lock) -> dict[str, Any]:
    rows, notes, _ = await ensure_scenarios(session, settings, proc, lock)
    app = await engine_app(session, proc)
    ip = engine_process(app, proc)
    masked: set[str] = set()
    inputs = [{"scenario": r.key, "name": r.name, "kind": (r.data or {}).get("kind"),
               "input": mask_doc((r.data or {}).get("input"), masked),
               "seed_state": mask_doc((r.data or {}).get("seed_state") or [], masked)} for r in rows]
    tables: dict[str, set[str]] = {}
    for act in ip.activities:
        for ref in sql_tables(activity_sql(act) or ""):
            tables.setdefault(ref.name, set()).add(ref.operation)
    seed = SeedData.for_app(app, ip)
    table_rows = []
    for name in sorted(tables):
        seeded = (seed.rows.get(name) if seed else None) or []
        table_rows.append({"table": name, "operations": sorted(tables[name]),
                           "rows": [mask_doc(r, masked) for r in seeded]})
    return {"process_id": proc.id, "note": TEST_DATA_NOTE, "label": MODEL_LABEL,
            "masked_fields": sorted(masked), "masking": "SSN keeps the last four digits (***-**-1234); "
            "account and card numbers keep the last four (****1234)", "inputs": inputs, "tables": table_rows,
            "notes": notes}


# ------------------------------------------------------------------ running services
@dataclass
class RunInfo:
    build_id: str
    handle: Any  # ServiceHandle or any object with url, pid, log_path, poll(), stop()
    port: int
    started_at: str = field(default_factory=lambda: utcnow().isoformat())

    @property
    def url(self) -> str:
        return self.handle.url

    @property
    def running(self) -> bool:
        return self.handle.poll() is None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def connection_names(build_root: Path, app_name: str) -> list[str]:
    layout = solution_layout(app_name)
    folder = layout["projects"]["api"]["folder"]
    path = build_root / folder / "appsettings.Development.json"
    names: set[str] = set()
    for candidate in (path, build_root / folder / "appsettings.json"):
        try:
            names |= set(json.loads(candidate.read_text(encoding="utf-8")).get("ConnectionStrings") or {})
        except (OSError, ValueError):
            continue
    return sorted(n for n in names if n.lower() != "servicebus")


def service_env(dsn: str, names: list[str]) -> dict[str, str]:
    env = {"ASPNETCORE_ENVIRONMENT": "Development", "Auth__Mode": "Development",
           "Messaging__Mode": "InMemory", "KeyVault__Uri": ""}
    conn = npgsql_connection_string(dsn)
    for n in names:
        env[f"ConnectionStrings__{n}"] = conn
    return env


def run_status(info: RunInfo | None) -> dict[str, Any]:
    if info is None:
        return {"running": False, "url": None, "port": None, "pid": None, "swagger_url": None,
                "started_at": None, "exit_code": None}
    code = info.handle.poll()
    return {"running": code is None, "url": info.url, "port": info.port, "pid": info.handle.pid,
            "swagger_url": f"{info.url}/swagger", "started_at": info.started_at, "exit_code": code}


def read_log(info: RunInfo | None, limit: int = 20000) -> str:
    if info is None:
        return ""
    try:
        text = Path(info.handle.log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:]


async def start_service(settings: Settings, build: Build, project_id: str,
                        timeout_s: float = 240.0) -> RunInfo:
    """``dotnet run`` of the build's Api project on a free port (raises ``RuntimeError`` with the log)."""
    if not build.output_dir or not Path(build.output_dir).is_dir():
        raise RuntimeError("the build has no solution on disk")
    dotnet = find_dotnet()
    if dotnet is None:
        raise RuntimeError("dotnet not found: install the .NET 10 SDK (see Settings, toolchains)")
    root = Path(build.output_dir)
    app_name = next((p.stem for p in root.glob("*.sln")), None)
    if app_name is None:
        raise RuntimeError("no .sln in the build folder")
    dsn = scenario_dsn(settings, project_id)
    if not settings.scenario_database_url:
        await asyncio.to_thread(ensure_database, dsn, psycopg_url(settings.database_url))
    env = service_env(dsn, connection_names(root, app_name))
    port = free_port()
    runner = DotnetRunner(root, dotnet=dotnet)
    handle: ServiceHandle = await asyncio.to_thread(
        runner.run_service, solution_layout(app_name)["api_project"], port, env, timeout_s)
    return RunInfo(build_id=build.id, handle=handle, port=port)


# ------------------------------------------------------------------ actual results
def noise_rules_for(expected: ExpectedResult) -> list[NoiseRule]:
    """The default reviewed rules plus exact paths whose expected value is a wall-clock time, a SQL
    clock function or a generated id."""
    rules = list(DEFAULT_NOISE)
    from engine.parity.compare import flatten_result

    fields, effects = flatten_result(expected, COMPARED_SIDE_EFFECTS)
    for path, value in {**fields, **effects}.items():
        text = "" if value is None else str(value).strip()
        if _ISO_TIME.match(text):
            rules.append(NoiseRule(path_glob=path, reason="timestamp value: the wall clock differs per run"))
        elif _SQL_CLOCK.match(text):
            rules.append(NoiseRule(path_glob=path, reason=f"SQL {text} is evaluated by the database at run time"))
        elif _UUID.match(text):
            rules.append(NoiseRule(path_glob=path, reason="generated id: differs per run"))
    return rules


def _equalish(a: Any, b: Any) -> bool:
    from engine.parity.compare import equal

    return equal(a, b)


def _best(candidates: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any] | None:
    best, score = None, -1
    for c in candidates:
        n = sum(1 for k, v in payload.items() if _equalish(v, c.get(k.removeprefix("where."))))
        if n > score:
            best, score = c, n
    return best


def _db_rows(db: ScenarioDB, table: str) -> list[dict[str, Any]]:
    parts = [p for p in table.split(".") if p]
    ident = sql.Identifier(*parts)
    try:
        rows = db.execute(sql.SQL("SELECT * FROM {}").format(ident).as_string(None))
    except psycopg.Error:
        return []
    return rows if isinstance(rows, list) else []


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def actual_rows(db: ScenarioDB, expected: ExpectedResult) -> list[SideEffect]:
    out: list[SideEffect] = []
    for se in expected.side_effects:
        if se.kind != "row_written":
            continue
        row = _best(_db_rows(db, se.target), se.payload)
        if row is None:
            continue
        out.append(SideEffect(kind="row_written", target=se.target,
                              payload={k: _jsonable(row.get(k.removeprefix("where."))) for k in se.payload}))
    return out


def actual_messages(expected: ExpectedResult, messages: list[dict[str, Any]]) -> list[SideEffect]:
    """Published messages as side effects: the best match per expected one, then any unexpected."""
    pool = []
    for m in messages:
        body = m.get("body")
        try:
            payload = json.loads(body) if isinstance(body, str) else (body or {})
        except ValueError:
            payload = {"body": body}
        pool.append({"entity": m.get("entity"), "payload": payload if isinstance(payload, dict) else {"body": payload}})
    out: list[SideEffect] = []
    for se in expected.side_effects:
        if se.kind != "message_published":
            continue
        same = [p for p in pool if p["entity"] == se.target]
        best = _best([p["payload"] for p in same], se.payload)
        if best is None:
            continue
        pool = [p for p in pool if p["payload"] is not best]
        out.append(SideEffect(kind="message_published", target=se.target, payload=best))
    for p in pool:  # published but not expected
        out.append(SideEffect(kind="message_published", target=str(p["entity"]), payload=p["payload"]))
    return out


def _problem_fault(status: int, body: Any) -> Fault | None:
    if status >= 500 and isinstance(body, dict) and isinstance(body.get("title"), str):
        data = body.get("fault") if isinstance(body.get("fault"), dict) else {}
        return Fault(name=body["title"], data=data)
    return None


async def dev_token(client: httpx.AsyncClient) -> str:
    r = await client.post("/auth/dev-token", json={"subject": VERIFIER_SUBJECT})
    r.raise_for_status()
    return str(r.json()["access_token"])


async def drive_rest(client: httpx.AsyncClient, proc: IRProcess, design_app: str,
                     scenario: EngineScenario, expected: ExpectedResult) -> ExpectedResult:
    ctx = TestContext(app=design_app, process=proc.name, starter_kind=proc.starter.kind if proc.starter else "rest")
    req = _request(ctx, proc, scenario)
    if req is None:
        raise RuntimeError("no request shape for this starter")
    url = req["path"]
    if req["query"]:
        url += "?" + urllib.parse.urlencode({k: str(v) for k, v in req["query"].items()})
    kwargs: dict[str, Any] = {}
    if req["body"] is not None and req["method"] not in ("GET", "DELETE"):
        kwargs["json"] = req["body"]
    r = await client.request(req["method"], url, **kwargs)
    try:
        body = r.json() if r.content else None
    except ValueError:
        body = r.text
    fault = _problem_fault(r.status_code, body)
    if expected.fault is not None and fault is not None:
        return ExpectedResult(output={}, fault=fault)
    output: dict[str, Any] = {"status": r.status_code}
    if body is not None:
        output["body"] = body
    return ExpectedResult(output=output, fault=fault)


async def bus_messages(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Everything the running service published on its in-memory bus (empty when there is none)."""
    r = await client.get("/_bus/messages")
    if r.status_code != 200:
        return []
    data = r.json()
    return data if isinstance(data, list) else []


async def drive_jms(client: httpx.AsyncClient, proc: IRProcess, scenario: EngineScenario) -> tuple[ExpectedResult, list[dict]]:
    cfg = proc.starter.config if proc.starter else {}
    entity = str(cfg.get("destination") or "")
    props = dict(_SELECTOR.findall(str(cfg.get("selector") or "")))
    before = await bus_messages(client)
    r = await client.post("/_bus/publish", json={"entity": entity, "payload": scenario.input, "properties": props})
    r.raise_for_status()
    outcome = r.json() if r.content else {}
    new = (await bus_messages(client))[len(before):]
    reason = outcome.get("reason") if isinstance(outcome, dict) else None
    fault = Fault(name=str(reason)) if reason and reason not in ("SelectorMismatch", "InvalidBody") else None
    if reason in ("SelectorMismatch", "InvalidBody"):
        raise RuntimeError(f"the service dead-lettered the message: {reason}")
    return ExpectedResult(output={}, fault=fault), new


async def run_verify(ctx: JobContext, build_id: str, settings: Settings, services: dict[str, RunInfo],
                     lock: asyncio.Lock) -> dict[str, Any]:
    async with ctx.sessionmaker() as s:
        build = await s.get(Build, build_id)
        if build is None:
            raise ValueError(f"build {build_id} not found")
        row = await s.get(Process, build.process_id)
        app = await engine_app(s, row)
        proc = engine_process(app, row)
        fixes = [f for f in await engine_findings(s, row.id) if f.decision == "fix"]
        project_id = row.project_id
        await ctx.stage("scenarios")
        rows, notes, fresh = await ensure_scenarios(s, settings, row, lock)
        scenarios = [(r.id, r.key, r.name, EngineScenario.model_validate(r.data)) for r in rows]
    await ctx.log(f"{len(scenarios)} scenario(s) " + ("generated" if fresh else "from the cache")
                  + f", expected results {MODEL_LABEL}")
    for n in notes:
        await ctx.log(n)
    info = services.get(build_id)
    if info is None or not info.running:
        raise ServiceNotRunning("the generated service is not running: start it first (POST /builds/{id}/run)")
    kind = proc.starter.kind if proc.starter else "subprocess"
    app_name = next((p.stem for p in Path(build.output_dir or ".").glob("*.sln")), "App")
    await ctx.stage("run")
    results: list[dict[str, Any]] = []
    async with lock, httpx.AsyncClient(base_url=info.url, timeout=60) as client:
        token = await dev_token(client)
        client.headers["Authorization"] = f"Bearer {token}"
        await ctx.log(f"development token issued by {info.url}/auth/dev-token")
        db = await asyncio.to_thread(open_db, settings, project_id)
        try:
            for scenario_id, key, name, sc in scenarios:
                expected = sc.expected
                started = time.monotonic()
                try:
                    if expected is None:
                        raise RuntimeError("no expected result (the evaluator could not run it)")
                    await asyncio.to_thread(reset_db, db, app, sc.seed_state)
                    if kind in ("rest", "http"):
                        before = await bus_messages(client)
                        actual = await drive_rest(client, proc, app_name, sc, expected)
                        messages = (await bus_messages(client))[len(before):]
                    elif kind == "jms":
                        actual, messages = await drive_jms(client, proc, sc)
                    else:
                        raise RuntimeError(f"no driver for a {kind} starter: verified through its caller "
                                           "or the generated tests")
                    effects = await asyncio.to_thread(actual_rows, db, expected)
                    effects += actual_messages(expected, messages if isinstance(messages, list) else [])
                    actual.side_effects = effects
                    try:
                        lineage = build_lineage(expected, app, proc.id)
                    except Exception:  # noqa: BLE001  lineage is best effort; no lineage means no "intended"
                        lineage = {}
                    report = compare(expected, actual, noise_rules_for(expected), fixes, lineage,
                                     scenario_id=key, finding_process=proc.id,
                                     side_effect_kinds=COMPARED_SIDE_EFFECTS)
                    status = "match" if report.unexplained == 0 else "differs"
                    results.append({"scenario_id": scenario_id, "key": key, "status": status,
                                    "report": report, "expected": expected, "actual": actual, "error": None})
                    await ctx.log(f"{key}: parity {report.parity:.0%}, {report.unexplained} unexplained "
                                  f"({time.monotonic() - started:.1f}s)")
                except Exception as exc:  # noqa: BLE001  one scenario failing never hides the others
                    results.append({"scenario_id": scenario_id, "key": key, "status": "error", "report": None,
                                    "expected": expected, "actual": None, "error": f"{type(exc).__name__}: {exc}"})
                    await ctx.log(f"{key}: could not run: {type(exc).__name__}: {exc}")
        finally:
            db.close()

    await ctx.stage("persist")
    async with ctx.sessionmaker() as s:
        for res in results:
            report: ParityReport | None = res["report"]
            report_json = report.model_dump(mode="json") if report else {"scenario_id": res["key"],
                                                                        "error": res["error"]}
            run = ScenarioRun(build_id=build_id, scenario_id=res["scenario_id"], job_id=ctx.job_id,
                              status=res["status"], report=report_json,
                              expected=_result_json(res["expected"]), actual=_result_json(res["actual"]),
                              parity=report.parity if report else None,
                              report_hash=evidence.canonical_hash(report_json))
            s.add(run)
            await s.flush()
            if report is not None:
                for kind_name, diffs in (("field", report.fields), ("side_effect", report.side_effects)):
                    for d in diffs:
                        s.add(FieldDiff(run_id=run.id, kind=kind_name, field=d.path, expected=_jsonable(d.expected),
                                        actual=_jsonable(d.actual), classification=d.status,
                                        finding_key=d.finding_id))
        process = await s.get(Process, row.id)
        rows_g = await gate_svc.gate_rows(s, row.id)
        reset = await gate_svc.reset_gates(s, process, rows_g, "G5", RESET_REASON, gate_svc.SYSTEM,
                                           gate_svc.SYSTEM, {"build_id": build_id, "job_id": ctx.job_id})
        await s.commit()
    summary = parity_summary([r["report"] for r in results if r["report"] is not None])
    errors = sum(1 for r in results if r["status"] == "error")
    await ctx.log(f"parity {summary['parity'] if summary['parity'] is not None else 'n/a'}: "
                  f"{summary['matched']} of {summary['compared']} fields match, {summary['unexplained']} "
                  f"unexplained, {errors} scenario(s) could not run"
                  + (f"; gates reset: {', '.join(reset)}" if reset else ""))
    return {**summary, "scenarios": len(results), "errors": errors, "gates_reset": reset, "source": "model"}


def _result_json(result: ExpectedResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return result.model_dump(mode="json", exclude={"trace"})


def parity_summary(reports: list[ParityReport]) -> dict[str, Any]:
    counted = [d for r in reports for d in r.fields + r.side_effects if d.status != "noise"]
    matched = sum(1 for d in counted if d.status == "match")
    return {"parity": (matched / len(counted)) if counted else (1.0 if reports else None),
            "matched": matched, "compared": len(counted),
            "intended": sum(1 for d in counted if d.status == "intended"),
            "unexplained": sum(r.unexplained for r in reports)}


async def parity_view(session: AsyncSession, build: Build) -> dict[str, Any] | None:
    runs = await evidence.latest_verify_runs(session, build.id)
    if not runs:
        return None
    names = {s.id: (s.key, s.name) for s in (await session.execute(
        select(Scenario).where(Scenario.id.in_([r.scenario_id for r in runs])))).scalars()}
    reports_out = []
    parsed: list[ParityReport] = []
    for r in runs:
        key, name = names.get(r.scenario_id, (None, None))
        rep = r.report or {}
        if r.status != "error":
            try:
                parsed.append(ParityReport.model_validate(rep))
            except ValueError:
                pass
        reports_out.append({**rep, "run_id": r.id, "scenario_key": key, "scenario_name": name,
                            "status": r.status, "error": rep.get("error")})
    summary = parity_summary(parsed)
    errors = sum(1 for r in runs if r.status == "error")
    return {"build_id": build.id, "job_id": runs[0].job_id, "created_at": runs[0].created_at.isoformat(),
            "source": "model", "label": MODEL_LABEL, "reports": reports_out, "parity": summary["parity"],
            "unexplained": summary["unexplained"], "matched": summary["matched"], "compared": summary["compared"],
            "intended": summary["intended"], "errors": errors, "report_hash": evidence.parity_hash(runs),
            "noise_rules": [n.model_dump() for n in DEFAULT_NOISE]}
