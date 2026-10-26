"""Shared helpers for the scripts tests: ``fake_api`` (a loopback HTTP server standing in for the
API) and ``run_demo_check`` (runs ``demo_check.main`` and returns what it printed)."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@dataclass
class FakeApi:
    url: str
    connectors: dict[str, Any] = field(default_factory=dict)
    tests: dict[str, dict[str, Any]] = field(default_factory=dict)
    requests: list[tuple[str, str]] = field(default_factory=list)


def _serve(api: FakeApi) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: Any) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _route(self) -> None:
            path = self.path.removeprefix("/api")  # --base-url serves the API under /api
            api.requests.append((self.command, path))
            if path == "/settings":
                self._send(200, {"toolchains": {}})
            elif path == "/connectors":
                self._send(200, api.connectors)
            elif self.command == "POST" and path.startswith("/connectors/") and path.endswith("/test"):
                kind = path.split("/")[2]
                self._send(200, api.tests.get(kind, {"ok": False, "message": "no test"}))
            else:
                self._send(404, {"detail": "not found"})

        do_GET = do_POST = _route

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


OFF = {k: {"enabled": False} for k in ("github", "atlassian", "figma")}


@pytest.fixture
def fake_api():
    api = FakeApi(url="", connectors=json.loads(json.dumps(OFF)))
    server = _serve(api)
    api.url = f"http://127.0.0.1:{server.server_port}"
    yield api
    server.shutdown()


@pytest.fixture
def fake_api_with_github(fake_api):
    fake_api.connectors["github"] = {"enabled": True}
    fake_api.tests["github"] = {"ok": True, "account": "exiqo-bot", "site": "github.gmf.example",
                                "scopes": [], "latency_ms": 5, "message": ""}
    return fake_api


def run_demo_check(args: list[str]) -> str:
    """Run ``demo_check.main`` with ``--only connectors`` unless the caller chose a step, and
    return the standard output."""
    import demo_check

    argv = list(args) if "--only" in args else [*args, "--only", "connectors"]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        demo_check.main(argv)
    return out.getvalue()
