"""Ask: questions answered from the project's knowledge base (analysis chunks and migration
memory) with citations. Without a model provider the closest knowledge items are the answer."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from api.app.deps import SessionDep, SettingsDep
from api.app.models import Project
from api.app.schemas.analysis import AnswerOut, AskIn
from api.app.services.knowledge import KnowledgeUnavailable
from api.app.services.providers import build_provider, model_call_recorder
from engine.agents.jsonout import JsonOutputError
from engine.agents.provider import ProviderError

router = APIRouter(tags=["ask"])


@router.post("/ask", response_model=AnswerOut)
async def ask(body: AskIn, request: Request, session: SessionDep, settings: SettingsDep) -> AnswerOut:
    if await session.get(Project, body.project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    choice = build_provider(settings)
    if choice.provider is not None:
        choice.provider.recorder = model_call_recorder(
            request.app.state.sessionmaker, job_id=None, project_id=body.project_id, process_id=None,
            primary_name=choice.provider.primary.name)
    knowledge = request.app.state.knowledge
    try:
        try:
            answer = await knowledge.ask(body.project_id, body.question.strip(), choice.provider)
        except (ProviderError, JsonOutputError):
            answer = await knowledge.ask(body.project_id, body.question.strip(), None)
    except KnowledgeUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"knowledge base unavailable: {exc}") from exc
    return AnswerOut.model_validate(answer.model_dump())
