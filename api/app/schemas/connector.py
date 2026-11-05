"""API models for Settings → Connectors and the connector links."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from api.app.connectors.jira import IssueItem


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


class GithubPushIn(BaseModel):
    """``POST /builds/{id}/github/push``: the repository (``owner/name``), the branch (default
    ``exiqo/<process slug>/build-<build id[:8]>``; ``exiqo/`` branches only), whether to open the
    pull request, and its base (default: the repository's default branch)."""

    model_config = ConfigDict(extra="forbid")

    repo: str = Field(min_length=3, max_length=201)
    branch: str | None = None
    open_pr: bool = True
    base: str | None = None


class JiraIssuesIn(BaseModel):
    """``POST /processes/{id}/jira/issues``: 1 to 50 items; ``create_again`` replaces the issues of
    stale links (deleted in Jira) instead of skipping them."""

    model_config = ConfigDict(extra="forbid")

    items: list[IssueItem] = Field(min_length=1, max_length=50)
    create_again: bool = False


class ConfluencePublishIn(BaseModel):
    """``POST /processes/{id}/confluence/publish`` (the body is optional): ``create_again`` makes a
    new page when the linked one was deleted in Confluence (its link is stale) instead of refusing."""

    model_config = ConfigDict(extra="forbid")

    create_again: bool = False


class JobAccepted(BaseModel):
    job_id: str


class FigmaFramesIn(BaseModel):
    """``POST /processes/{id}/figma/frames``: 1 to 20 frames (node ids) of one Figma file."""

    model_config = ConfigDict(extra="forbid")

    file_key: str = Field(min_length=1, max_length=64)
    node_ids: list[str] = Field(min_length=1, max_length=20)


class FrameLinkOut(BaseModel):
    """An attached frame: what the design tab shows and the designer reads. ``url`` is empty when
    the install has no Figma web URL (show the name only); ``has_image`` says whether a thumbnail
    is stored (``GET /figma/frames/{id}/image``), else ``image_note`` says why not."""

    id: str
    file_key: str
    node_id: str
    name: str
    page: str
    text: str
    url: str
    has_image: bool
    image_note: str | None = None
    stale: bool
    updated_at: datetime
