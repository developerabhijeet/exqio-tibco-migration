"""The engine's knowledge store (schema ``knowledge``) as the API uses it.

``KnowledgeStore`` holds one synchronous psycopg connection, which must not be shared between
threads. Every operation here therefore opens its own store in a worker thread
(``asyncio.to_thread``) and closes it afterwards; on a laptop database that costs a few
milliseconds. ``ensure_schema`` runs once per API process.

Text written here (memory items, chunks) passes through ``mask_sensitive`` first, so SSNs and
sensitive field values are not stored in the knowledge base. Failures are reported to the
caller as ``KnowledgeUnavailable`` so a decision or an analysis never fails because the
knowledge base is down; callers log the reason.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any, TypeVar

import psycopg

from engine.agents.provider import mask_sensitive
from engine.domains.models import Graph
from engine.knowledge.ask import Answer
from engine.knowledge.ask import ask as engine_ask
from engine.knowledge.memory import MigrationMemory
from engine.knowledge.store import KnowledgeError, KnowledgeStore

log = logging.getLogger(__name__)
T = TypeVar("T")
MEMORY_SOURCE = "sme"


class KnowledgeUnavailable(Exception):
    """The knowledge store could not be reached or refused the write."""


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_sensitive(value)
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(v) for v in value]
    return value


class Knowledge:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._ready = False
        self._ready_lock = threading.Lock()

    # ------------------------------------------------------------ plumbing
    def _call(self, fn: Callable[[KnowledgeStore], T]) -> T:
        store = KnowledgeStore(self.dsn)
        try:
            if not self._ready:
                with self._ready_lock:
                    if not self._ready:
                        store.ensure_schema()
                        self._ready = True
            return fn(store)
        finally:
            store.close()

    async def run(self, fn: Callable[[KnowledgeStore], T]) -> T:
        try:
            return await asyncio.to_thread(self._call, fn)
        except Exception as exc:
            raise KnowledgeUnavailable(f"{type(exc).__name__}: {exc}") from exc

    def query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        """Read rows (tests and diagnostics). Synchronous: call it through ``to_thread``."""
        return self._call(lambda s: s.connection().execute(sql, params or []).fetchall())

    # ------------------------------------------------------------ operations
    async def record_decision(self, project_id: str, text: str, context: dict[str, Any],
                              user: str) -> str:
        """One SME decision as migration memory (``kind=decision``); returns the memory id."""
        masked_text, masked_ctx = mask_sensitive(text), _mask_value(context)
        return await self.run(lambda s: MigrationMemory(s).record(
            project_id, "decision", masked_text, masked_ctx, MEMORY_SOURCE, user))

    async def retrieve_memory(self, project_id: str, query: str, k: int = 6) -> list[Any]:
        return await self.run(lambda s: MigrationMemory(s).retrieve(project_id, query, k=k))

    async def index_process(self, project_id: str, process_id: str,
                            items: list[tuple[str, str]]) -> int:
        """Replace the analysis chunks (summary, rules, findings) of one process."""
        masked = [(kind, mask_sensitive(text)) for kind, text in items if text and text.strip()]

        def write(s: KnowledgeStore) -> int:
            s.connection().execute(
                "DELETE FROM knowledge.chunks WHERE project_id = %s AND process_id = %s",
                [project_id, process_id])
            return len(s.upsert_chunks(project_id, process_id, masked))

        return await self.run(write)

    async def load_graph(self, project_id: str, graph: Graph) -> None:
        await self.run(lambda s: s.load_graph(project_id, graph))

    async def ask(self, project_id: str, question: str, provider: Any = None) -> Answer:
        """``engine.knowledge.ask.ask`` over a store of its own (it calls the store from worker
        threads one after another, never at the same time)."""
        await self.run(lambda s: None)  # schema ready, store reachable
        store = KnowledgeStore(self.dsn)
        try:
            return await engine_ask(store, MigrationMemory(store), project_id, question, provider)
        except (psycopg.Error, KnowledgeError) as exc:
            raise KnowledgeUnavailable(f"{type(exc).__name__}: {exc}") from exc
        finally:
            await asyncio.to_thread(store.close)
