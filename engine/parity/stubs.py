"""Scenario-defined responses for external calls (``http.invoke``, ``rest.invoke``, ...).

Design spec 6.2: a registry keyed by resource and path. A response whose ``delay_ms`` exceeds
the call's timeout is a timeout: ``call`` raises ``TimeoutError`` (virtual time, nothing
sleeps), which is how resilience branches such as the bureau single-bureau fallback are
driven. Every call is recorded in ``calls`` so the evaluator can report ``service_called``
side effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
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
