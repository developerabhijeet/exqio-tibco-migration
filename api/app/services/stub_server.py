"""The outbound HTTP stub server of a running generated service (Verify).

The generated service's typed HTTP clients read ``Clients:<Client>:BaseAddress``; when the API
starts the service it points every client at this server (``Clients__<Client>__BaseAddress``).
Before each scenario the verify job loads the scenario's ``stub_responses`` (the responses the
parity evaluator used, keyed ``<resource id><path>``); the server answers them by path (exact,
then glob), sleeps ``delay_ms`` so timeouts happen for real, and records every call. Those calls
become the actual ``service_called`` side effects, compared with the evaluator's.
"""
from __future__ import annotations

import json
import re
import threading
import time
from fnmatch import fnmatchcase
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from engine.parity.models import SideEffect, StubResponse

MAX_DELAY_S = 30.0
_CLIENT_KEY = re.compile(r'Clients:(\w+):BaseAddress')


def client_names(build_root: Path) -> list[str]:
    """Typed client names the generated solution configures (``Clients:<Name>:BaseAddress``)."""
    names: set[str] = set()
    for f in build_root.glob("src/*.Infrastructure/*.cs"):
        try:
            names |= set(_CLIENT_KEY.findall(f.read_text(encoding="utf-8")))
        except OSError:
            continue
    return sorted(names)


class StubServer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.routes: list[tuple[str, str, StubResponse]] = []  # (path pattern, resource id, response)
        self.names: dict[str, str] = {}
        self.timeouts: dict[str, int] = {}
        self.calls: list[dict[str, Any]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # quiet
                pass

            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body: Any = json.loads(raw) if raw else None
                except ValueError:
                    body = raw.decode("utf-8", errors="replace")
                path = urlsplit(self.path).path
                route = outer.match(path)
                if route is None:
                    outer.record({"method": self.command, "path": path, "body": body, "resource_id": None,
                                  "status": 404})
                    self._send(404, {"title": f"no stub for {self.command} {path}"})
                    return
                resource_id, response = route
                timeout = outer.timeouts.get(resource_id)
                call = {"method": self.command, "path": path, "body": body, "resource_id": resource_id}
                if timeout is not None and response.delay_ms > timeout:
                    call["outcome"] = "timeout"
                else:
                    call["status"] = response.status
                outer.record(call)
                if response.delay_ms:
                    time.sleep(min(response.delay_ms / 1000.0, MAX_DELAY_S))
                try:
                    self._send(response.status, response.body)
                except (BrokenPipeError, ConnectionResetError):  # the client gave up (a timeout)
                    pass

            def _send(self, status: int, body: Any) -> None:
                data = json.dumps(body).encode("utf-8") if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _serve

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="exiqo-stub-server")
        self._thread.start()

    # ------------------------------------------------------------ scenario state
    def load(self, stub_responses: dict[str, StubResponse], names: dict[str, str],
             timeouts: dict[str, int]) -> None:
        routes = []
        for key, response in stub_responses.items():
            resource, sep, rest = key.partition("/")
            routes.append(("/" + rest if sep else "*", resource, response))
        with self._lock:
            self.routes, self.names, self.timeouts, self.calls = routes, names, timeouts, []

    def match(self, path: str) -> tuple[str, StubResponse] | None:
        with self._lock:
            routes = list(self.routes)
        for pattern, resource, response in routes:
            if pattern == path:
                return resource, response
        for pattern, resource, response in routes:
            if fnmatchcase(path, pattern):
                return resource, response
        return None

    def record(self, call: dict[str, Any]) -> None:
        with self._lock:
            self.calls.append(call)

    def side_effects(self) -> list[SideEffect]:
        """Recorded calls as ``service_called`` side effects, shaped like the evaluator's
        (``<resource name> <METHOD> <path>``, payload ``request`` plus ``status`` or ``outcome``).
        Identical consecutive calls (resilience retries of one TIBCO call) count once."""
        with self._lock:
            calls = list(self.calls)
        out: list[SideEffect] = []
        last: tuple[str, str] | None = None
        for c in calls:
            name = self.names.get(c["resource_id"] or "", c["resource_id"] or "(unstubbed)")
            target = f"{name} {c['method']} {c['path']}"
            payload: dict[str, Any] = {"request": c["body"]}
            if "outcome" in c:
                payload["outcome"] = c["outcome"]
            else:
                payload["status"] = c["status"]
            key = (target, json.dumps(payload, sort_keys=True, default=str))
            if key == last:
                continue
            last = key
            out.append(SideEffect(kind="service_called", target=target, payload=payload))
        return out

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
