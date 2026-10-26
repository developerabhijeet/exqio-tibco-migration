"""Layer 5 and the prompt: phase-diversified, persona-ordered sources from the memory graph,
the source injection scrub and the prompt rendering."""
import asyncio
import logging
import re
from pathlib import Path

from pydantic import BaseModel

from engine.agents.provider import mask_sensitive
from engine.citations.memory import cite_ref
from engine.citations.model import CiteRef
from engine.exiqoian.guard import detect_injection
from engine.exiqoian.personas import PERSONAS, PersonaKey
from engine.memory.model import KNOWLEDGE_LABELS, MemoryHit
from engine.memory.search import MemoryRetriever, query_terms

MAX_SOURCES = 10
HISTORY_MESSAGES = 6
HISTORY_CHARS = 600
GROUPS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("knowledge", tuple(KNOWLEDGE_LABELS), 8),
    ("old code", ("Process", "Activity"), 4),
    ("new code", ("Class", "Method"), 4),
    ("decisions", ("GateDecision", "Error", "Curation"), 3),
)

_PROMPT = (Path(__file__).parent / "prompts" / "exiqoian.md").read_text(encoding="utf-8")
_SYSTEM, _USER = (part.strip() for part in _PROMPT.split("<!-- user -->", 1))
log = logging.getLogger("engine.exiqoian")
# `<` or fullwidth `＜`, optionally spaced, before a delimiter tag name.
_FORGED = re.compile("[<" + chr(0xff1c) + r"](?=\s*/?\s*(?:source|history|question|system))", re.IGNORECASE)
# [S1], [s 1], [S1, S2], [S1;S2], [S1,2], [ S1 ]
_MARKER = re.compile(r"\[\s*S\s*\d+(?:\s*[,;]\s*S?\s*\d+)*\s*\]", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\{(persona_line|canary|history|question|sources)\}")


class Source(BaseModel):
    marker: str  # "S1", "S2", ...
    hit: MemoryHit
    ref: CiteRef


def strip_markers(text: str) -> str:
    """Remove source markers from retrieved text, repeating until stable so that removing an inner
    marker cannot reassemble an outer one (`[S[S7]7]`)."""
    while (out := _MARKER.sub(" ", text)) != text:
        text = out
    return text


def escape_tags(text: str) -> str:
    """``<source``, ``</source``, ``<history``, ``<question``, ``<system`` (any case) lose their ``<``."""
    return _FORGED.sub("&lt;", text)


def retrieval_query(question: str, previous_question: str | None) -> str:
    """A short follow-up borrows the previous question's terms (retrieval only)."""
    if previous_question and len(query_terms(question)) < 6:
        return f"{question} {previous_question}"
    return question


async def gather_sources(retriever: MemoryRetriever, project_id: str, query: str, persona: PersonaKey,
                         process_ids: dict[str, str]) -> tuple[list[Source], int]:
    """Up to MAX_SOURCES sources and the number of hits dropped for carrying an injection."""
    results = await asyncio.gather(*(
        retriever.retrieve(project_id, query, labels=labels, k=k, min_terms=2, include_obsolete=False)
        for _, labels, k in GROUPS), return_exceptions=True)
    failed = [r for r in results if isinstance(r, BaseException)]
    if len(failed) == len(results):
        raise failed[0]  # nothing readable: the caller maps this (MemoryUnavailable -> unavailable)
    for (name, _, _), r in zip(GROUPS, results, strict=True):
        if isinstance(r, BaseException):
            log.warning("exiqoian retrieval group %r failed and was skipped: %s", name, r)
    groups = [r for r in results if not isinstance(r, BaseException)]
    best: dict[str, MemoryHit] = {}
    for h in (h for group in groups for h in group):
        if h.uid not in best or h.score > best[h.uid].score:
            best[h.uid] = h
    kept: list[tuple[MemoryHit, CiteRef]] = []
    dropped = 0
    for h in sorted(best.values(), key=lambda h: -h.score):
        if detect_injection(h.text) is not None:
            dropped += 1
            continue
        kept.append((h, cite_ref(h, "tmp", process_ids)))
    by_phase: dict[str, list[tuple[MemoryHit, CiteRef]]] = {}
    for h, ref in kept:
        by_phase.setdefault(ref.phase, []).append((h, ref))
    order = [p for p in PERSONAS[persona].phase_order if p in by_phase] + [p for p in by_phase if p not in PERSONAS[persona].phase_order]
    picked: list[tuple[MemoryHit, CiteRef]] = []
    while len(picked) < MAX_SOURCES and any(by_phase[p] for p in order):
        for p in order:
            if by_phase[p] and len(picked) < MAX_SOURCES:
                picked.append(by_phase[p].pop(0))
    return [Source(marker=f"S{i}", hit=h, ref=ref.model_copy(update={"id": f"S{i}"}))
            for i, (h, ref) in enumerate(picked, 1)], dropped


def _fill(template: str, values: dict[str, str]) -> str:
    """One pass, so a placeholder inside a value is never expanded again."""
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def _source_block(s: Source) -> str:
    def attr(v: str) -> str:
        return escape_tags(v).replace('"', "&quot;")
    text = escape_tags(mask_sensitive(strip_markers(s.hit.text)))
    return (f'<source id="{s.marker}" phase="{attr(s.ref.phase)}" kind="{attr(s.ref.kind)}" '
            f'label="{attr(s.ref.label)}">{text}</source>')


def render_prompt(question: str, sources: list[Source], history: list[tuple[str, str]],
                  persona: PersonaKey, canary: str) -> tuple[str, str]:
    """(system, user) prompt. History keeps the last 6 messages cut to 600 characters."""
    def turn(role: str, text: str) -> str:  # mask, then flatten lines, then cut
        flat = " ".join(mask_sensitive(text).split())[:HISTORY_CHARS]
        return f"{'assistant' if role == 'assistant' else 'user'}: {escape_tags(flat)}"
    hist = "\n".join(turn(role, text) for role, text in history[-HISTORY_MESSAGES:])
    system = _fill(_SYSTEM, {"persona_line": PERSONAS[persona].prompt_line, "canary": canary,
                             "history": "", "question": "", "sources": ""})
    user = _fill(_USER, {"persona_line": "", "canary": "", "history": hist or "(none)",
                         "question": escape_tags(mask_sensitive(question)),
                         "sources": "\n".join(_source_block(s) for s in sources) or "(none)"})
    return system, user
