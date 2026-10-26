"""Which class serves each connector kind, and the views Settings and the UI read.

Tasks 3 to 6 ``register`` their classes; a kind with none yet is served by ``_Pending``, whose
test says so. Every connector derives from ``BaseConnector``: the merged configuration, a
``ready()`` check (enabled, credential mounted) and a ``client()`` over the allowlist that reads
its credential on every request.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.app.connectors.base import (
    KINDS,
    ConnectionTest,
    ConnectorDisabled,
    ConnectorKind,
    ConnectorNotConfigured,
)
from api.app.connectors.config import (
    LAST_TEST_KEY,
    ConnectorConfig,
    file_var,
    merged,
    secret,
    secret_names,
)
from api.app.connectors.http import ConnectorClient
from api.app.services.settings import load_stored

if TYPE_CHECKING:
    from api.app.config import Settings


class BaseConnector:
    kind: ConnectorKind

    def __init__(self, config: ConnectorConfig, settings: Settings) -> None:
        self.kind = config.kind
        self.config = config
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def secret_name(self, role: str) -> str:
        """What the admin has to mount: the Key Vault object name when the chart gave it."""
        field = self.config.secret_roles()[role]
        return secret_names(self.settings)[self.kind].get(role) or file_var(field)

    def credential(self, role: str = "token") -> str:
        """The secret, read now; ``ConnectorNotConfigured`` when missing or empty."""
        value = secret(self.config.secret_roles()[role], self.settings)
        if not value:
            raise ConnectorNotConfigured(self.kind, self.secret_name(role))
        return value

    def ready(self) -> None:
        """``ConnectorDisabled`` or ``ConnectorNotConfigured`` before any request is made."""
        if not self.enabled:
            raise ConnectorDisabled(self.kind)
        for role in self.config.secret_roles():
            self.credential(role)

    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.credential()}"}

    def client(self, base_url: str, *, auth: Callable[[], dict[str, str]] | None = None,
               allowed_hosts: list[str] | None = None) -> ConnectorClient:
        """A client over the allowlist; ``ConnectorDisabled`` when the connector is off, so no code
        path reaches the network without the switch (``ready()`` also checks the credential)."""
        if not self.enabled:
            raise ConnectorDisabled(self.kind)
        return ConnectorClient(base_url, allowed_hosts if allowed_hosts is not None else self.config.allowed_hosts,
                               auth or self.auth_header,
                               allow_insecure_loopback=self.config.allow_insecure_loopback,
                               user_agent=f"{self.settings.brand_name}-Migrate",
                               ca_bundle=self.settings.connectors_ca_bundle or None)

    async def test(self) -> ConnectionTest:
        raise NotImplementedError


class _Pending(BaseConnector):
    async def test(self) -> ConnectionTest:
        return ConnectionTest(ok=False, message="not implemented yet")


_CLASSES: dict[str, type[BaseConnector]] = {}


def register(kind: ConnectorKind, cls: type[BaseConnector]) -> None:
    _CLASSES[kind] = cls


def get_connector(kind: ConnectorKind, *, settings: Settings, stored: dict[str, Any]) -> BaseConnector:
    if kind not in KINDS:
        raise ValueError(f"unknown connector {kind!r}")
    return _CLASSES.get(kind, _Pending)(merged(kind, settings, stored), settings)


async def connector_for(session: AsyncSession, settings: Settings, kind: ConnectorKind) -> BaseConnector:
    """The connector with the admin's layer as stored now (the table, not one process's memory)."""
    return get_connector(kind, settings=settings, stored=await load_stored(session))


def connectors_view(settings: Settings, stored: dict[str, Any], *, admin: bool) -> dict[str, Any]:
    """Settings → Connectors. Everyone sees the install and Settings flags; an admin also sees the
    URLs, allowlist, defaults, which secrets are mounted (never their values) and the last test."""
    names = secret_names(settings)
    last_tests = stored.get(LAST_TEST_KEY) or {}
    out: dict[str, Any] = {}
    for kind in KINDS:
        cfg = merged(kind, settings, stored)
        view: dict[str, Any] = {"install_enabled": cfg.install_enabled, "enabled": cfg.enabled}
        if admin:
            roles = cfg.secret_roles()
            install = cfg.install.model_dump(exclude={"enabled", "cidrs"})
            defaults = cfg.app.model_dump(exclude={"enabled", *cfg.urls()})
            view.update({
                "app_enabled": cfg.app.enabled,
                **cfg.urls(),
                "overrides": {f: getattr(cfg.app, f) for f in cfg.urls()},
                "install": install,
                "allowed_hosts": cfg.allowed_hosts,
                **defaults,
                "secret_names": {role: names[kind].get(role) or file_var(field) for role, field in roles.items()},
                "secrets_present": {role: bool(secret(field, settings)) for role, field in roles.items()},
                "last_test": last_tests.get(kind),
            })
        out[kind] = view
    return out


def enabled_flags(settings: Settings, stored: dict[str, Any]) -> dict[str, Any]:
    """``GET /connectors``: what the UI may show to any signed-in user."""
    out: dict[str, Any] = {}
    for kind in KINDS:
        cfg = merged(kind, settings, stored)
        flags: dict[str, Any] = {"enabled": cfg.enabled}
        if kind == "atlassian":
            flags["confluence"] = cfg.enabled and bool(cfg.url("confluence_url"))
            flags["jira"] = cfg.enabled and bool(cfg.url("jira_url"))
        out[kind] = flags
    return out
