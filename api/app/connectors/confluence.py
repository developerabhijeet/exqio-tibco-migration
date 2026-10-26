"""Confluence: publish a process's architecture page (spec section 4), Data Center or Cloud.

Two small adapters behind one ``ConfluencePublisher``:

- ``DataCenterAdapter``: REST v1 ``/rest/api/content`` (create ``POST``, update ``PUT`` with
  ``version.number + 1``), ``Authorization: Bearer <PAT>``.
- ``CloudAdapter``: REST v2 ``/wiki/api/v2/pages`` for pages (the space id looked up from its key),
  basic ``ATLASSIAN_USER:ATLASSIAN_TOKEN``.

Both attach through v1 (``.../child/attachment``, with ``X-Atlassian-Token: no-check``, which
Confluence's XSRF check requires on a multipart POST). Every request goes through the allowlisting
``ConnectorClient``; errors are redacted of the credential.

Publishing updates the linked page (its stored version + 1). A 409 means someone else saved a
version in between: the current version is fetched once and the update retried; a second 409 is
an error. Without a link (or when the linked page is gone) a page of the same title in the space
is updated, else a page is created under the parent page.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from api.app.connectors.base import ConnectionTest, ConnectorError
from api.app.connectors.confluence_page import (
    PageContext,
    attachment_name,
    design_attachment,
    page_title,
    render_architecture_page,
)
from api.app.connectors.http import ConnectorClient, header_secrets, redact

if TYPE_CHECKING:
    from api.app.connectors.registry import AtlassianConnector
    from api.app.models.connector import ConnectorLink

XSRF = {"X-Atlassian-Token": "no-check"}
JSON = {"Accept": "application/json"}


class ConfluenceHTTPError(ConnectorError):
    """An error answer; ``remote`` is Confluence's own message."""

    def __init__(self, message: str, status: int, remote: str) -> None:
        super().__init__(message, status)
        self.remote = remote


@dataclass
class Page:
    id: str
    version: int
    url: str


def _remote_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(data, dict):
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str((e or {}).get("title") or e) if isinstance(e, dict) else str(e) for e in errors)[:500]
        return str(data.get("message") or data)[:500]
    return str(data)[:300]


class _Http:
    def __init__(self, client: ConnectorClient, secrets: list[str], site: str) -> None:
        self.client, self.secrets, self.site = client, secrets, site

    async def call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        headers = {**JSON, **(kw.pop("headers", None) or {})}
        response = await self.client.request(method, path, headers=headers, **kw)
        if response.status_code >= 400:
            remote = redact(_remote_message(response), self.secrets)
            where = f"{method} {response.request.url.path}"
            raise ConfluenceHTTPError(f"Confluence {where}: HTTP {response.status_code}: {remote}",
                                      response.status_code, remote)
        return response

    def page_url(self, data: dict[str, Any], ui_base: str) -> str:
        """The page's link for people: ``_links.base + webui`` when that is on the configured site,
        else ``ui_base + webui`` (a link the platform shows must not point anywhere else)."""
        links = data.get("_links") or {}
        webui = str(links.get("webui") or "")
        if webui and not webui.startswith("/"):
            webui = f"/{webui}"
        url = f"{links.get('base') or ui_base}{webui}"
        if url == self.site or url.startswith(f"{self.site}/"):
            return url
        return f"{ui_base}{webui}"

    async def attach(self, api: str, page_id: str, filename: str, content: bytes) -> None:
        """Upload ``filename`` (a new version of it when the page already has one)."""
        base = f"{api}/content/{quote(page_id)}/child/attachment"
        found = (await self.call("GET", base, params={"filename": filename})).json() or {}
        existing = [a for a in found.get("results") or [] if a.get("title") == filename]
        files = {"file": (filename, content, "application/json")}
        data = {"minorEdit": "true", "comment": "design record"}
        url = f"{base}/{quote(str(existing[0]['id']))}/data" if existing else base
        await self.call("POST", url, headers=XSRF, files=files, data=data)


class DataCenterAdapter:
    """REST v1 at ``<confluenceUrl>/rest/api``."""

    api = "/rest/api"

    def __init__(self, http: _Http) -> None:
        self.http = http

    def _page(self, data: dict[str, Any]) -> Page:
        return Page(str(data["id"]), int((data.get("version") or {}).get("number") or 1),
                    self.http.page_url(data, self.http.site))

    async def current_user(self) -> dict[str, Any]:
        return (await self.http.call("GET", f"{self.api}/user/current")).json() or {}

    async def get(self, page_id: str) -> Page:
        return self._page((await self.http.call("GET", f"{self.api}/content/{quote(page_id)}",
                                                params={"expand": "version"})).json())

    async def find(self, space_key: str, title: str) -> Page | None:
        rows = ((await self.http.call("GET", f"{self.api}/content", params={
            "spaceKey": space_key, "title": title, "type": "page", "expand": "version"})).json() or {}).get("results")
        return self._page(rows[0]) if rows else None

    async def create(self, space_key: str, parent_id: str, title: str, body: str) -> Page:
        payload: dict[str, Any] = {"type": "page", "title": title, "space": {"key": space_key},
                                   "body": {"storage": {"value": body, "representation": "storage"}}}
        if parent_id:
            payload["ancestors"] = [{"id": parent_id}]
        return self._page((await self.http.call("POST", f"{self.api}/content", json=payload)).json())

    async def update(self, page_id: str, title: str, body: str, version: int, message: str) -> Page:
        payload = {"id": page_id, "type": "page", "title": title,
                   "version": {"number": version, "message": message},
                   "body": {"storage": {"value": body, "representation": "storage"}}}
        return self._page((await self.http.call("PUT", f"{self.api}/content/{quote(page_id)}", json=payload)).json())

    async def attach(self, page_id: str, filename: str, content: bytes) -> None:
        await self.http.attach(self.api, page_id, filename, content)


class CloudAdapter:
    """REST v2 at ``<site>/wiki/api/v2`` for pages; v1 at ``<site>/wiki/rest/api`` for the user and
    attachments."""

    v1 = "/wiki/rest/api"
    v2 = "/wiki/api/v2"

    def __init__(self, http: _Http) -> None:
        self.http = http
        self._space_ids: dict[str, str] = {}

    def _page(self, data: dict[str, Any]) -> Page:
        return Page(str(data["id"]), int((data.get("version") or {}).get("number") or 1),
                    self.http.page_url(data, f"{self.http.site}/wiki"))

    async def current_user(self) -> dict[str, Any]:
        return (await self.http.call("GET", f"{self.v1}/user/current")).json() or {}

    async def space_id(self, space_key: str) -> str:
        if space_key not in self._space_ids:
            rows = ((await self.http.call("GET", f"{self.v2}/spaces", params={"keys": space_key})).json()
                    or {}).get("results") or []
            match = [r for r in rows if r.get("key") == space_key]
            if not match:
                raise ConnectorError(f"Confluence has no space with the key {space_key!r} (or the account cannot see it)")
            self._space_ids[space_key] = str(match[0]["id"])
        return self._space_ids[space_key]

    async def get(self, page_id: str) -> Page:
        return self._page((await self.http.call("GET", f"{self.v2}/pages/{quote(page_id)}")).json())

    async def find(self, space_key: str, title: str) -> Page | None:
        rows = ((await self.http.call("GET", f"{self.v2}/pages", params={
            "space-id": await self.space_id(space_key), "title": title, "status": "current"})).json()
                or {}).get("results") or []
        return self._page(rows[0]) if rows else None

    async def create(self, space_key: str, parent_id: str, title: str, body: str) -> Page:
        payload: dict[str, Any] = {"spaceId": await self.space_id(space_key), "status": "current", "title": title,
                                   "body": {"representation": "storage", "value": body}}
        if parent_id:
            payload["parentId"] = parent_id
        return self._page((await self.http.call("POST", f"{self.v2}/pages", json=payload)).json())

    async def update(self, page_id: str, title: str, body: str, version: int, message: str) -> Page:
        payload = {"id": page_id, "status": "current", "title": title,
                   "body": {"representation": "storage", "value": body},
                   "version": {"number": version, "message": message}}
        return self._page((await self.http.call("PUT", f"{self.v2}/pages/{quote(page_id)}", json=payload)).json())

    async def attach(self, page_id: str, filename: str, content: bytes) -> None:
        await self.http.attach(self.v1, page_id, filename, content)


Adapter = DataCenterAdapter | CloudAdapter


class ConfluencePublisher:
    def __init__(self, connector: AtlassianConnector) -> None:
        self.connector = connector
        site = connector.config.url("confluence_url")
        if not site:
            raise ConnectorError("the Atlassian connector has no Confluence URL (connectors.atlassian.confluenceUrl)")
        self.flavor = getattr(connector.config.install, "flavor", "datacenter")
        if self.flavor == "cloud" and site.endswith("/wiki"):
            site = site[: -len("/wiki")]  # the adapter adds /wiki itself
        self.site = site
        http = _Http(connector.client(site), header_secrets(connector.auth_header()), site)
        self.adapter: Adapter = CloudAdapter(http) if self.flavor == "cloud" else DataCenterAdapter(http)

    async def test(self) -> ConnectionTest:
        self.connector.ready()
        user = await self.adapter.current_user()
        return ConnectionTest(ok=True, account=user.get("displayName") or user.get("username"),
                              site=self.site, message=f"Confluence {self.flavor}")

    async def _update(self, page_id: str, title: str, body: str, known_version: int | None,
                      message: str) -> Page:
        version = known_version if known_version is not None else (await self.adapter.get(page_id)).version
        try:
            return await self.adapter.update(page_id, title, body, version + 1, message)
        except ConfluenceHTTPError as exc:
            if exc.status != 409:
                raise
        current = (await self.adapter.get(page_id)).version  # saved by someone in between: once more
        try:
            return await self.adapter.update(page_id, title, body, current + 1, message)
        except ConfluenceHTTPError as exc:
            if exc.status != 409:
                raise
            raise ConnectorError(f"Confluence rejected the update twice: {exc.remote}", 409) from None

    async def publish(self, ctx: PageContext, *, space_key: str, parent_page_id: str,
                      existing: ConnectorLink | None) -> tuple[str, str, int]:
        """Create or update the page, then attach the design record. ``(page id, url, version)``."""
        page = await self.write_page(ctx, space_key=space_key, parent_page_id=parent_page_id, existing=existing)
        await self.attach_design(page[0], ctx)
        return page

    async def attach_design(self, page_id: str, ctx: PageContext) -> None:
        """The design record as ``design-<hash 12>.json`` (a new version when already attached)."""
        self.connector.ready()
        design_hash = str(ctx.design.get("design_hash") or "")
        await self.adapter.attach(page_id, attachment_name(design_hash), design_attachment(ctx.design))

    async def write_page(self, ctx: PageContext, *, space_key: str, parent_page_id: str,
                         existing: ConnectorLink | None) -> tuple[str, str, int]:
        """Create or update the page only. ``(page id, url, version)``."""
        self.connector.ready()
        if not space_key:
            raise ConnectorError("set the Confluence space key in Settings → Connectors")
        title = page_title(ctx.app_name, ctx.process_name)
        body = render_architecture_page(ctx)
        message = f"republished from design v{ctx.design_version}"
        page: Page | None = None
        if existing is not None and existing.remote_id:
            try:
                page = await self._update(existing.remote_id, title, body, existing.remote_version, message)
            except ConfluenceHTTPError as exc:
                if exc.status != 404:
                    raise
                page = None  # the linked page was deleted: find it by title, or create it again
        if page is None:
            found = await self.adapter.find(space_key, title)
            if found is not None:
                page = await self._update(found.id, title, body, found.version, message)
            else:
                page = await self.adapter.create(space_key, parent_page_id, title, body)
        return page.id, page.url, page.version
