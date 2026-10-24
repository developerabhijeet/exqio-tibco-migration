"""Settings screen: providers, models, routing, toolchains, Langfuse; per-provider evaluations."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.app.deps import RunnerDep, SessionDep, SettingsDep, UserDep
from api.app.models import Eval, Process
from api.app.services import providers as provider_svc
from api.app.services import settings as svc

router = APIRouter(tags=["settings"])


class AzureKeys(BaseModel):
    """Bring your own Azure OpenAI key. The key is encrypted with APP_SECRET before it is stored and
    is never returned; ``clear`` takes the whole block back out."""

    endpoint: str | None = None
    api_key: str | None = None
    deployments: dict[str, str] | None = None
    clear: bool = False


class SettingsPatch(BaseModel):
    models: dict[str, str] | None = None  # {strong, standard, efficient}: Claude model names per tier
    routing_bands: dict[str, list[int]] | None = None  # {strong: [8, 10], standard: [4, 7], efficient: [0, 3]}
    langfuse_enabled: bool | None = None
    azure: AzureKeys | None = None


class EvalRequest(BaseModel):
    process_ids: list[str] = Field(min_length=1)


@router.get("/settings")
async def get_settings(settings: SettingsDep, session: SessionDep) -> dict[str, Any]:
    """Providers as configured or missing (never values), models, routing, toolchains, Langfuse."""
    stored = await svc.load_stored(session)
    return svc.settings_view(settings, await asyncio.to_thread(svc.detect_toolchains),
                             stored.get(svc.AZURE_KEY))


@router.put("/settings")
async def put_settings(body: SettingsPatch, session: SessionDep, settings: SettingsDep,
                       user: UserDep) -> dict[str, Any]:
    try:
        await svc.update(session, settings, body.model_dump(exclude_none=True), user)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    stored = await svc.load_stored(session)
    return svc.settings_view(settings, await asyncio.to_thread(svc.detect_toolchains),
                             stored.get(svc.AZURE_KEY))


@router.post("/providers/eval", status_code=202)
async def start_eval(body: EvalRequest, session: SessionDep, runner: RunnerDep,
                     settings: SettingsDep) -> dict[str, str]:
    """Run the understanding pipeline on the chosen processes with each configured provider."""
    first = await session.get(Process, body.process_ids[0])
    if first is None:
        raise HTTPException(status_code=404, detail=f"process {body.process_ids[0]} not found")
    if not provider_svc.raw_providers(settings):
        raise HTTPException(status_code=409, detail=provider_svc.NO_MODEL_REASON)
    ev = Eval(status="queued", process_ids=body.process_ids)
    session.add(ev)
    await session.commit()
    job_id = await runner.submit("eval", {"eval_id": ev.id, "process_ids": body.process_ids},
                                 provider_svc.eval_job(ev.id, body.process_ids, settings),
                                 project_id=first.project_id)
    ev.job_id = job_id
    await session.commit()
    return {"job_id": job_id, "eval_id": ev.id}


@router.get("/providers/eval/{eval_id}")
async def get_eval(eval_id: str, session: SessionDep) -> dict[str, Any]:
    ev = await session.get(Eval, eval_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="evaluation not found")
    results = ev.results or {}
    return {"id": ev.id, "job_id": ev.job_id, "status": ev.status, "process_ids": ev.process_ids,
            "rows": results.get("rows", []), "summary": results.get("summary", []),
            "created_at": ev.created_at.isoformat() if ev.created_at else None}
