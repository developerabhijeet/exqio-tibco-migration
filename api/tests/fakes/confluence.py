"""Fake Confluence: the v1 content API (``/rest/api/content``, also under ``/wiki`` for Cloud) and
the Cloud v2 pages API. An update must carry ``version.number == current + 1``, else 409
(``conflict_always`` makes every update 409, as a page someone keeps editing). A page's parent must
exist. Attachments need ``X-Atlassian-Token: no-check``; a new version of one is posted to
``.../child/attachment/{id}/data``."""
from __future__ import annotations

from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from api.tests.fakes.atlassian import authorized
from api.tests.fakes.server import FakeService, json_response


@dataclass
class PageView:
    id: str
    title: str
    version: int
    body: str
    parent: str
    attachments: list[dict] = field(default_factory=list)


class FakeConfluence(FakeService):
    def __init__(self, flavor: str = "datacenter", token: str = "atlassian-fake-token",
                 user: str = "bot@example.org") -> None:
        self.flavor, self.token, self.user = flavor, token, user
        self.pages: dict[str, dict] = {}
        self.attachments: dict[str, list[dict]] = {}
        self._next = 1000
        self.seeded: set[str] = set()  # pages a test created directly (parents), not by the API
        self.conflict_always = False
        routes = []
        # Data Center serves /rest/api; Cloud serves /wiki/rest/api and the v2 API under /wiki/api/v2.
        for prefix in ("/wiki" if flavor == "cloud" else "",):
            routes += [
                Route(f"{prefix}/rest/api/user/current", self._guard(self._current)),
                Route(f"{prefix}/rest/api/content", self._guard(self._v1_collection), methods=["GET", "POST"]),
                Route(f"{prefix}/rest/api/content/{{pid}}", self._guard(self._v1_page), methods=["GET", "PUT"]),
                Route(f"{prefix}/rest/api/content/{{pid}}/child/attachment",
                      self._guard(self._attachments), methods=["GET", "POST"]),
                Route(f"{prefix}/rest/api/content/{{pid}}/child/attachment/{{aid}}/data",
                      self._guard(self._attachment_data), methods=["POST"]),
            ]
        if flavor == "cloud":
            routes += [
                Route("/wiki/api/v2/pages", self._guard(self._v2_collection), methods=["GET", "POST"]),
                Route("/wiki/api/v2/pages/{pid}", self._guard(self._v2_page), methods=["GET", "PUT"]),
                Route("/wiki/api/v2/spaces", self._guard(self._v2_spaces)),
            ]
        self._serve(Starlette(routes=routes))

    def _guard(self, handler):
        async def run(request: Request) -> Response:
            if not authorized(request, self.flavor, self.token, self.user):
                return json_response({"message": "Unauthorized"}, 401)
            return await handler(request)

        return run

    def _view(self, page: dict, *, v2: bool = False, body: bool = False) -> dict:
        webui = f"/spaces/{page['space']}/pages/{page['id']}"
        base = {"id": page["id"], "type": "page", "status": "current", "title": page["title"],
                "version": {"number": page["version"]},
                "_links": {"webui": webui, "base": self.url + ("/wiki" if v2 else "")}}
        if v2:
            base["spaceId"] = page["space"]
            base["parentId"] = page["parent"]
        else:
            base["space"] = {"key": page["space"]}
            base["ancestors"] = [{"id": page["parent"]}] if page["parent"] else []
        if body:
            base["body"] = {"storage": {"value": page["body"], "representation": "storage"}}
        return base

    @staticmethod
    def _expands_body(request: Request) -> bool:
        return "body.storage" in request.query_params.get("expand", "").split(",")

    @staticmethod
    def _wants_body(request: Request) -> bool:
        return request.query_params.get("body-format") == "storage"

    def _new(self, space: str, title: str, body: str, parent: str = "") -> dict:
        self._next += 1
        page = {"id": str(self._next), "space": space, "title": title, "body": body, "parent": parent,
                "version": 1}
        self.pages[page["id"]] = page
        return page

    async def _current(self, request: Request) -> Response:
        return json_response({"type": "known", "username": "exiqo-bot", "displayName": "ExiQO bot",
                              "accountId": "5b10ac8d82e05b22cc7d4ef5"})

    # ---- v1
    async def _v1_collection(self, request: Request) -> Response:
        if request.method == "GET":
            title, space = request.query_params.get("title"), request.query_params.get("spaceKey")
            rows = [self._view(p, body=self._expands_body(request)) for p in self.pages.values()
                    if (not title or p["title"] == title) and (not space or p["space"] == space)]
            return json_response({"results": rows, "size": len(rows)})
        body = await request.json()
        space = (body.get("space") or {}).get("key", "")
        if not space or not body.get("title"):
            return json_response({"message": "space and title are required"}, 400)
        ancestors = body.get("ancestors") or []
        if ancestors and str(ancestors[-1].get("id")) not in self.pages:
            return json_response({"statusCode": 400, "message": "parent page not found"}, 400)
        value = ((body.get("body") or {}).get("storage") or {}).get("value", "")
        return json_response(self._view(self._new(space, body["title"], value,
                                                  str(ancestors[-1]["id"]) if ancestors else ""), body=True))

    async def _v1_page(self, request: Request) -> Response:
        page = self.pages.get(request.path_params["pid"])
        if page is None:
            return json_response({"message": "No content found"}, 404)
        if request.method == "GET":
            return json_response(self._view(page, body=self._expands_body(request)))
        body = await request.json()
        return self._update(page, body, ((body.get("body") or {}).get("storage") or {}).get("value"))

    def _update(self, page: dict, body: dict, value: str | None, *, v2: bool = False) -> Response:
        number = (body.get("version") or {}).get("number")
        if self.conflict_always or number != page["version"] + 1:
            return json_response({"statusCode": 409, "message":
                                  f"Version must be {page['version'] + 1}, got {number}"}, 409)
        page["version"] = number
        page["title"] = body.get("title", page["title"])
        if value is not None:
            page["body"] = value
        return json_response(self._view(page, v2=v2, body=True))

    async def _attachments(self, request: Request) -> Response:
        pid = request.path_params["pid"]
        if pid not in self.pages:
            return json_response({"message": "No content found"}, 404)
        rows = self.attachments.setdefault(pid, [])
        if request.method == "POST":
            if request.headers.get("x-atlassian-token") != "no-check":
                return json_response({"statusCode": 403, "message": "XSRF check failed"}, 403)
            added = []
            for name, data in await self._files(request):
                if any(a["title"] == name for a in rows):
                    return json_response({"statusCode": 400, "message":
                                          f"Cannot add a new attachment with same file name as an existing "
                                          f"attachment: {name}"}, 400)
                row = {"id": f"att{pid}-{len(rows) + 1}", "type": "attachment", "title": name,
                       "size": len(data), "version": {"number": 1}, "data": data}
                rows.append(row)
                added.append(row)
            return json_response({"results": [self._att(a) for a in added], "size": len(added)})
        name = request.query_params.get("filename")
        shown = [self._att(a) for a in rows if not name or a["title"] == name]
        return json_response({"results": shown, "size": len(shown)})

    async def _attachment_data(self, request: Request) -> Response:
        rows = self.attachments.get(request.path_params["pid"], [])
        row = next((a for a in rows if a["id"] == request.path_params["aid"]), None)
        if row is None:
            return json_response({"message": "No attachment found"}, 404)
        if request.headers.get("x-atlassian-token") != "no-check":
            return json_response({"statusCode": 403, "message": "XSRF check failed"}, 403)
        files = await self._files(request)
        if not files:
            return json_response({"message": "no file"}, 400)
        row["data"], row["size"] = files[0][1], len(files[0][1])
        row["version"] = {"number": row["version"]["number"] + 1}
        return json_response(self._att(row))

    @staticmethod
    def _att(row: dict) -> dict:
        return {k: v for k, v in row.items() if k != "data"}

    @staticmethod
    async def _files(request: Request) -> list[tuple[str, bytes]]:
        ctype = request.headers.get("content-type", "")
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + await request.body())
        if not message.is_multipart():
            return []
        return [(part.get_filename(), part.get_payload(decode=True) or b"")
                for part in message.iter_parts() if part.get_filename()]

    # ---- v2
    async def _v2_collection(self, request: Request) -> Response:
        if request.method == "GET":
            title = request.query_params.get("title")
            rows = [self._view(p, v2=True, body=self._wants_body(request)) for p in self.pages.values()
                    if not title or p["title"] == title]
            return json_response({"results": rows})
        body = await request.json()
        if not body.get("spaceId") or not body.get("title"):
            return json_response({"message": "spaceId and title are required"}, 400)
        if body.get("parentId") and str(body["parentId"]) not in self.pages:
            return json_response({"errors": [{"status": 400, "title": "parent page not found"}]}, 400)
        return json_response(self._view(self._new(str(body["spaceId"]), body["title"],
                                                  (body.get("body") or {}).get("value", ""),
                                                  str(body.get("parentId") or "")), v2=True, body=True))

    async def _v2_page(self, request: Request) -> Response:
        page = self.pages.get(request.path_params["pid"])
        if page is None:
            return json_response({"message": "Not Found"}, 404)
        if request.method == "GET":
            return json_response(self._view(page, v2=True, body=self._wants_body(request)))
        body = await request.json()
        return self._update(page, body, (body.get("body") or {}).get("value"), v2=True)

    async def _v2_spaces(self, request: Request) -> Response:
        keys = [k for k in request.query_params.get("keys", "").split(",") if k]
        return json_response({"results": [{"id": k, "key": k, "name": k} for k in keys]})

    # ---- test helpers
    def create_page(self, space: str, title: str, body: str, parent: str = "") -> str:
        pid = self._new(space, title, body, parent)["id"]
        self.seeded.add(pid)
        return pid

    def published(self) -> list[PageView]:
        """The pages created through the API (not the ones a test seeded)."""
        return [PageView(p["id"], p["title"], p["version"], p["body"], p["parent"],
                         list(self.attachments.get(p["id"], []))) for p in self.pages.values()
                if p["id"] not in self.seeded]

    def page_count(self) -> int:
        return len(self.published())

    def only_page(self) -> PageView:
        pages = self.published()
        assert len(pages) == 1, f"expected one published page, found {len(pages)}"
        return pages[0]

    def bump_version(self, page_id: str, by: int = 1) -> None:
        self.pages[page_id]["version"] += by
