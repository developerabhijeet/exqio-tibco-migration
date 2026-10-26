"""The cited-text model shared by ExiQoian answers and every phase's claims (spec section 5).

Span offsets are Unicode code points (Python string indices); the web converts with
``Array.from(text)``."""
from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, Field, model_validator

UiPhase = Literal["upload", "discover", "validate", "design", "build", "verify", "deploy", "memory", "iteration"]
UI_PHASES: tuple[str, ...] = get_args(UiPhase)

LABEL_MAX = 60
PREVIEW_MAX = 280


class CiteTarget(BaseModel):
    # page: a screen that is no phase (/settings, /history); ``path`` is its route (ruling X18)
    kind: Literal["activity", "step", "memory", "file", "page"]
    step: UiPhase | None = None
    process_id: str | None = None  # API process id
    activity_id: str | None = None
    path: str | None = None
    line: int | None = None
    uid: str | None = None  # memory uid
    hash: str | None = None  # step or page anchor: "#tests", "#infra", "#parity", "#scenarios", ...


class CiteRef(BaseModel):
    id: str  # "c1", "c2", ...: unique within one CitedText
    phase: UiPhase
    # rule|finding|summary|decision|activity|process|code|test|error|gate|pattern|note|evidence
    kind: str
    label: str = Field(max_length=LABEL_MAX)
    preview: str = Field(max_length=PREVIEW_MAX)  # masked
    memory_uid: str | None = None
    revision: int | None = None
    verified: bool | None = None
    reason: str | None = None
    target: CiteTarget


class CiteSpan(BaseModel):
    start: int
    end: int
    refs: list[str]


class CitedText(BaseModel):
    text: str
    spans: list[CiteSpan] = []
    refs: list[CiteRef] = []

    @model_validator(mode="after")
    def _invariants(self) -> CitedText:
        n = len(self.text)
        for s in self.spans:
            if not 0 <= s.start < s.end <= n:
                raise ValueError(
                    f"span {s.start}..{s.end} is out of bounds: need 0 <= start < end <= {n}")
        for a, b in zip(self.spans, self.spans[1:]):
            if b.start < a.end:
                raise ValueError(
                    f"spans must be sorted and must not overlap: {a.start}..{a.end} then "
                    f"{b.start}..{b.end}")
        ids = [r.id for r in self.refs]
        if len(set(ids)) != len(ids):
            raise ValueError(f"ref ids must be unique, got {ids}")
        known = set(ids)
        used: set[str] = set()
        for s in self.spans:
            if not s.refs:
                raise ValueError(f"span {s.start}..{s.end} names no ref")
            for rid in s.refs:
                if rid not in known:
                    raise ValueError(f"span {s.start}..{s.end} names unknown ref {rid!r}")
            used.update(s.refs)
        unused = [rid for rid in ids if rid not in used]
        if unused:
            raise ValueError(f"refs unused by any span: {unused}")
        return self

    @classmethod
    def plain(cls, text: str) -> CitedText:
        return cls(text=text)


def cut(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` code points, an ellipsis marking the cut."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
