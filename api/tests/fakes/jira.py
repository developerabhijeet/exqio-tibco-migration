"""Fake Jira (REST v2): issues ``<PROJECT>-<n>`` numbered from 1; ``delete(key)`` makes later reads 404."""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from api.tests.fakes.atlassian import authorized
from api.tests.fakes.server import FakeService, json_response


class FakeJira(FakeService):
    def __init__(self, flavor: str = "datacenter", token: str = "atlassian-fake-token",
                 user: str = "bot@example.org", project: str = "GMF") -> None:
        self.flavor, self.token, self.user, self.project = flavor, token, user, project
        self.issues: dict[str, dict] = {}
        self._n = 0
        self._serve(Starlette(routes=[
            Route("/rest/api/2/myself", self._guard(self._myself)),
            Route("/rest/api/2/project", self._guard(self._projects)),
            Route("/rest/api/2/project/{key}", self._guard(self._project)),
            Route("/rest/api/2/issue", self._guard(self._create), methods=["POST"]),
            Route("/rest/api/2/issue/{key}", self._guard(self._issue), methods=["GET", "PUT"]),
        ]))

    def _guard(self, handler):
        async def run(request: Request) -> Response:
            if not authorized(request, self.flavor, self.token, self.user):
                return json_response({"errorMessages": ["Unauthorized"]}, 401)
            return await handler(request)

        return run

    async def _myself(self, request: Request) -> Response:
        return json_response({"name": "exiqo-bot", "displayName": "ExiQO bot", "active": True,
                              "accountId": "5b10ac8d82e05b22cc7d4ef5"})

    async def _projects(self, request: Request) -> Response:
        return json_response([{"key": self.project, "name": f"{self.project} project"}])

    async def _project(self, request: Request) -> Response:
        if request.path_params["key"] != self.project:
            return json_response({"errorMessages": ["No project could be found"], "errors": {}}, 404)
        return json_response({"key": self.project, "name": f"{self.project} project",
                              "issueTypes": [{"name": "Task"}, {"name": "Story"}]})

    def _view(self, issue: dict) -> dict:
        return {"id": issue["id"], "key": issue["key"], "self": f"{self.url}/rest/api/2/issue/{issue['id']}",
                "fields": issue["fields"]}

    async def _create(self, request: Request) -> Response:
        fields = (await request.json()).get("fields") or {}
        if not fields.get("summary") or not (fields.get("project") or {}).get("key"):
            return json_response({"errorMessages": [], "errors": {"summary": "required"}}, 400)
        self._n += 1
        key = f"{fields['project']['key']}-{self._n}"
        issue = {"id": str(10000 + self._n), "key": key, "fields": dict(fields)}
        self.issues[key] = issue
        return json_response({"id": issue["id"], "key": key, "self": self._view(issue)["self"]}, 201)

    async def _issue(self, request: Request) -> Response:
        issue = self.issues.get(request.path_params["key"])
        if issue is None:
            return json_response({"errorMessages": ["Issue does not exist"], "errors": {}}, 404)
        if request.method == "PUT":
            issue["fields"].update((await request.json()).get("fields") or {})
            return Response(status_code=204)
        return json_response(self._view(issue))

    def delete(self, key: str) -> None:
        self.issues.pop(key, None)
