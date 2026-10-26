"""Figma: frames and their text as design references, thumbnails only from an allowlisted image host.

Reads only: ``GET /v1/me`` (test), ``/v1/files/<key>?depth=2`` (pages and their top-level frames),
``/v1/files/<key>/nodes`` (the text of chosen frames) and ``/v1/images/<key>`` (render URLs).
No Figma host is written in code: the API URL, the web URL and the image hosts are install values.

Figma answers an image request with URLs on another host (a storage service). That host is a
separate allowlist (``imageHosts``) and a URL the API returns is fetched only when its host is on
it; with none configured, or for a URL elsewhere, the thumbnail is off and the reason says why.
The image request carries no Figma token.

Frame text is design data that reaches model prompts, so name, page and every text line are
masked (``mask_sensitive``), reduced to single lines (control characters and newlines collapsed,
angle brackets removed so no ``<source>`` tag can be written), cut (name and page 120 characters,
text 4 000) and rendered one frame per line. The repo has no injection-pattern sanitiser; the
defence is that structure plus the ``<source>`` data wrapper and the designer's system rule that
references are not instructions.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from pydantic import BaseModel

from api.app.connectors.base import ConnectionTest, ConnectorError
from api.app.connectors.config import host_allowed
from api.app.connectors.confluence_page import PAGE_MASKED_FIELDS
from api.app.connectors.registry import BaseConnector, register
from engine.agents.provider import mask_sensitive

FRAME_TYPES = frozenset({"FRAME", "COMPONENT", "COMPONENT_SET"})
TEXT_MAX = 4000
IMAGE_MAX_BYTES = 5 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
FILE_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NODE_ID = re.compile(r"^[A-Za-z0-9_:;-]{1,64}$")
JSON = {"Accept": "application/json"}
NO_IMAGE_HOSTS = "no image host is allowlisted (connectors.figma.imageHosts): thumbnails are off"


class FileNotAllowed(ValueError):
    """The file is not among the install's Figma files (API: 422)."""


class Frame(BaseModel):
    node_id: str
    name: str
    page: str
    width: float = 0
    height: float = 0


def valid_file_key(key: str) -> bool:
    return bool(FILE_KEY.match(key))


def valid_node_id(node_id: str) -> bool:
    return bool(NODE_ID.match(node_id))


def frame_url(web_url: str, file_key: str, node_id: str) -> str:
    """The frame's link in the Figma web app ("" when no web URL is configured)."""
    if not web_url:
        return ""
    return f"{web_url.rstrip('/')}/design/{quote(file_key, safe='')}?node-id={quote(node_id.replace(':', '-'), safe='-')}"


LABEL_MAX = 120
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\s]+")


def _mask(value: str) -> str:
    return mask_sensitive(value, PAGE_MASKED_FIELDS)


def one_line(value: str, limit: int | None = None) -> str:
    """``value`` masked, on one line (whitespace and control characters collapsed, ``<`` and ``>``
    removed), cut to ``limit`` characters."""
    out = _CONTROL.sub(" ", _mask(str(value)).replace("<", "").replace(">", "")).strip()
    return out[:limit] if limit else out


def collect_text(node: dict[str, Any]) -> str:
    """The ``characters`` of the TEXT nodes under ``node``, depth first, one per line, masked and cut
    to 4 000 characters (masked first, so a secret is never cut into an unrecognisable half)."""
    lines: list[str] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.get("type") == "TEXT" and str(current.get("characters") or "").strip():
            lines.extend(x for x in (one_line(part) for part in str(current["characters"]).splitlines()) if x)
        stack.extend(reversed(current.get("children") or []))
    return "\n".join(lines)[:TEXT_MAX]


class FigmaConnector(BaseConnector):
    def auth_header(self) -> dict[str, str]:
        return {"X-Figma-Token": self.credential()}

    @property
    def api_url(self) -> str:
        url = self.config.url("api_url")
        if not url:
            raise ConnectorError("the Figma connector has no API URL (connectors.figma.apiUrl)")
        return url

    @property
    def web_url(self) -> str:
        return self.config.url("web_url")

    @property
    def image_hosts(self) -> list[str]:
        return list(getattr(self.config.install, "image_hosts", []) or [])

    @property
    def thumbnails(self) -> bool:
        return bool(self.image_hosts)

    @property
    def image_dir(self) -> Path:
        return self.settings.data_dir.resolve() / "figma"

    def check_file(self, file_key: str) -> None:
        """``ValueError`` for a malformed key, ``FileNotAllowed`` when the admin limited the files."""
        if not valid_file_key(file_key):
            raise ValueError(f"not a Figma file key: {file_key!r}")
        allowed = [k for k in getattr(self.config.app, "file_keys", []) if k]
        if allowed and file_key not in allowed:
            raise FileNotAllowed(f"file {file_key} is not in this install's Figma files")

    def _api(self):
        return self.client(self.api_url)

    async def test(self) -> ConnectionTest:
        self.ready()
        me = await self._api().json("GET", "/v1/me", headers=JSON) or {}
        message = "Figma" + ("" if self.thumbnails else "; " + NO_IMAGE_HOSTS)
        return ConnectionTest(ok=True, account=me.get("handle") or me.get("email"), site=self.api_url,
                              message=message)

    async def frames(self, file_key: str) -> list[Frame]:
        """The top-level frames of every page of the file, in document order."""
        self.check_file(file_key)
        data = await self._api().json("GET", f"/v1/files/{quote(file_key, safe='')}", params={"depth": 2},
                                      headers=JSON) or {}
        out: list[Frame] = []
        for page in (data.get("document") or {}).get("children") or []:
            for node in page.get("children") or []:
                if node.get("type") not in FRAME_TYPES or not node.get("id"):
                    continue
                box = node.get("absoluteBoundingBox") or node.get("size") or {}
                out.append(Frame(node_id=str(node["id"]), name=one_line(str(node.get("name") or node["id"]), LABEL_MAX) or str(node["id"]),
                                 page=one_line(str(page.get("name") or ""), LABEL_MAX),
                                 width=float(box.get("width") or 0), height=float(box.get("height") or 0)))
        return out

    async def frame_text(self, file_key: str, node_ids: list[str]) -> dict[str, str]:
        """node id -> its text. A node the file does not have is left out of the answer."""
        self.check_file(file_key)
        self._valid_nodes(node_ids)
        data = await self._api().json("GET", f"/v1/files/{quote(file_key, safe='')}/nodes",
                                      params={"ids": ",".join(node_ids)}, headers=JSON) or {}
        out: dict[str, str] = {}
        for node_id, entry in (data.get("nodes") or {}).items():
            document = (entry or {}).get("document")
            if document is not None:
                out[node_id] = collect_text(document)
        return out

    @staticmethod
    def _valid_nodes(node_ids: list[str]) -> None:
        for node_id in node_ids:
            if not valid_node_id(node_id):
                raise ValueError(f"not a Figma node id: {node_id!r}")

    def image_path(self, file_key: str, node_id: str) -> Path:
        digest = hashlib.sha256(f"{file_key}\n{node_id}".encode()).hexdigest()[:24]
        return self.image_dir / f"{digest}.png"

    async def render(self, file_key: str, node_ids: list[str]) -> dict[str, Path | None]:
        return (await self.render_with_notes(file_key, node_ids))[0]

    async def render_with_notes(self, file_key: str, node_ids: list[str]) -> tuple[dict[str, Path | None],
                                                                                    dict[str, str]]:
        """node id -> the PNG saved for it (or ``None``) and, for each ``None``, why. Nothing is
        requested at all while no image host is configured."""
        self.check_file(file_key)
        self._valid_nodes(node_ids)
        if not self.thumbnails:
            return {n: None for n in node_ids}, {n: NO_IMAGE_HOSTS for n in node_ids}
        data = await self._api().json("GET", f"/v1/images/{quote(file_key, safe='')}",
                                      params={"ids": ",".join(node_ids), "format": "png", "scale": 1},
                                      headers=JSON) or {}
        urls = data.get("images") or {}
        images = self.client(self.api_url, auth=dict, allowed_hosts=self.image_hosts)  # no Figma token
        images.max_bytes = IMAGE_MAX_BYTES
        paths: dict[str, Path | None] = {}
        notes: dict[str, str] = {}
        for node_id in node_ids:
            url = urls.get(node_id)
            paths[node_id] = None
            if not url:
                notes[node_id] = "Figma rendered no image for this frame"
                continue
            parts = urlsplit(str(url))
            if not host_allowed(parts.netloc, self.image_hosts, parts.scheme):
                notes[node_id] = f"the image host {parts.netloc or '?'} is not in imageHosts: not fetched"
                continue
            try:
                response = await images.request("GET", str(url))
            except ConnectorError as exc:
                notes[node_id] = ("the image is over 5 MB: dropped" if "is over" in exc.message
                                  else f"the image could not be fetched: {exc.message}")
                continue
            kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if response.status_code != 200:
                notes[node_id] = f"the image host answered HTTP {response.status_code}"
            elif kind != "image/png":
                notes[node_id] = f"the image is {kind or 'of no type'}, not image/png: dropped"
            elif len(response.content) > IMAGE_MAX_BYTES:
                notes[node_id] = "the image is over 5 MB: dropped"
            elif not response.content.startswith(PNG_MAGIC):
                notes[node_id] = "the image is not a PNG: dropped"
            else:
                path = self.image_path(file_key, node_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(response.content)
                paths[node_id] = path
        return paths, notes


register("figma", FigmaConnector)
