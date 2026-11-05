"""Fake Figma REST API. ``X-Figma-Token`` is required. ``/v1/images`` answers with URLs on a second
server (``images``, which serves PNG bytes), as the real API answers with another host."""
from __future__ import annotations

import json
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from api.tests.fakes.server import FakeServer, FakeService, json_response, responder

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15"
       b"\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00"
       b"\x00IEND\xaeB`\x82")
FILE_JSON = Path(__file__).with_name("figma_file.json")


def images_server() -> FakeServer:
    """A server for the rendered images: every ``GET /img/...`` is a PNG."""
    def handle(method, path, headers):
        if method == "GET" and path.startswith("/img/"):
            return 200, {"content-type": "image/png"}, PNG
        return 404, {}, b""

    return FakeServer(responder(handle))


class FakeFigma(FakeService):
    def __init__(self, images: FakeServer, token: str = "figma-fake-token") -> None:
        self.token, self.images = token, images
        self.files: dict[str, dict] = {"FILE1": json.loads(FILE_JSON.read_text())}
        self._serve(Starlette(routes=[
            Route("/v1/me", self._guard(self._me)),
            Route("/v1/files/{key}", self._guard(self._file)),
            Route("/v1/files/{key}/nodes", self._guard(self._nodes)),
            Route("/v1/images/{key}", self._guard(self._images)),
        ]))

    def _guard(self, handler):
        async def run(request: Request) -> Response:
            if request.headers.get("x-figma-token") != self.token:
                return json_response({"status": 403, "err": "Invalid token"}, 403)
            return await handler(request)

        return run

    def add_file(self, key: str, name: str = "Another file") -> None:
        self.files[key] = {**json.loads(FILE_JSON.read_text()), "name": name}

    @staticmethod
    def _walk(node: dict):
        yield node
        for child in node.get("children", []):
            yield from FakeFigma._walk(child)

    async def _me(self, request: Request) -> Response:
        return json_response({"id": "1", "email": "exiqo-bot@example.org", "handle": "exiqo-bot"})

    def _missing(self) -> Response:
        return json_response({"status": 404, "err": "Not found"}, 404)

    async def _file(self, request: Request) -> Response:
        data = self.files.get(request.path_params["key"])
        return self._missing() if data is None else json_response(data)

    async def _nodes(self, request: Request) -> Response:
        data = self.files.get(request.path_params["key"])
        if data is None:
            return self._missing()
        wanted = [i for i in request.query_params.get("ids", "").split(",") if i]
        index = {n["id"]: n for n in self._walk(data["document"])}
        return json_response({"name": data["name"], "nodes": {
            i: ({"document": index[i]} if i in index else None) for i in wanted}})

    async def _images(self, request: Request) -> Response:
        key = request.path_params["key"]
        if key not in self.files:
            return self._missing()
        fmt = request.query_params.get("format", "png")
        ids = [i for i in request.query_params.get("ids", "").split(",") if i]
        return json_response({"err": None, "images": {
            i: f"{self.images.url}/img/{key}/{i.replace(':', '-')}.{fmt}" for i in ids}})
