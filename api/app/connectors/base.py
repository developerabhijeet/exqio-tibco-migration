"""Shared vocabulary of the connectors: kinds, the connection-test result, the errors."""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

ConnectorKind = Literal["github", "atlassian", "figma"]
KINDS: tuple[ConnectorKind, ...] = ("github", "atlassian", "figma")
NAMES: dict[str, str] = {"github": "GitHub", "atlassian": "Atlassian", "figma": "Figma"}


class ConnectionTest(BaseModel):
    ok: bool
    account: str | None = None
    site: str | None = None
    scopes: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    message: str = ""


class ConnectorDisabled(Exception):
    """The connector is off in the install or in Settings (API: 409)."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(f"the {NAMES.get(kind, kind)} connector is not enabled")


class ConnectorNotConfigured(Exception):
    """The connector's secret file is missing or empty (API: 409)."""

    def __init__(self, kind: str, secret_name: str) -> None:
        self.kind, self.secret_name = kind, secret_name
        super().__init__(f"the {NAMES.get(kind, kind)} connector has no credential: mount {secret_name}")


class ConnectorMisconfigured(ConnectorNotConfigured):
    """The install layer cannot be used (an install URL off its own ``allowedHosts``; API: 409)."""

    def __init__(self, kind: str, problem: str) -> None:
        self.kind, self.secret_name = kind, ""
        Exception.__init__(self, f"the {NAMES.get(kind, kind)} connector is not ready: {problem}")


class ConnectorError(Exception):
    """The remote service failed or refused; ``status`` is its HTTP status when there was one.
    The message is already redacted."""

    def __init__(self, message: str, status: int | None = None) -> None:
        self.message, self.status = message, status
        super().__init__(message)


class HostNotAllowed(ConnectorError):
    """A request or a redirect to a host outside the connector's allowed hosts, or not https."""

    def __init__(self, host: str, reason: str | None = None) -> None:
        self.host = host
        super().__init__(reason or f"host {host} is not in this connector's allowed hosts")


class Connector(Protocol):
    kind: ConnectorKind

    @property
    def enabled(self) -> bool: ...

    async def test(self) -> ConnectionTest: ...
