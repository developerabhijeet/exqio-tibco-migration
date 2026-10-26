"""The ExiQoian answer pipeline, layers 1-7 (guard, retrieval and grounding, model or no-model
composer, output validation). Pure: the caller supplies the retriever, the provider and the
project lexicon; persistence, auth and audit live in the API."""
from __future__ import annotations

import re
import secrets
from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel, Field

from engine.agents.jsonout import JsonOutputError
from engine.agents.prompting import ask_json
from engine.agents.provider import ProviderError, mask_sensitive
from engine.agents.routing import RoutedProvider
from engine.citations.model import CitedText, CiteRef
from engine.citations.spans import from_markers, sentence_bounds
from engine.exiqoian.guard import check, detect_output_injection, normalise
from engine.exiqoian.personas import PersonaKey
from engine.exiqoian.replies import REPLY_FOR, Verdict
from engine.exiqoian.sources import (
    Source,
    gather_sources,
    render_prompt,
    retrieval_query,
    strip_markers,
)
from engine.memory.search import MemoryRetriever

ANSWER_MAX_TOKENS = 900
EXIQOIAN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["answer", "off_topic", "injection", "not_covered"]},
        "answer": {"type": "string"},
    },
    "required": ["verdict", "answer"],
}
COMPOSER_INTRO = "From this project's memory:"
COMPOSER_SOURCES = 3
COMPOSER_CHARS = 240
_LEAK_TAG = re.compile(r"<\s*/?\s*(?:source|history|question|system)", re.IGNORECASE)
_FIRST_SENTENCE = re.compile(r"^.*?\.(?=\s|$)", re.DOTALL)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class Reply(BaseModel):
    verdict: Verdict
    cited: CitedText
    rule: str | None = None
    used_model: bool = False
    sources: list[CiteRef] = Field(default_factory=list)  # every source offered, ids S1..Sn
    dropped_sources: int = 0


def _fixed(verdict: Verdict, rule: str | None = None, *, used_model: bool = False,
           sources: list[CiteRef] | None = None, dropped: int = 0) -> Reply:
    return Reply(verdict=verdict, cited=CitedText.plain(REPLY_FOR[verdict]), rule=rule,
                 used_model=used_model, sources=sources or [], dropped_sources=dropped)


_LIST_NUMBER = re.compile(r"^\s*(?:\d+[.)]|[-*\u2022])\s+")
_BODYLESS = re.compile(r"^[\s\-*\u2022>#\d.)(:]*$")  # what may precede a marker that leads its sentence
_MARKER_GROUP = re.compile(r"[ \t]*\[S\d+(?:\s*,\s*S\d+)*\][ \t]*")
_ALLOWED_DECLINES = ("off_topic", "injection", "not_covered")


def _first_sentence(text: str, limit: int = COMPOSER_CHARS) -> str:
    flat = _LIST_NUMBER.sub("", " ".join(strip_markers(text).split()))
    bounds = sentence_bounds(flat)
    cut = flat[bounds[0][0]:bounds[0][1]] if bounds else ""
    return cut.rstrip(".!? ")[:limit].rstrip()


def compose_without_model(sources: list[Source]) -> str:
    """The offline answer: up to three sources, one sentence each, each ending with its marker.
    Empty when no source has a sentence to quote."""
    lines = []
    for s in sources:
        if sentence := _first_sentence(s.hit.text):
            lines.append(f"{sentence} [{s.marker}].")
        if len(lines) == COMPOSER_SOURCES:
            break
    return COMPOSER_INTRO + "\n" + "\n".join(lines) if lines else ""


_PREV_ENDS_WITH_MARKER = re.compile(r"\]\s*[.!?]+\s*$")


def _leads(answer: str, start: int) -> bool:
    """A marker leads its text when only a bullet, number or punctuation stands before it on its
    line, or when the sentence before it already ends with its own marker. Anywhere else it
    trails the sentence before it (`Fact one. [S1] Fact two. [S2]` cites each sentence)."""
    prefix = answer[answer.rfind("\n", 0, start) + 1:start]
    return bool(_BODYLESS.match(prefix) or _PREV_ENDS_WITH_MARKER.search(prefix))


def _trailing_markers(answer: str) -> str:
    """Move a marker that leads its sentence or list item (`[S1] Text.`, `- [S2] Text.`) to the end
    of that sentence, before the closing punctuation. A marker with no text after it stays where
    it is (it cites what precedes it, or nothing)."""
    pos_from = 0
    for _ in range(100):
        moved = False
        for m in _MARKER_GROUP.finditer(answer, pos_from):
            start = m.start() + len(m.group()) - len(m.group().lstrip())  # the spaces before it stay
            if not _leads(answer, start):
                continue
            rest = answer[:start] + answer[m.end():]
            body = next(((a, b) for a, b in sentence_bounds(rest) if a <= start < b), None)
            if body is None:
                continue
            end = body[1]
            while end > start and rest[end - 1] in ".!?":
                end -= 1
            if end <= start:
                continue
            answer = rest[:end] + " " + m.group().strip() + rest[end:]
            pos_from = start
            moved = True
            break
        if not moved:
            break
    return answer


def validate_answer(answer: str, sources: list[Source], canary: str,
                    system_first_sentence: str) -> tuple[Verdict, CitedText, str | None]:
    """Layer 7 steps 2-5 on an answer the model gave with verdict ``answer``, plus X22: the text
    that would be shown must itself pass the injection check."""
    low, folded = answer.lower(), normalise(answer)
    if (canary.lower() in low or canary.lower() in folded.replace(" ", "") or _LEAK_TAG.search(folded)
            or system_first_sentence.lower() in low):
        return "injection", CitedText.plain(REPLY_FOR["injection"]), "output_leak"
    cited, _dropped = from_markers(_trailing_markers(mask_sensitive(answer)), {s.marker: s.ref for s in sources})
    if not cited.refs:
        return "not_covered", CitedText.plain(REPLY_FOR["not_covered"]), "uncited"
    if detect_output_injection(cited.text) is not None:
        return "injection", CitedText.plain(REPLY_FOR["injection"]), "output_leak"
    return "answer", cited, None


async def respond(*, retriever: MemoryRetriever, project_id: str, question: str, persona: PersonaKey,
                  lexicon: frozenset[str], process_ids: dict[str, str], history: list[Turn],
                  provider: RoutedProvider | None,
                  on_stage: Callable[[str], Awaitable[None]] | None = None) -> Reply:
    """One question through the layers; the first that decides ends it. ``MemoryUnavailable``
    from the retriever propagates."""
    async def stage(name: str) -> None:
        if on_stage is not None:
            await on_stage(name)

    question = mask_sensitive(question)
    await stage("checking")
    guard = check(question, lexicon, has_context=bool(history))
    if guard.verdict is not None:
        return _fixed(guard.verdict, guard.rule)

    await stage("searching")
    previous = next((t.text for t in reversed(history) if t.role == "user"), None)
    sources, dropped = await gather_sources(
        retriever, project_id, retrieval_query(question, previous), persona, process_ids)
    if not sources:
        return _fixed("not_covered", dropped=dropped)
    refs = [s.ref for s in sources]

    await stage("writing")
    if provider is not None:
        canary = secrets.token_hex(8)
        system, user = render_prompt(question, sources, [(t.role, t.text) for t in history], persona, canary)
        try:
            data, _ = await ask_json(
                provider, "analyse", None, system, user, EXIQOIAN_SCHEMA,
                tags={"feature": "exiqoian", "project": project_id, "persona": persona},
                max_tokens=ANSWER_MAX_TOKENS)
        except (ProviderError, JsonOutputError):
            data = None
        if data is not None:
            verdict = data.get("verdict")
            if verdict != "answer":
                verdict = verdict if verdict in _ALLOWED_DECLINES else "not_covered"
                return _fixed(verdict, used_model=True, sources=refs, dropped=dropped)
            first = _FIRST_SENTENCE.match(system)
            v, cited, rule = validate_answer(str(data.get("answer", "")), sources, canary,
                                             first.group() if first else system)
            return Reply(verdict=v, cited=cited, rule=rule, used_model=True, sources=refs,
                         dropped_sources=dropped)

    composed = compose_without_model(sources)
    cited, _ = from_markers(composed, {s.marker: s.ref for s in sources})
    if not cited.refs:
        return _fixed("not_covered", dropped=dropped)
    return Reply(verdict="answer", cited=cited, used_model=False, sources=refs, dropped_sources=dropped)
