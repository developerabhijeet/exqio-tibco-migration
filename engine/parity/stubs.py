"""Scenario-defined responses for external calls (``http.invoke``, ``rest.invoke``, ...).

Design spec 6.2: a registry keyed by resource and path. A response whose ``delay_ms`` exceeds
the call's timeout is a timeout: ``call`` raises ``TimeoutError`` (virtual time, nothing
sleeps), which is how resilience branches such as the bureau single-bureau fallback are
driven. Every call is recorded in ``calls`` so the evaluator can report ``service_called``
side effects.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Literal

from engine.parity.models import StubResponse

DEFAULT_TIMEOUT_MS = 30_000


@dataclass
class StubCall:
    resource_id: str
    path: str
    body: Any
    outcome: Literal["ok", "timeout"]
    status: int | None = None


class StubRegistry:
    def __init__(self, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> None:
        self.timeout_ms = timeout_ms
        self._stubs: dict[str, list[tuple[str, StubResponse]]] = {}
        self.calls: list[StubCall] = []

    @classmethod
    def from_stub_responses(
        cls, stub_responses: dict[str, StubResponse], timeout_ms: int = DEFAULT_TIMEOUT_MS
    ) -> StubRegistry:
        """Build from ``Scenario.stub_responses``.

        Keys are ``"<resource_id>/<path>"`` (the first ``/`` separates them, so the path is
        ``"/<path>"``) or a bare ``"<resource_id>"``, which answers every path (``"*"``).
        """
        reg = cls(timeout_ms)
        for key, response in stub_responses.items():
            resource, sep, rest = key.partition("/")
            reg.register(resource, "/" + rest if sep else "*", response)
        return reg

    def register(self, resource_id: str, path: str, response: StubResponse) -> None:
        """Register (or replace) the response for a resource and path; ``path`` may be a glob."""
        entries = [e for e in self._stubs.get(resource_id, []) if e[0] != path]
        entries.append((path, response))
        self._stubs[resource_id] = entries

    def _lookup(self, resource_id: str, path: str) -> StubResponse:
        entries = self._stubs.get(resource_id, [])
        for pattern, response in entries:
            if pattern == path:
                return response
        for pattern, response in entries:
            if fnmatchcase(path, pattern):
                return response
        raise LookupError(f"no stub registered for {resource_id} {path}")

    def call(
        self, resource_id: str, path: str, body: Any, timeout_ms: int | None = None
    ) -> StubResponse:
        """Return the registered response.

        Raises ``TimeoutError`` when the response's ``delay_ms`` exceeds the timeout (the
        evaluator catches it to drive resilience branches) and ``LookupError`` when no stub
        matches. It never returns the exception.
        """
        limit = self.timeout_ms if timeout_ms is None else timeout_ms
        response = self._lookup(resource_id, path)
        if response.delay_ms > limit:
            self.calls.append(StubCall(resource_id, path, body, "timeout"))
            raise TimeoutError(f"{resource_id} {path} timed out after {limit} ms")
        self.calls.append(StubCall(resource_id, path, body, "ok", response.status))
        return response


# --- WireMock stubs -------------------------------------------------------------------


@dataclass
class _Mapping:
    method: str
    path: str
    priority: int
    body_patterns: list[dict[str, Any]]
    response: StubResponse


def _json_path(body: Any, expression: str) -> Any:
    """Tiny ``$.a.b`` JSONPath (what the estate's WireMock mappings use)."""
    if not expression.startswith("$"):
        return None
    cur = body
    for part in [p for p in expression[1:].split(".") if p]:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


class WireMockStubs(StubRegistry):
    """A :class:`StubRegistry` answering from WireMock mapping files (``Stubs/*.json``).

    Requests are matched like WireMock does for the subset the estate uses: ``urlPath``,
    ``method`` and ``bodyPatterns`` with ``matchesJsonPath`` + ``equalTo``; the lowest
    ``priority`` wins. ``fixedDelayMilliseconds`` becomes ``delay_ms``, so a delay above the
    resource timeout is a timeout. The resource id is not used for matching (the mapping files
    are per service and paths are distinct); an explicit ``register`` still takes precedence.
    """

    def __init__(self, timeout_ms: int | None = None) -> None:
        if timeout_ms is None:
            super().__init__()
        else:
            super().__init__(timeout_ms)
        self.mappings: list[_Mapping] = []

    @classmethod
    def from_folder(cls, folder: Path, timeout_ms: int | None = None) -> WireMockStubs:
        reg = cls(timeout_ms)
        for f in sorted(Path(folder).glob("*.json")):
            doc = json.loads(f.read_text(encoding="utf-8"))
            for m in doc.get("mappings", [doc] if "request" in doc else []):
                req, resp = m.get("request", {}), m.get("response", {})
                body = resp.get("jsonBody")
                if body is None and resp.get("body"):
                    try:
                        body = json.loads(resp["body"])
                    except ValueError:
                        body = {"text": resp["body"]}
                reg.mappings.append(_Mapping(
                    method=str(req.get("method", "ANY")).upper(),
                    path=req.get("urlPath") or req.get("url") or "",
                    priority=int(m.get("priority", 5)),
                    body_patterns=req.get("bodyPatterns", []),
                    response=StubResponse(status=int(resp.get("status", 200)), body=body,
                                          delay_ms=int(resp.get("fixedDelayMilliseconds", 0))),
                ))
        reg.mappings.sort(key=lambda m: m.priority)
        return reg

    def match(self, resource_id: str, path: str, body: Any) -> StubResponse:
        try:
            return super()._lookup(resource_id, path)
        except LookupError:
            pass
        for m in self.mappings:
            if m.path != path:
                continue
            if all(self._pattern_ok(p, body) for p in m.body_patterns):
                return m.response
        raise LookupError(f"no WireMock mapping for {path} with body {body!r}")

    @staticmethod
    def _pattern_ok(pattern: dict[str, Any], body: Any) -> bool:
        jp = pattern.get("matchesJsonPath")
        if isinstance(jp, dict):
            value = _json_path(body if isinstance(body, dict) else {}, jp.get("expression", ""))
            if "equalTo" in jp:
                return value is not None and str(value) == str(jp["equalTo"])
            return value is not None
        if isinstance(jp, str):
            return _json_path(body if isinstance(body, dict) else {}, jp) is not None
        if "equalToJson" in pattern:
            want = pattern["equalToJson"]
            return body == (json.loads(want) if isinstance(want, str) else want)
        return True

    def _lookup(self, resource_id: str, path: str) -> StubResponse:
        try:
            return super()._lookup(resource_id, path)
        except LookupError:
            for m in self.mappings:
                if m.path == path and not m.body_patterns:
                    return m.response
            raise

    def call(self, resource_id: str, path: str, body: Any, timeout_ms: int | None = None) -> StubResponse:
        limit = self.timeout_ms if timeout_ms is None else timeout_ms
        response = self.match(resource_id, path, body)
        if response.delay_ms > limit:
            self.calls.append(StubCall(resource_id, path, body, "timeout"))
            raise TimeoutError(f"{resource_id} {path} timed out after {limit} ms")
        self.calls.append(StubCall(resource_id, path, body, "ok", response.status))
        return response

