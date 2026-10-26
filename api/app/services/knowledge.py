"""Migration memory as the API uses it: agents, Ask and the readiness screen read the Neo4j memory
graph (``MemoryGraph``); SME decisions are stored in Postgres (``memory_events``) and reach the
graph through a ``memory.ingest`` job of phase ``decision``.

Decision text and context pass through ``mask_sensitive`` before Postgres (``apply_revision``
masks again on the way into Neo4j). Reads report a missing or unreachable memory as
``KnowledgeUnavailable`` (``memory not configured`` when ``NEO4J_URI`` is empty, ``memory
unavailable: <class>`` when Neo4j fails), so a decision or an analysis never fails because
memory is down; callers log the reason and Ask answers 503.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar

from neo4j.exceptions import DriverError, Neo4jError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.app.models import MemoryEvent, Process
from engine.agents.jsonout import JsonOutputError
from engine.agents.provider import ProviderError, mask_sensitive
from engine.knowledge.ask import Answer
from engine.knowledge.ask import ask as engine_ask
from engine.knowledge.memory import MEMORY_KINDS, MemoryItem
from engine.memory.graph import MemoryGraph, MemoryUnavailable
from engine.memory.model import KnowledgeError, MemoryHit

if TYPE_CHECKING:
    from api.app.config import Settings

log = logging.getLogger(__name__)
T = TypeVar("T")
MEMORY_SOURCE = "sme"
NOT_CONFIGURED = "memory not configured"
# What a read of the memory graph raises when Neo4j is down, refuses the request or holds
# vectors of another embedding backend; reported by class name. Any other failure of a read (an
# embedding backend that cannot load, a bug) is reported too, with its class, so an agent run
# logs it and carries on; only the model's own errors (Ask) pass through.
MEMORY_ERRORS = (Neo4jError, DriverError, MemoryUnavailable, KnowledgeError, OSError)
PASS_THROUGH = (ProviderError, JsonOutputError)


class KnowledgeUnavailable(Exception):
    """Memory is not configured, or Neo4j could not be reached or refused the request."""


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_sensitive(value)
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(v) for v in value]
    return value


def _context(raw: Any) -> dict[str, Any]:
    """A Memory's ``context`` property (JSON text); absent on seed patterns (rulings R28)."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _created_at(raw: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(raw) if isinstance(raw, str) and raw else None
    except ValueError:
        return None


def memory_item(project_id: str, h: MemoryHit) -> MemoryItem | None:
    """A ``Memory`` hit as the agents' ``MemoryItem`` (``None`` for a kind they do not know)."""
    if h.kind not in MEMORY_KINDS:
        return None
    p = h.props
    return MemoryItem(id=h.uid, project_id=project_id, kind=h.kind, text=h.text,  # type: ignore[arg-type]
                      context=_context(p.get("context")), source=str(p.get("source") or ""),
                      created_by=str(p.get("created_by") or ""),
                      created_at=_created_at(p.get("created_at")), score=h.score)


async def about_uid(s: AsyncSession, context: dict[str, Any]) -> str | None:
    """The node a decision is about: its process, else its domain."""
    process_id = context.get("process_id")
    if isinstance(process_id, str) and process_id:
        process = await s.get(Process, process_id)
        if process is not None:
            return f"process:{process.ir_id}"
    key = context.get("domain_key")
    return f"domain:{key}" if isinstance(key, str) and key else None


class Knowledge:
    def __init__(self, memory: MemoryGraph | None, sessionmaker: async_sessionmaker[AsyncSession],
                 settings: Settings) -> None:
        self.memory = memory
        self.sessionmaker = sessionmaker
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.memory is not None

    async def _read(self, work: Callable[[MemoryGraph], Awaitable[T]]) -> T:
        if self.memory is None:
            raise KnowledgeUnavailable(NOT_CONFIGURED)
        try:
            return await work(self.memory)
        except PASS_THROUGH:
            raise
        except MEMORY_ERRORS as exc:
            cls = str(exc) if isinstance(exc, MemoryUnavailable) else type(exc).__name__
            raise KnowledgeUnavailable(f"memory unavailable: {cls}") from exc
        except Exception as exc:  # never fail the caller's phase over memory
            log.exception("memory read failed")
            raise KnowledgeUnavailable(f"memory unavailable: {type(exc).__name__}") from exc

    async def read(self, work: Callable[[MemoryGraph], Awaitable[T]]) -> T:
        """Run ``work`` on the memory graph; failures read as ``KnowledgeUnavailable``."""
        return await self._read(work)

    # ------------------------------------------------------------ reads
    async def retrieve_memory(self, project_id: str, query: str, k: int = 6) -> list[MemoryItem]:
        """The ``k`` best current ``Memory`` nodes for ``query`` (obsolete ones left out)."""
        hits = await self._read(lambda m: m.retrieve(project_id, query, labels=("Memory",), k=k))
        return [item for h in hits if (item := memory_item(project_id, h)) is not None]

    async def ask(self, project_id: str, question: str, provider: Any = None) -> Answer:
        """``engine.knowledge.ask.ask`` over the memory graph. Provider and JSON errors pass
        through (the caller retries without a model)."""
        return await self._read(lambda m: engine_ask(m, project_id, question, provider))

    async def counts(self, project_id: str) -> dict[str, int]:
        """Current ``Chunk`` and ``Memory`` nodes of the project."""
        counts = await self._read(lambda m: m.counts(project_id))
        return {"Chunk": counts.get("Chunk", 0), "Memory": counts.get("Memory", 0)}

    # ------------------------------------------------------------ writes (Postgres, then an ingest)

    async def record_decision(self, project_id: str, text: str, context: dict[str, Any],
                              user: str) -> str:
        """One SME decision as a ``memory_events`` row (``kind=decision``) and a queued ingest of
        phase ``decision``; returns the event id. The row is stored even when memory is not
        configured (nothing is queued then)."""
        from api.app.services.memory_ingest import (
            enqueue_ingest,  # imports analysis, which imports us
        )

        masked = _mask_value(dict(context))
        try:
            async with self.sessionmaker() as s:
                about = await about_uid(s, context)
                if about:
                    masked["about_uid"] = about
                row = MemoryEvent(project_id=project_id, kind="decision", text=mask_sensitive(text),
                                  context=masked, actor=user)
                s.add(row)
                await s.commit()
                event_id = row.id
        except SQLAlchemyError as exc:
            raise KnowledgeUnavailable(f"decision not stored: {type(exc).__name__}") from exc
        source = {"event_id": event_id}
        if isinstance(context.get("process_id"), str) and context["process_id"]:
            source["process_id"] = context["process_id"]
        try:
            await enqueue_ingest(self.sessionmaker, self.settings, project_id=project_id,
                                 phase="decision", scope=f"decision:{event_id}", source=source,
                                 actor=user, ingest_key=f"decision:{event_id}")
        except SQLAlchemyError as exc:  # the row is stored: a re-ingest of the project brings it
            log.error("decision %s stored but its memory ingest was not queued: %s", event_id,
                      type(exc).__name__)
            raise KnowledgeUnavailable(f"decision {event_id} stored; memory ingest not queued: "
                                       f"{type(exc).__name__}") from exc
        return event_id

    async def reindex_process(self, project_id: str, process_id: str) -> None:
        """Queue a re-ingest of the process's analysis scope (its chunks read the stored rows
        when the ingest runs); a queued one of the scope already covers it."""
        from api.app.services.memory_ingest import SYSTEM_ACTOR, enqueue_ingest

        try:
            await enqueue_ingest(self.sessionmaker, self.settings, project_id=project_id,
                                 phase="analysis", scope=f"analysis:{process_id}",
                                 source={"process_id": process_id}, actor=SYSTEM_ACTOR,
                                 ingest_key=f"analysis:reindex:{uuid.uuid4().hex}")
        except SQLAlchemyError as exc:
            raise KnowledgeUnavailable(f"re-ingest not queued: {type(exc).__name__}") from exc
