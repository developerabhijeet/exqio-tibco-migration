"""API models for Settings → Connectors and the connector links."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class _Patch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None


class GithubPatch(_Patch):
    base_url: str | None = None
    api_url: str | None = None
    default_owner: str | None = None


class AtlassianPatch(_Patch):
    confluence_url: str | None = None
    jira_url: str | None = None
    space_key: str | None = None
    parent_page_id: str | None = None
    project_key: str | None = None
    issue_type: str | None = None
    labels: list[str] | None = None


class FigmaPatch(_Patch):
    api_url: str | None = None
    web_url: str | None = None
    file_keys: list[str] | None = None


class ConnectorsPatch(BaseModel):
    """A partial update of the admin's layer; a field left out keeps its stored value. There are no
    secret fields: credentials come only from Key Vault mounted files."""

    model_config = ConfigDict(extra="forbid")

    github: GithubPatch | None = None
    atlassian: AtlassianPatch | None = None
    figma: FigmaPatch | None = None


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    process_id: str
    kind: str
    object_type: str
    local_ref: str
    remote_id: str
    remote_url: str
    remote_version: int | None
    stale: bool
    created_by: str
    created_at: datetime
    updated_at: datetime
