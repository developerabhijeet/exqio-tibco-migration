"""Tab 6, Verify: scenarios (generated and SME), the verify job, the parity report, test data."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.app.deps import RunnerDep, SessionDep, SettingsDep, UserDep
from api.app.models import Build, Process
from api.app.schemas.job import JobCreated
from api.app.services import verify as svc
from api.app.services.context import active_job
from engine.parity.evaluator import EvaluationError

router = APIRouter(tags=["verify"])


class SmeScenario(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    input: dict[str, Any]
    stub_responses: dict[str, dict[str, Any]] = Field(default_factory=dict)
    seed_state: list[dict[str, Any]] = Field(default_factory=list)


async def _process(session, process_id: str) -> Process:
    proc = await session.get(Process, process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail="process not found")
    return proc


class GenerateRequest(BaseModel):
    refresh: bool = False


@router.get("/processes/{process_id}/scenarios")
async def list_scenarios(process_id: str, session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    """The cached scenario set (read only). Empty, with a ``hint``, until
    ``POST /processes/{id}/scenarios/generate`` (or a build) has generated it."""
    proc = await _process(session, process_id)
    rows = await svc.load_scenarios(session, proc.id)
    return {"process_id": process_id, "source": "model", "label": svc.MODEL_LABEL, "generated_now": False,
            "notes": [], "database": svc.scenario_db_name(settings, proc.project_id),
            "hint": None if rows else svc.GENERATE_HINT.replace("{id}", process_id),
            "scenarios": [svc.scenario_out(r) for r in rows]}


@router.post("/processes/{process_id}/scenarios/generate")
async def generate_scenarios(process_id: str, request: Request, session: SessionDep, settings: SettingsDep,
                             body: GenerateRequest | None = None) -> dict[str, Any]:
    """Generate the scenario set (static scenarios, expected results from the evaluator) and cache
    it; without ``refresh`` an existing set is returned as is. 503 when the scenario database
    cannot be created or seeded."""
    proc = await _process(session, process_id)
    try:
        rows, notes, fresh = await svc.ensure_scenarios(session, settings, proc, request.app.state.scenario_lock,
                                                        refresh=(body or GenerateRequest()).refresh)
    except svc.ScenarioDBUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"process_id": process_id, "source": "model", "label": svc.MODEL_LABEL, "generated_now": fresh,
            "notes": notes, "database": svc.scenario_db_name(settings, proc.project_id), "hint": None,
            "scenarios": [svc.scenario_out(r) for r in rows]}


@router.post("/processes/{process_id}/scenarios", status_code=201)
async def add_scenario(process_id: str, body: SmeScenario, request: Request, session: SessionDep,
                       settings: SettingsDep, user: UserDep) -> dict[str, Any]:
    """Add an SME scenario; its expected result comes from the evaluator (the TIBCO model)."""
    proc = await _process(session, process_id)
    try:
        row, notes = await svc.add_sme_scenario(session, settings, proc, request.app.state.scenario_lock, user,
                                                body.name, body.input, body.stub_responses, body.seed_state)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EvaluationError as exc:
        raise HTTPException(status_code=422, detail=f"the evaluator could not run the scenario: {exc}") from exc
    except svc.ScenarioDBUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {**svc.scenario_out(row), "notes": notes}


@router.get("/processes/{process_id}/test-data")
async def test_data(process_id: str, session: SessionDep) -> dict[str, Any]:
    """Read only: masked inputs of the cached scenarios and seed rows of the tables the process touches."""
    return await svc.test_data(session, await _process(session, process_id))


@router.post("/builds/{build_id}/verify", status_code=202, response_model=JobCreated)
async def verify(build_id: str, request: Request, session: SessionDep, runner: RunnerDep,
                 settings: SettingsDep) -> JobCreated:
    """Queue the verify job; 409 when the build's service is not running or a verify is in progress."""
    build = await session.get(Build, build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="build not found")
    info = request.app.state.services.get(build_id)
    if info is None or not info.running:
        raise HTTPException(status_code=409, detail="the generated service is not running: POST /builds/{id}/run "
                                                    "first")
    running = await active_job(session, "verify", key="build_id", value=build_id)
    if running is not None:
        raise HTTPException(status_code=409, detail=f"a verify run of this build is in progress: {running}")
    proc = await session.get(Process, build.process_id)
    app = request.app

    async def run(ctx):
        return await svc.run_verify(ctx, build_id, settings, app.state.services, app.state.scenario_lock)

    job_id = await runner.submit("verify", {"build_id": build_id}, run, project_id=proc.project_id,
                                 process_id=proc.id)
    return JobCreated(job_id=job_id)


@router.get("/builds/{build_id}/parity")
async def parity(build_id: str, session: SessionDep) -> dict[str, Any]:
    """The parity report of the latest verify run; 404 before the first one."""
    build = await session.get(Build, build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="build not found")
    view = await svc.parity_view(session, build)
    if view is None:
        raise HTTPException(status_code=404, detail="no verify run for this build yet")
    return view
