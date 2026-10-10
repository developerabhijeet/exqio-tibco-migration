"""Ask: hybrid search over the analysis chunks and the migration memory, then an optional model
call that answers from the hits and cites them.

Without a provider the hits themselves are the answer, so the feature works offline and shows
exactly what the knowledge base holds. With a provider, the model sees only the retrieved hits
(wrapped as data) and every citation it returns is checked against them: ids it invents are
dropped.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from . import search
from .search import Hit

if TYPE_CHECKING:
    from engine.agents.routing import RoutedProvider

    from .memory import MigrationMemory
    from .store import KnowledgeStore

PROMPT_FILE = "ask.md"
ASK_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "citation_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "citation_ids"],
}
NO_ANSWER = "The knowledge base has nothing on this question."


class Answer(BaseModel):
    text: str
    citations: list[Hit] = Field(default_factory=list)
    used_model: bool = False


def gather_hits(
    store: KnowledgeStore, memory: MigrationMemory, project_id: str, question: str, k: int = 8,
) -> list[Hit]:
    """Chunk hits and memory hits merged, best first. Each half is already normalised to its
    own best score, so the merge interleaves by that score."""
    hits = search.hybrid_search(store, project_id, question, k=k)
    for m in memory.retrieve(project_id, question, k=k):
        hits.append(Hit(id=m.id, kind=m.kind, process_id=None, text=m.text, score=m.score or 0.0))
    hits.sort(key=lambda h: (-h.score, h.id))
    return hits[:k]


def _render(hits: list[Hit], memory_sources: dict[str, str]) -> str:
    lines = []
    for h in hits:
        origin = memory_sources.get(h.id) or (f"process {h.process_id}" if h.process_id else "")
        lines.append(f"[id={h.id} kind={h.kind} origin={origin}] {h.text}")
    return "\n".join(lines)


def _render_prompt(question: str, source: str) -> tuple[str, str]:
    from pathlib import Path

    from engine.agents.prompting import render, split_prompt, wrap_source

    system, user = split_prompt(
        (Path(__file__).parent / "prompts" / PROMPT_FILE).read_text(encoding="utf-8")
    )
    values: dict[str, Any] = {"question": question, "source": wrap_source(source)}
    return render(system, **values), render(user, **values)


def fallback_text(hits: list[Hit]) -> str:
    if not hits:
        return NO_ANSWER
    lines = ["Closest knowledge items (no model answer was generated):"]
    for i, h in enumerate(hits, 1):
        where = f" ({h.process_id})" if h.process_id else ""
        lines.append(f"{i}. [{h.kind}]{where} {h.text}")
    return "\n".join(lines)


async def ask(
    store: KnowledgeStore,
    memory: MigrationMemory,
    project_id: str,
    question: str,
    provider: RoutedProvider | None = None,
    *,
    k: int = 8,
) -> Answer:
    hits = await asyncio.to_thread(gather_hits, store, memory, project_id, question, k)
    if not hits:
        return Answer(text=NO_ANSWER, citations=[])
    if provider is None:
        return Answer(text=fallback_text(hits), citations=hits)

    from engine.agents.prompting import ask_json

    sources = await asyncio.to_thread(_memory_sources, store, [h.id for h in hits])
    system, user = _render_prompt(question, _render(hits, sources))
    data, _ = await ask_json(
        provider, "analyse", None, system, user, ASK_SCHEMA,
        tags={"feature": "ask", "project": project_id}, max_tokens=1024,
    )
    by_id = {h.id: h for h in hits}
    cited = [by_id[i] for i in dict.fromkeys(data.get("citation_ids") or []) if i in by_id]
    return Answer(text=str(data.get("answer", "")).strip() or NO_ANSWER, citations=cited,
                  used_model=True)


def _memory_sources(store: KnowledgeStore, ids: list[str]) -> dict[str, str]:
    rows = store.connection().execute(
        "SELECT id, source FROM knowledge.memory_items WHERE id = ANY(%s)", [ids]
    ).fetchall()
    return {r["id"]: r["source"] for r in rows}
