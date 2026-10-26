"""``[S<n>]`` source markers in a model answer to cited spans (spec section 5)."""
from __future__ import annotations

import re

from engine.citations.model import CitedText, CiteRef, CiteSpan

# One bracketed group (`[S1]`, `[S2, S5]`) with the whitespace before it; consecutive groups
# match one by one and merge onto the same span.
MARKER = re.compile(r"\s*\[(S\d+(?:\s*,\s*S\d+)*)\]")
# A sentence ends at `.`, `!` or `?` followed by whitespace or the text end, or at a line break.
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)|\n")


def sentence_bounds(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` code-point offsets of each sentence, trimmed of surrounding whitespace
    and including its closing punctuation."""
    bounds: list[tuple[int, int]] = []
    start = 0

    def add(a: int, b: int) -> None:
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if a < b:
            bounds.append((a, b))

    for m in _SENTENCE_END.finditer(text):
        add(start, m.start() if m.group() == "\n" else m.end())
        start = m.end()
    add(start, len(text))
    return bounds


def _strip(text: str, a: int, b: int) -> tuple[int, int]:
    while a < b and (text[a].isspace() or text[a] in ",;:"):
        a += 1
    while b > a and text[b - 1].isspace():
        b -= 1
    return a, b


def from_markers(answer: str, refs_by_marker: dict[str, CiteRef]) -> tuple[CitedText, list[str]]:
    """Remove the markers (and the whitespace before each) and cite, for each marker group,
    the sentence it ends: from the sentence start (or the end of an earlier span in the same
    sentence) to the last non-space character before the marker. A marker before the closing
    punctuation leaves the punctuation out (`claim [S1].` cites `claim`).

    Returns the cited text and the markers that named no ref (dropped, in order, once each).
    Ref ids are renumbered ``c1..cn`` in order of first appearance."""
    parts: list[str] = []
    groups: list[tuple[int, list[str]]] = []  # (offset in the cleaned text, marker names)
    last = length = 0
    for m in MARKER.finditer(answer):
        parts.append(answer[last:m.start()])
        length += m.start() - last
        last = m.end()
        groups.append((length, [s.strip() for s in m.group(1).split(",")]))
    parts.append(answer[last:])
    text = "".join(parts)
    bounds = sentence_bounds(text)

    ids: dict[str, str] = {}  # marker -> new ref id
    refs: list[CiteRef] = []
    dropped: list[str] = []
    spans: list[CiteSpan] = []
    for offset, markers in groups:
        known: list[str] = []
        for name in markers:
            if name not in refs_by_marker:
                if name not in dropped:
                    dropped.append(name)
            elif name not in known:
                known.append(name)
        end = offset
        while end > 0 and text[end - 1].isspace():
            end -= 1
        sentence = next(((a, b) for a, b in bounds if a < end <= b), None)
        if not known or sentence is None:
            continue
        start = sentence[0]
        if spans and spans[-1].end == end:  # consecutive markers on one sentence
            span = spans[-1]
        else:
            if spans and spans[-1].end > start:  # an earlier marker in the same sentence
                start = spans[-1].end
            start, end = _strip(text, start, end)
            if start >= end:
                continue
            span = CiteSpan(start=start, end=end, refs=[])
            spans.append(span)
        for name in known:
            if name not in ids:
                ids[name] = f"c{len(ids) + 1}"
                refs.append(refs_by_marker[name].model_copy(update={"id": ids[name]}))
            if ids[name] not in span.refs:
                span.refs.append(ids[name])
    return CitedText(text=text, spans=spans, refs=refs), dropped
