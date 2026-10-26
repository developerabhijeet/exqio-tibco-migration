"""ExiQoian service: the project lexicon, personas of a caller, private conversations, the rate
limit, and one question end to end (guard, retrieval, answer, storage and audit row)."""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.auth.roles import Principal
from api.app.models import (
    Application,
    ChatConversation,
    ChatMessage,
    DesignRecord,
    Domain,
    Finding,
    Process,
    Rule,
)
from api.app.services.gates import audit
from api.app.services.knowledge import KnowledgeUnavailable
from engine.agents.provider import mask_sensitive
from engine.agents.routing import RoutedProvider
from engine.citations.model import CitedText
from engine.exiqoian.lexicon import MIGRATION_VOCABULARY, split_identifier
from engine.exiqoian.personas import PERSONA_ORDER, PersonaKey, SuggestionNames, default_persona
from engine.exiqoian.pipeline import Reply, Turn, respond
from engine.exiqoian.replies import NOT_CONFIGURED_REPLY, UNAVAILABLE_REPLY
from engine.exiqoian.sources import HISTORY_CHARS, HISTORY_MESSAGES, strip_markers
from engine.memory.graph import MemoryUnavailable
from engine.memory.search import MemoryRetriever

log = logging.getLogger("api.exiqoian")

RATE_LIMIT = 12
RATE_WINDOW_S = 60
LEXICON_TTL_S = 300
RATE_LIMITED = "slow down: ExiQoian takes 12 questions a minute"
TITLE_CHARS = 80
AUDIT_QUESTION_CHARS = 500
CONVERSATION_LIST = 50
NOT_CONFIGURED = "memory not configured"

_lexicon_cache: dict[str, tuple[float, tuple[frozenset[str], dict[str, str], SuggestionNames]]] = {}


def clear_lexicon_cache() -> None:
    _lexicon_cache.clear()


async def project_lexicon(session: AsyncSession, project_id: str) -> tuple[frozenset[str], dict[str, str], SuggestionNames]:
    """(casefolded lexicon, ``ir_id`` -> API process id, suggestion names), cached 300 s per project."""
    hit = _lexicon_cache.get(project_id)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    words: set[str] = set(MIGRATION_VOCABULARY)

    def add(name: str | None) -> None:
        if name:
            words.update(split_identifier(name))

    processes = (await session.execute(
        select(Process).where(Process.project_id == project_id).order_by(Process.created_at, Process.name))).scalars().all()
    ids: dict[str, str] = {}
    for p in processes:
        ids[p.ir_id] = p.id
        add(p.name)
        for act in (p.ir or {}).get("activities") or []:
            add(act.get("id"))
            add(act.get("name"))
    for name in (await session.execute(select(Application.name).where(Application.project_id == project_id))).scalars():
        add(name)
    for name in (await session.execute(select(Domain.name).where(Domain.project_id == project_id))).scalars():
        add(name)
    pids = list(ids.values())
    rules: dict[str, list[str]] = {}
    if pids:
        for pid, key in (await session.execute(
                select(Rule.process_id, Rule.rule_key).where(Rule.process_id.in_(pids)).order_by(Rule.created_at))).all():
            rules.setdefault(pid, []).append(key)
            add(key)
        for key in (await session.execute(select(Finding.finding_key).where(Finding.process_id.in_(pids)))).scalars():
            add(key)
    classes: dict[str, str] = {}
    if pids:
        latest = (await session.execute(
            select(DesignRecord.process_id, DesignRecord.record)
            .where(DesignRecord.process_id.in_(pids)).order_by(DesignRecord.version))).all()
        for pid, record in latest:  # ascending version: the last write per process is the latest
            rows = (record or {}).get("mapping") or []
            names = [r.get("class_name") for r in rows if r.get("class_name")]
            classes[pid] = names[0] if names else ""
            for n in names:
                add(n)
    first = next((p for p in processes if p.id in rules), processes[0] if processes else None)
    names = SuggestionNames(
        process=first.name if first else None,
        rule_key=(rules.get(first.id) or [None])[0] if first else None,
        class_name=(classes.get(first.id) or None) if first else None)
    value = (frozenset(words), ids, names)
    _lexicon_cache[project_id] = (time.monotonic() + LEXICON_TTL_S, value)
    return value


def held_personas(principal: Principal) -> list[PersonaKey]:
    """The personas the caller holds, in ``PERSONA_ORDER``; ``viewer`` when they hold none."""
    held = [k for k in PERSONA_ORDER if k in principal.roles]
    return held or ["viewer"]


def resolve_persona(principal: Principal, requested: PersonaKey | None) -> PersonaKey:
    if requested is None:
        return default_persona(frozenset(held_personas(principal)))
    if requested not in held_personas(principal):
        raise HTTPException(403, "persona not held")
    return requested


async def create_conversation(session: AsyncSession, project_id: str, owner: str, persona: PersonaKey) -> ChatConversation:
    conv = ChatConversation(project_id=project_id, owner=owner, persona=persona)
    session.add(conv)
    await session.commit()
    return conv


async def list_conversations(session: AsyncSession, project_id: str, owner: str) -> list[ChatConversation]:
    return list((await session.execute(
        select(ChatConversation).where(ChatConversation.project_id == project_id, ChatConversation.owner == owner)
        .order_by(ChatConversation.updated_at.desc()).limit(CONVERSATION_LIST))).scalars())


async def get_conversation(session: AsyncSession, project_id: str, owner: str, conversation_id: str) -> ChatConversation:
    conv = (await session.execute(select(ChatConversation).where(
        ChatConversation.id == conversation_id, ChatConversation.project_id == project_id,
        ChatConversation.owner == owner))).scalar_one_or_none()
    if conv is None:
        raise HTTPException(404, "conversation not found")
    return conv


async def list_messages(session: AsyncSession, conversation_id: str) -> list[ChatMessage]:
    return list((await session.execute(
        select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at, ChatMessage.role.desc()))).scalars())  # user before assistant on a tie


async def delete_conversation(session: AsyncSession, project_id: str, owner: str, conversation_id: str) -> None:
    conv = await get_conversation(session, project_id, owner, conversation_id)
    await session.execute(delete(ChatConversation).where(ChatConversation.id == conv.id))
    await session.commit()


async def check_rate(session: AsyncSession, owner: str, *, lock: bool = False) -> None:
    """429 when ``owner`` has asked ``RATE_LIMIT`` questions in the last ``RATE_WINDOW_S`` seconds
    (counted from the table on the database clock, so it holds across API replicas). With
    ``lock`` it first takes the owner's transaction-scoped advisory lock, so a check followed by an
    insert in the same transaction cannot interleave with another request of that owner."""
    if lock:
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(owner))))
    since = func.now() - timedelta(seconds=RATE_WINDOW_S)
    count = (await session.execute(
        select(func.count()).select_from(ChatMessage)
        .join(ChatConversation, ChatConversation.id == ChatMessage.conversation_id)
        .where(ChatConversation.owner == owner, ChatMessage.role == "user", ChatMessage.created_at > since))).scalar_one()
    if count >= RATE_LIMIT:
        raise HTTPException(429, RATE_LIMITED)


def history_turns(messages: list[ChatMessage]) -> list[Turn]:
    """Oldest first; a declined turn (the answer is not ``answer``) leaves out its decline and the
    user question before it; the last ``HISTORY_MESSAGES``, each masked, then cut to ``HISTORY_CHARS``."""
    kept: list[ChatMessage] = []
    for m in messages:
        if m.role == "assistant" and m.verdict != "answer":
            if kept and kept[-1].role == "user":
                kept.pop()
            continue
        kept.append(m)
    return [Turn(role=m.role, text=mask_sensitive(strip_markers(m.text) if m.role == "assistant" else m.text)[:HISTORY_CHARS])
            for m in kept[-HISTORY_MESSAGES:]]


def _unavailable(exc: Exception) -> Reply:
    not_configured = isinstance(exc, KnowledgeUnavailable) and str(exc) == NOT_CONFIGURED
    text = NOT_CONFIGURED_REPLY if not_configured else UNAVAILABLE_REPLY
    return Reply(verdict="unavailable", cited=CitedText.plain(text),
                 rule="not_configured" if not_configured else "memory_unavailable")


async def ask(session: AsyncSession, *, retriever: MemoryRetriever, provider: RoutedProvider | None,
              project_id: str, conversation_id: str, owner: str, persona: PersonaKey, text: str,
              on_stage: Callable[[str], Awaitable[None]] | None) -> tuple[ChatMessage, ChatMessage]:
    """One question: store the masked user message, answer, store the assistant message and the
    audit row, bump the conversation, commit once."""
    conv = await get_conversation(session, project_id, owner, conversation_id)
    prior = await list_messages(session, conv.id)
    started = time.monotonic()
    masked = mask_sensitive(text)
    # Count and insert under the owner's lock, and commit the user row before the (slow) answer,
    # so concurrent requests see each other's slots and the 12 a minute cap holds.
    await check_rate(session, owner, lock=True)
    user = ChatMessage(conversation_id=conv.id, role="user", text=masked, persona=persona)
    session.add(user)
    await session.commit()
    lexicon, process_ids, _ = await project_lexicon(session, project_id)
    try:
        reply = await respond(
            retriever=retriever, project_id=project_id, question=masked, persona=persona, lexicon=lexicon,
            process_ids=process_ids, history=history_turns(prior), provider=provider, on_stage=on_stage)
    except (MemoryUnavailable, KnowledgeUnavailable) as exc:
        reply = _unavailable(exc)
    except Exception:
        log.exception("exiqoian answer failed: project=%s", project_id)
        reply = Reply(verdict="unavailable", cited=CitedText.plain(UNAVAILABLE_REPLY), rule="error")
    bot = ChatMessage(
        conversation_id=conv.id, role="assistant", text=reply.cited.text, cited=reply.cited.model_dump(mode="json"),
        verdict=reply.verdict, persona=persona, used_model=reply.used_model,
        provider=provider.primary.name if provider is not None and reply.used_model else None,
        rule=reply.rule, dropped_sources=reply.dropped_sources, latency_ms=int((time.monotonic() - started) * 1000))
    session.add(bot)
    await session.flush()
    conv = await get_conversation(session, project_id, owner, conversation_id)
    if not conv.title:
        conv.title = masked[:TITLE_CHARS]
    conv.updated_at = func.now()
    audit(session, None, owner, "exiqoian asked", project_id=project_id, detail={
        "conversation_id": conv.id, "message_id": bot.id, "verdict": reply.verdict, "rule": reply.rule,
        "persona": persona, "used_model": reply.used_model, "provider": bot.provider,
        "sources": len(reply.sources), "cited": len(reply.cited.refs), "dropped_sources": reply.dropped_sources,
        "question": masked[:AUDIT_QUESTION_CHARS]})
    if reply.verdict == "injection":
        log.warning("exiqoian injection refused: rule=%s project=%s", reply.rule, project_id)
    await session.commit()
    return user, bot
