"""A phase's claims to cited text: each ref anchors on the first sentence that names it (by
one of its hints), refs naming no sentence go on the last one, so no ref is ever lost.

Sentences are ``sentence_bounds``; a span leaves the closing punctuation out, as a marker
span from ``from_markers`` does (``claim [S1].`` cites ``claim``)."""
from __future__ import annotations

import re

from pydantic import BaseModel

from engine.citations.model import CitedText, CiteRef, CiteSpan
from engine.citations.spans import sentence_bounds

_CLOSING = ".!?"
# Word boundaries inside an identifier: `lookupDealer`, `R2Check`, `HTTPServer`.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


class HintedRef(BaseModel):
    ref: CiteRef
    hints: list[str] = []  # names that, found in a sentence, put the ref's chip there


def hint_variants(hint: str) -> list[str]:
    """``hint`` and its spaced prose form: ``LookupDealer`` -> ``Lookup Dealer``,
    ``num_of_pulls`` -> ``num of pulls``."""
    hint = hint.strip()
    if not hint:
        return []
    spaced = " ".join(_CAMEL.sub(" ", hint).replace("_", " ").replace("-", " ").split())
    return [hint] if spaced == hint else [hint, spaced]


def _pattern(hint: str) -> re.Pattern[str] | None:
    variants = hint_variants(hint)
    if not variants:
        return None
    alts = "|".join(r"\s+".join(re.escape(w) for w in v.split()) for v in variants)
    return re.compile(rf"(?<![\w])(?:{alts})(?![\w])", re.IGNORECASE)


def _sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans without their closing punctuation (kept when it is all there is)."""
    out = []
    for a, b in sentence_bounds(text):
        e = b
        while e > a and text[e - 1] in _CLOSING:
            e -= 1
        while e > a and text[e - 1].isspace():
            e -= 1
        out.append((a, e if e > a else b))
    return out


def _cited(text: str, refs: list[CiteRef], sentence_of: list[int],
           sentences: list[tuple[int, int]]) -> CitedText:
    """``refs[i]`` on ``sentences[sentence_of[i]]``; ids renumbered ``c1..cn`` in ``refs``
    order."""
    by_sentence: dict[int, list[str]] = {}
    for i, k in enumerate(sentence_of):
        by_sentence.setdefault(k, []).append(f"c{i + 1}")
    spans = [CiteSpan(start=sentences[k][0], end=sentences[k][1], refs=ids)
             for k, ids in sorted(by_sentence.items())]
    renumbered = [ref.model_copy(update={"id": f"c{i + 1}"}) for i, ref in enumerate(refs)]
    return CitedText(text=text, spans=spans, refs=renumbered)


def _require_sentences(text: str) -> list[tuple[int, int]]:
    sentences = _sentences(text)
    if not sentences:
        raise ValueError("cannot anchor citations on empty text")
    return sentences


def anchor(text: str, hinted: list[HintedRef]) -> CitedText:
    """Each ref on the first sentence matching one of its hints (whole words, any case, camel
    case and snake case also as spaced words), else on the last sentence. Refs sharing a
    sentence share one span. Empty text with refs raises: anchor on the item's key or label."""
    if not hinted:
        return CitedText.plain(text)
    sentences = _require_sentences(text)
    sentence_of = []
    for h in hinted:
        patterns = [p for p in map(_pattern, h.hints) if p]
        k = next((k for k, (a, b) in enumerate(sentences)
                  if any(p.search(text, a, b) for p in patterns)), len(sentences) - 1)
        sentence_of.append(k)
    return _cited(text, [h.ref for h in hinted], sentence_of, sentences)


def tail(text: str, refs: list[CiteRef]) -> CitedText:
    """Every ref on the last sentence (claims whose wording no longer names them)."""
    if not refs:
        return CitedText.plain(text)
    sentences = _require_sentences(text)
    return _cited(text, refs, [len(sentences) - 1] * len(refs), sentences)


def link_entities(text: str, entities: dict[str, CiteRef]) -> list[HintedRef]:
    """A hinted ref for each known name the text mentions (whole words, as ``anchor`` matches
    them), in order of first mention; names the text does not mention are skipped."""
    found: list[tuple[int, int, HintedRef]] = []
    for order, (name, ref) in enumerate(entities.items()):
        pattern = _pattern(name)
        m = pattern.search(text) if pattern else None
        if m:
            found.append((m.start(), order, HintedRef(ref=ref, hints=[name])))
    return [h for _, _, h in sorted(found, key=lambda f: (f[0], f[1]))]
