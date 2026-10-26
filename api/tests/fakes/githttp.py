"""Git smart HTTP for the fake GitHub: ``git http-backend`` (the CGI that ships with git) served
as an ASGI app over a project root of bare repositories (``<root>/<owner>/<repo>.git``)."""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response


def _run_cgi(project_root: Path, request: Request, body: bytes) -> Response:
    env = {
        "PATH": os.environ.get("PATH", ""), "HOME": str(project_root),
        "GIT_PROJECT_ROOT": str(project_root), "GIT_HTTP_EXPORT_ALL": "1",
        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.receivepack", "GIT_CONFIG_VALUE_0": "true",
        "GIT_CONFIG_NOSYSTEM": "1",
        "REQUEST_METHOD": request.method, "PATH_INFO": request.url.path,
        "QUERY_STRING": request.url.query, "REMOTE_USER": "exiqo-bot", "REMOTE_ADDR": "127.0.0.1",
        "CONTENT_TYPE": request.headers.get("content-type", ""), "CONTENT_LENGTH": str(len(body)),
    }
    if "content-encoding" in request.headers:
        env["HTTP_CONTENT_ENCODING"] = request.headers["content-encoding"]
    if "git-protocol" in request.headers:
        env["GIT_PROTOCOL"] = request.headers["git-protocol"]
    proc = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True, check=False)
    head, _, payload = proc.stdout.partition(b"\r\n\r\n")
    if not _:
        head, _, payload = proc.stdout.partition(b"\n\n")
    headers: dict[str, str] = {}
    status = 200
    for line in head.decode("latin-1").splitlines():
        name, _, value = line.partition(":")
        if name.lower() == "status":
            status = int(value.strip().split()[0])
        elif name:
            headers[name.strip()] = value.strip()
    return Response(payload, status_code=status, headers=headers)


def wsgi_http_backend(project_root: Path):
    """An ASGI handler ``(request) -> Response`` running ``git http-backend`` for the request
    (``receive-pack`` enabled, every repository exported)."""

    async def handle(request: Request) -> Response:
        body = await request.body()
        return await asyncio.to_thread(_run_cgi, project_root, request, body)

    return handle
