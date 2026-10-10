"""Completeness bookkeeping shared by the BW5 and BW6 parsers.

Design spec section 4.1: a parser never drops anything silently. Every element
of a source tree is either mapped into the IR, explained (known and
intentionally not modelled, with a reason) or reported as unknown. Every native
activity type with no canonical mapping is reported too. ``report().ok`` is
true only when no file failed and nothing is unknown.
"""
from pydantic import BaseModel, Field

from engine.ir.models import UnknownElement


class ParseFailure(BaseModel):
    path: str
    error: str


class UnknownType(BaseModel):
    process_id: str
    native_type: str


class Explained(BaseModel):
    path: str
    reason: str


class CompletenessReport(BaseModel):
    files_found: int = 0
    files_parsed: int = 0
    failures: list[ParseFailure] = Field(default_factory=list)
    unknown_activity_types: list[UnknownType] = Field(default_factory=list)
    unknown_elements: list[UnknownElement] = Field(default_factory=list)
    explained: list[Explained] = Field(default_factory=list)
    ok: bool = True


class CompletenessCollector:
    """Accumulates completeness facts across every file of a scan."""

    def __init__(self) -> None:
        self._found: list[str] = []
        self._parsed: list[str] = []
        self._failures: list[ParseFailure] = []
        self._unknown_types: list[UnknownType] = []
        self._unknown: list[UnknownElement] = []
        self._explained: list[Explained] = []

    # ---- files
    def file_found(self, path: str) -> None:
        if path not in self._found:
            self._found.append(path)

    def file_parsed(self, path: str) -> None:
        self.file_found(path)
        if path not in self._parsed:
            self._parsed.append(path)

    def failure(self, path: str, error: str) -> None:
        self.file_found(path)
        self._failures.append(ParseFailure(path=path, error=error))

    # ---- elements and types
    def explain(self, path: str, reason: str) -> None:
        """A known element that is intentionally not modelled (and why)."""
        self._explained.append(Explained(path=path, reason=reason))

    def unknown(self, path: str, tag: str, reason: str) -> UnknownElement:
        item = UnknownElement(path=path, tag=tag, reason=reason)
        self._unknown.append(item)
        return item

    def unknown_type(self, process_id: str, native_type: str) -> None:
        item = UnknownType(process_id=process_id, native_type=native_type)
        if item not in self._unknown_types:
            self._unknown_types.append(item)

    # ---- result
    def report(self) -> CompletenessReport:
        ok = not (self._failures or self._unknown_types or self._unknown)
        return CompletenessReport(
            files_found=len(self._found),
            files_parsed=len(self._parsed),
            failures=list(self._failures),
            unknown_activity_types=list(self._unknown_types),
            unknown_elements=list(self._unknown),
            explained=list(self._explained),
            ok=ok,
        )
