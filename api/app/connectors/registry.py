"""Which class serves each connector kind, and the views Settings and the UI read.

Tasks 3 to 6 ``register`` their classes; a kind with none yet is served by ``_Pending``, whose
test says so. Every connector derives from ``BaseConnector``: the merged configuration, a
``ready()`` check (enabled, credential mounted) and a ``client()`` over the allowlist that reads
its credential on every request.
"""
from __future__ import annotations

import base64
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.app.connectors.base import (
    KINDS,
    ConnectionTest,
    ConnectorDisabled,
    ConnectorError,
    ConnectorKind,
    ConnectorMisconfigured,
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
    from api.app.connectors.confluence import ConfluencePublisher
    from api.app.connectors.jira import JiraConnector


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
        """``ConnectorDisabled`` or ``ConnectorNotConfigured`` (``ConnectorMisconfigured`` for an
        install URL off its own allowlist) before any request is made."""
        if not self.enabled:
            raise ConnectorDisabled(self.kind)
        problem = self.config.install_problem()
        if problem:
            raise ConnectorMisconfigured(self.kind, problem)
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


class AtlassianConnector(BaseConnector):
    """Confluence and Jira share the Atlassian credential (separate URLs): ``Bearer <PAT>`` on Data
    Center, basic ``ATLASSIAN_USER:ATLASSIAN_TOKEN`` on Cloud."""

    @property
    def flavor(self) -> str:
        return getattr(self.config.install, "flavor", "datacenter")

    def auth_header(self) -> dict[str, str]:
        if self.flavor == "cloud":
            pair = f"{self.credential('user')}:{self.credential()}".encode()
            return {"Authorization": f"Basic {base64.b64encode(pair).decode()}"}
        return {"Authorization": f"Bearer {self.credential()}"}

    @property
    def confluence(self) -> ConfluencePublisher:
        from api.app.connectors.confluence import ConfluencePublisher  # imports this module

        return ConfluencePublisher(self)

    @property
    def jira(self) -> JiraConnector:
        from api.app.connectors.jira import JiraConnector  # imports this module

        return JiraConnector(self)

    async def test(self) -> ConnectionTest:
        """Confluence and Jira, each that has a URL: ok when every one answered; the account is the
        first one's, the message names both."""
        products: list[tuple[str, Callable[[], Any]]] = []
        if self.config.url("confluence_url"):
            products.append(("Confluence", lambda: self.confluence.test()))
        if self.config.url("jira_url"):
            products.append(("Jira", lambda: self.jira.test()))
        if not products:
            raise ConnectorError("the Atlassian connector has no Confluence or Jira URL "
                                 "(connectors.atlassian.confluenceUrl, jiraUrl)")
        results: list[ConnectionTest] = []
        for name, run in products:
            try:
                results.append(await run())
            except ConnectorError as exc:
                results.append(ConnectionTest(ok=False, message=f"{name}: {exc.message}"))
        return ConnectionTest(ok=all(r.ok for r in results),
                              account=next((r.account for r in results if r.account), None),
                              site=next((r.site for r in results if r.site), None),
                              message="; ".join(r.message for r in results if r.message))


class _Pending(BaseConnector):
    async def test(self) -> ConnectionTest:
        return ConnectionTest(ok=False, message="not implemented yet")


_CLASSES: dict[str, type[BaseConnector]] = {"atlassian": AtlassianConnector}


def register(kind: ConnectorKind, cls: type[BaseConnector]) -> None:
    _CLASSES[kind] = cls


def _load_builtin() -> None:
    """Import the connector modules (each ``register``s its class); deferred, as they import this one."""
    from api.app.connectors import figma, github  # noqa: F401


def get_connector(kind: ConnectorKind, *, settings: Settings, stored: dict[str, Any]) -> BaseConnector:
    _load_builtin()
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
