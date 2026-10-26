"""ExiQoian: the project chat assistant. Guarded by ``require_chat_user`` (mounted in main.py):
every persona, viewers included, may use every method here and nowhere else.

A question's answer is validated in full (``services.exiqoian.ask`` commits both messages) and
only then streamed, one sentence per ``delta`` event, as ``text/event-stream``."""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from api.app.deps import ChatUserDep, SessionDep, SettingsDep
from api.app.models import ChatConversation, ChatMessage, Project
from api.app.schemas.exiqoian import (
    ChatMessageOut,
    ConversationDetailOut,
    ConversationIn,
    ConversationOut,
    ConversationsOut,
    MemoryState,
    MessageIn,
    PersonaOut,
    ProfileOut,
)
from api.app.services import exiqoian as service
from api.app.services.knowledge import NOT_CONFIGURED, KnowledgeUnavailable
from api.app.services.providers import build_provider, model_call_recorder
from engine.citations.model import CitedText
from engine.citations.spans import sentence_bounds
from engine.exiqoian.personas import PERSONAS, PersonaKey, suggestions
from engine.memory.search import MemoryRetriever

log = logging.getLogger("api.exiqoian")
router = APIRouter(prefix="/exiqoian", tags=["exiqoian"])
PING_S = 15


class _NotConfigured:
    """The retriever of an install without Neo4j: every read says so."""

    async def retrieve(self, *args: Any, **kwargs: Any) -> list:
        raise KnowledgeUnavailable(NOT_CONFIGURED)


def get_retriever(request: Request) -> MemoryRetriever:
    """The memory graph, or a stand-in that reports it is not configured. Tests override this."""
    memory = request.app.state.memory
    return memory if memory is not None else _NotConfigured()  # type: ignore[return-value]


async def _project(session: SessionDep, project_id: str) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def _conversation_out(c: ChatConversation) -> ConversationOut:
    return ConversationOut(id=c.id, title=c.title, persona=c.persona, updated_at=c.updated_at)  # type: ignore[arg-type]


def _message_out(m: ChatMessage) -> ChatMessageOut:
    return ChatMessageOut(
        id=m.id, role=m.role, text=m.text, cited=CitedText.model_validate(m.cited) if m.cited else None,
        verdict=m.verdict, persona=m.persona, used_model=m.used_model, created_at=m.created_at)  # type: ignore[arg-type]


@router.get("/{project_id}/profile", response_model=ProfileOut)
async def profile(project_id: str, request: Request, session: SessionDep, settings: SettingsDep,
                  principal: ChatUserDep) -> ProfileOut:
    await _project(session, project_id)
    held = service.held_personas(principal)
    _, _, names = await service.project_lexicon(session, project_id)
    memory = request.app.state.memory
    available = False
    if memory is not None:
        try:
            await asyncio.wait_for(memory.ping(), timeout=2)
            available = True
        except Exception:  # noqa: BLE001  unreachable, refused or slow: reported, not raised
            available = False
    return ProfileOut(
        personas=[PersonaOut(key=k, label=PERSONAS[k].label) for k in held],
        default_persona=service.resolve_persona(principal, None),
        suggestions={k: suggestions(k, names) for k in held},
        provider=build_provider(settings).name,
        memory=MemoryState(configured=memory is not None, available=available))


@router.get("/{project_id}/conversations", response_model=ConversationsOut)
async def list_conversations(project_id: str, session: SessionDep, principal: ChatUserDep) -> ConversationsOut:
    await _project(session, project_id)
    rows = await service.list_conversations(session, project_id, principal.actor)
    return ConversationsOut(conversations=[_conversation_out(c) for c in rows])


@router.post("/{project_id}/conversations", status_code=201, response_model=ConversationOut)
async def create_conversation(project_id: str, body: ConversationIn, session: SessionDep,
                              principal: ChatUserDep) -> ConversationOut:
    await _project(session, project_id)
    persona = service.resolve_persona(principal, body.persona)
    return _conversation_out(await service.create_conversation(session, project_id, principal.actor, persona))


@router.get("/{project_id}/conversations/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(project_id: str, conversation_id: str, session: SessionDep,
                           principal: ChatUserDep) -> ConversationDetailOut:
    await _project(session, project_id)
    conv = await service.get_conversation(session, project_id, principal.actor, conversation_id)  # owner check first
    messages = await service.list_messages(session, conv.id)
    return ConversationDetailOut(id=conv.id, title=conv.title, persona=conv.persona,  # type: ignore[arg-type]
                                 messages=[_message_out(m) for m in messages])


@router.delete("/{project_id}/conversations/{conversation_id}", status_code=204)
async def delete_conversation(project_id: str, conversation_id: str, session: SessionDep,
                              principal: ChatUserDep) -> Response:
    await _project(session, project_id)
    await service.delete_conversation(session, project_id, principal.actor, conversation_id)
    return Response(status_code=204)


def deltas(cited: CitedText) -> list[dict[str, Any]]:
    """The validated answer split one sentence per event. The pieces tile the text exactly (the
    whitespace between sentences travels with the next one) and each carries its own spans,
    offsets relative to that piece."""
    text = cited.text
    bounds = sentence_bounds(text)
    if not bounds:
        return [{"text": text, "spans": []}]
    cuts = [0, *(b for _, b in bounds[:-1]), len(text)]
    out = []
    for start, end in itertools.pairwise(cuts):
        spans = [{"start": sp.start - start, "end": sp.end - start, "refs": sp.refs}
                 for sp in cited.spans if sp.start >= start and sp.end <= end]
        out.append({"text": text[start:end], "spans": spans})
    return out


def _event(name: str, data: dict[str, Any]) -> ServerSentEvent:
    return ServerSentEvent(event=name, data=json.dumps(data, default=str))


@router.post("/{project_id}/conversations/{conversation_id}/messages", response_model=None)
async def post_message(project_id: str, conversation_id: str, body: MessageIn, request: Request,
                       session: SessionDep, settings: SettingsDep, principal: ChatUserDep,
                       retriever: Annotated[MemoryRetriever, Depends(get_retriever)]) -> EventSourceResponse:
    await _project(session, project_id)
    conv = await service.get_conversation(session, project_id, principal.actor, conversation_id)
    if body.persona is not None:
        persona: PersonaKey = service.resolve_persona(principal, body.persona)
    elif conv.persona in service.held_personas(principal):
        persona = conv.persona  # type: ignore[assignment]
    else:
        persona = service.resolve_persona(principal, None)
    await service.check_rate(session, principal.actor)
    choice = build_provider(settings)
    if choice.provider is not None:
        choice.provider.recorder = model_call_recorder(
            request.app.state.sessionmaker, job_id=None, project_id=project_id, process_id=None,
            primary_name=choice.provider.primary.name)
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

    async def on_stage(stage: str) -> None:
        await queue.put(("status", {"stage": stage}))

    async def on_event(name: str, data: dict[str, Any]) -> None:
        await queue.put((name, data))

    async def work() -> None:
        """Runs to completion even if the client goes away: both messages are committed."""
        try:
            async with request.app.state.sessionmaker() as own:
                _, bot = await service.ask(
                    own, retriever=retriever, provider=choice.provider, project_id=project_id,
                    conversation_id=conversation_id, owner=principal.actor, persona=persona,
                    text=body.text, on_stage=on_stage, on_event=on_event)
                message = _message_out(bot)
            cited = message.cited or CitedText.plain(message.text)
            if cited.refs:
                await queue.put(("sources", {"refs": [r.model_dump(mode="json") for r in cited.refs]}))
            for piece in deltas(cited):
                await queue.put(("delta", piece))
            await queue.put(("done", {"message": message.model_dump(mode="json")}))
        except HTTPException as exc:  # e.g. the rate limit lost a race with a concurrent request
            await queue.put(("error", {"detail": exc.detail}))
        except Exception:
            log.exception("exiqoian stream failed: project=%s", project_id)
            await queue.put(("error", {"detail": "ExiQoian could not answer"}))
        finally:
            await queue.put(None)

    task = asyncio.create_task(work())
    background = request.app.state.background
    background.add(task)
    task.add_done_callback(background.discard)

    async def stream() -> AsyncIterator[ServerSentEvent]:
        while (item := await queue.get()) is not None:
            yield _event(*item)

    return EventSourceResponse(stream(), ping=PING_S)
