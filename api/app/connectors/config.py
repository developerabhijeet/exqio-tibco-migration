"""The two configuration layers of the connectors and the per-call secret read.

Install layer (``InstallConnector``): the chart's ``connectors.<kind>`` values, passed as the
``CONNECTORS`` JSON (camelCase keys, as in the values file). It is the ceiling: a connector the
install leaves off cannot be turned on, and every URL must have a host in its ``allowedHosts``.

Application layer (``AppConnector``): what an admin sets in Settings → Connectors, stored in the
``settings`` table under ``connectors``: the toggle, URL overrides within the allowlist, and
defaults. It holds no secret field. ``ConnectorConfig`` is the two merged.
"""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel

from api.app.connectors.base import KINDS, ConnectorKind
from api.app.secrets import read_secret_file

if TYPE_CHECKING:
    from api.app.config import Settings

LOOPBACK = "127.0.0.1"
STORED_KEY = "connectors"
LAST_TEST_KEY = "connectors_last_test"


class _Install(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    enabled: bool = False
    allowed_hosts: list[str] = Field(default_factory=list)
    cidrs: list[str] = Field(default_factory=list)


class GithubInstall(_Install):
    base_url: str = ""
    api_url: str = ""  # empty: <baseUrl>/api/v3 (GitHub Enterprise Server)
    commit_email: str = ""


class AtlassianInstall(_Install):
    flavor: Literal["datacenter", "cloud"] = "datacenter"
    confluence_url: str = ""
    jira_url: str = ""


class FigmaInstall(_Install):
    api_url: str = ""
    web_url: str = ""
    image_hosts: list[str] = Field(default_factory=list)


InstallConnector = GithubInstall | AtlassianInstall | FigmaInstall
INSTALL_MODELS: dict[str, type[_Install]] = {"github": GithubInstall, "atlassian": AtlassianInstall,
                                             "figma": FigmaInstall}


class _App(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False


class GithubApp(_App):
    base_url: str = ""  # empty: the install's
    api_url: str = ""
    default_owner: str = ""


class AtlassianApp(_App):
    confluence_url: str = ""
    jira_url: str = ""
    space_key: str = ""
    parent_page_id: str = ""
    project_key: str = ""
    issue_type: str = "Task"
    labels: list[str] = Field(default_factory=lambda: ["exiqo"])


class FigmaApp(_App):
    api_url: str = ""
    web_url: str = ""
    file_keys: list[str] = Field(default_factory=list)


AppConnector = GithubApp | AtlassianApp | FigmaApp
APP_MODELS: dict[str, type[_App]] = {"github": GithubApp, "atlassian": AtlassianApp, "figma": FigmaApp}

#: URL fields per kind: the ones requests go to (host checked) and the ones only shown as links.
REQUEST_URLS: dict[str, tuple[str, ...]] = {"github": ("base_url", "api_url"),
                                            "atlassian": ("confluence_url", "jira_url"),
                                            "figma": ("api_url",)}
LINK_URLS: dict[str, tuple[str, ...]] = {"github": (), "atlassian": (), "figma": ("web_url",)}

#: Secret role -> ``Settings`` field per kind (``user`` only for Atlassian cloud).
SECRET_FIELDS: dict[str, dict[str, str]] = {
    "github": {"token": "github_token"},
    "atlassian": {"token": "atlassian_token", "user": "atlassian_user"},
    "figma": {"token": "figma_token"},
}


class ConnectorConfig(BaseModel):
    """The install ceiling and the admin's layer, merged."""

    kind: ConnectorKind
    install: GithubInstall | AtlassianInstall | FigmaInstall
    app: GithubApp | AtlassianApp | FigmaApp
    allow_insecure_loopback: bool = False

    @property
    def install_enabled(self) -> bool:
        return self.install.enabled

    @property
    def enabled(self) -> bool:
        return self.install.enabled and self.app.enabled

    @property
    def allowed_hosts(self) -> list[str]:
        return list(self.install.allowed_hosts)

    def url(self, field: str) -> str:
        """The admin's override of a URL field, else the install's ("" when neither is set)."""
        return (getattr(self.app, field, "") or getattr(self.install, field, "") or "").rstrip("/")

    def urls(self) -> dict[str, str]:
        return {f: self.url(f) for f in (*REQUEST_URLS[self.kind], *LINK_URLS[self.kind])}

    def secret_roles(self) -> dict[str, str]:
        """The secrets this connector needs: role -> ``Settings`` field."""
        roles = dict(SECRET_FIELDS[self.kind])
        if self.kind == "atlassian" and getattr(self.install, "flavor", "") != "cloud":
            roles.pop("user")
        return roles


def _json(text: str, var: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{var} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{var} must be a JSON object keyed by connector")  # noqa: TRY004  one error type for bad config
    return value


def load_install(settings: Settings) -> dict[str, InstallConnector]:
    """The install layer from ``CONNECTORS``; a kind it leaves out (or an empty value) is off."""
    raw = _json(settings.connectors, "CONNECTORS")
    out: dict[str, InstallConnector] = {}
    for kind in KINDS:
        try:
            out[kind] = INSTALL_MODELS[kind].model_validate(raw.get(kind) or {})
        except ValidationError as exc:
            raise ValueError(f"CONNECTORS.{kind}: {exc.errors()[0]['msg']}") from exc
    return out


def secret_names(settings: Settings) -> dict[str, dict[str, str]]:
    """``CONNECTOR_SECRET_NAMES``: ``{kind: {"token": <Key Vault name>, "user": ...}}``."""
    raw = _json(settings.connector_secret_names, "CONNECTOR_SECRET_NAMES")
    return {kind: {str(k): str(v) for k, v in (raw.get(kind) or {}).items() if v} for kind in KINDS}


def load_app(kind: str, stored: dict[str, Any]) -> AppConnector:
    block = (stored.get(STORED_KEY) or {}).get(kind) or {}
    return APP_MODELS[kind].model_validate(block)


def merged(kind: ConnectorKind, settings: Settings, stored: dict[str, Any]) -> ConnectorConfig:
    return ConnectorConfig(kind=kind, install=load_install(settings)[kind], app=load_app(kind, stored),
                           allow_insecure_loopback=settings.connectors_allow_insecure_loopback)


def host_allowed(netloc: str, allowed: list[str], scheme: str = "https") -> bool:
    """``netloc`` (``host[:port]``) matches an entry: ``host:port`` exactly, or a bare ``host`` on
    the scheme's default port. Case is ignored."""
    parts = urlsplit(f"//{netloc}")
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        return False
    default = 443 if scheme == "https" else 80
    for entry in allowed:
        e = urlsplit(f"//{entry.strip()}")
        try:
            e_port = e.port
        except ValueError:
            continue
        if (e.hostname or "").lower() != host or not host:
            continue
        if e_port is None and port in (None, default):
            return True
        if e_port is not None and e_port == (port or default):
            return True
    return False


def check_url(field: str, url: str, allowed: list[str], *, allow_insecure_loopback: bool = False,
              check_host: bool = True) -> None:
    """``https`` (``http://127.0.0.1`` only with the loopback flag) and a host in ``allowed``."""
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018  a malformed port raises here
    except ValueError:
        raise ValueError(f"{field}: not a valid URL") from None
    if "@" in parts.netloc:
        raise ValueError(f"{field}: credentials in the URL are not allowed")
    if parts.scheme == "http" and allow_insecure_loopback and parts.hostname == LOOPBACK:
        pass
    elif parts.scheme != "https" or not parts.netloc:
        raise ValueError(f"{field}: https required")
    if check_host and not host_allowed(parts.netloc, allowed, parts.scheme):
        raise ValueError(f"{field}: host {parts.netloc} not in allowedHosts")


def validate_app(kind: str, install: InstallConnector, patch: dict[str, Any],
                 current: dict[str, Any] | None = None, *,
                 allow_insecure_loopback: bool = False) -> AppConnector:
    """The admin's layer after ``patch`` is applied on ``current``. ``ValueError`` (API: 422) when
    it would exceed the install: turning on a connector the install leaves off, or a URL that is
    not https or whose host is not in ``allowedHosts``. Only the fields in ``patch`` are checked;
    the stored ones were checked when they were saved."""
    if patch.get("enabled") and not install.enabled:
        raise ValueError(f"enable it in the install first (connectors.{kind}.enabled)")
    for field in REQUEST_URLS[kind] + LINK_URLS[kind]:
        value = str(patch.get(field) or "").strip()
        if field in patch:
            patch = {**patch, field: value}
        if value:
            check_url(field, value, install.allowed_hosts, allow_insecure_loopback=allow_insecure_loopback,
                      check_host=field in REQUEST_URLS[kind])
    if kind == "atlassian" and "issue_type" in patch and not str(patch["issue_type"] or "").strip():
        raise ValueError("issue_type must not be empty")
    try:
        return APP_MODELS[kind].model_validate({**(current or {}), **patch})
    except ValidationError as exc:
        raise ValueError(f"{kind}: {exc.errors()[0]['msg']}") from exc


def secret(settings_field: str, settings: Settings | None = None) -> str:
    """A connector secret, read now: the file named by ``<FIELD>_FILE`` (so a token the Key Vault
    CSI driver rotates is used on the next call, without a restart), else the plain field
    (``settings`` when given, else the ``<FIELD>`` variable). A configured file that is missing
    reads as no credential."""
    path = os.environ.get(f"{settings_field.upper()}_FILE", "")
    if path:
        try:
            return read_secret_file(path).strip()
        except OSError:
            return ""
    if settings is not None:
        return str(getattr(settings, settings_field, "") or "").strip()
    return os.environ.get(settings_field.upper(), "").strip()


def secret_present(settings_field: str, settings: Settings | None = None) -> bool:
    return bool(secret(settings_field, settings))


def file_var(settings_field: str) -> str:
    return f"{settings_field.upper()}_FILE"

