"""Ask: hybrid search of the memory graph's analysis chunks and migration memory, then an
optional model call that answers from the hits and cites them.

Without a provider the hits themselves are the answer, so the feature works offline and shows
exactly what the memory holds. With a provider, the model sees only the retrieved hits (wrapped
as data) and every citation it returns is checked against them: ids it invents are dropped.
Each hit carries the memory ``Citation`` of the node version it came from; its ``id`` is the
node uid.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from engine.memory.model import KNOWLEDGE_LABELS, Citation, MemoryHit

if TYPE_CHECKING:
    from engine.agents.routing import RoutedProvider
    from engine.memory.search import MemoryRetriever

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


class Hit(BaseModel):
    id: str  # the node uid
    kind: str
    process_id: str | None = None  # API process id
    text: str
    score: float
    citation: Citation | None = None

    @classmethod
    def of(cls, h: MemoryHit) -> Hit:
        return cls(id=h.uid, kind=h.kind or h.label.lower(), process_id=h.citation.process_id,
                   text=h.text, score=h.score, citation=h.citation)


class Answer(BaseModel):
    text: str
    citations: list[Hit] = Field(default_factory=list)
    used_model: bool = False


def origin(h: Hit) -> str:
    """``<label> <artifact type> <phase> r<revision>`` of the version a hit cites."""
    c = h.citation
    return f"{c.label} {c.artifact_type} {c.phase} r{c.revision}" if c else ""


def _render(hits: list[Hit]) -> str:
    return "\n".join(f"[id={h.id} kind={h.kind} origin={origin(h)}] {h.text}" for h in hits)


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
    retriever: MemoryRetriever,
    project_id: str,
    question: str,
    provider: RoutedProvider | None = None,
    *,
    k: int = 8,
) -> Answer:
    """Chunk and Memory hits (one hybrid search, best first; obsolete memory left out)."""
    found = await retriever.retrieve(project_id, question, labels=KNOWLEDGE_LABELS, k=k)
    hits = [Hit.of(h) for h in found]
    if not hits:
        return Answer(text=NO_ANSWER, citations=[])
    if provider is None:
        return Answer(text=fallback_text(hits), citations=hits)

    from engine.agents.prompting import ask_json

    system, user = _render_prompt(question, _render(hits))
    data, _ = await ask_json(
        provider, "analyse", None, system, user, ASK_SCHEMA,
        tags={"feature": "ask", "project": project_id}, max_tokens=1024,
    )
    by_id = {h.id: h for h in hits}
    cited = [by_id[i] for i in dict.fromkeys(data.get("citation_ids") or []) if i in by_id]
    return Answer(text=str(data.get("answer", "")).strip() or NO_ANSWER, citations=cited,
                  used_model=True)
