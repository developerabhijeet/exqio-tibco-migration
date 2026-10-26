"""``FakeServer``: an ASGI app served by uvicorn in a thread on ``127.0.0.1:<free port>``, recording
every request it receives (``requests``) so tests can assert what reached it, and what did not.

The server's tick loop keeps a reference to the real ``asyncio.sleep``, so a test that patches
``asyncio.sleep`` (to assert retry delays) does not record the server's own ticks.
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Self

import uvicorn

_real_sleep = asyncio.sleep

ASGIApp = Any


@dataclass
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes = b""
    query: str = ""

    @property
    def json(self) -> Any:
        return json.loads(self.body) if self.body else None


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # a thread cannot install them
        return

    async def main_loop(self) -> None:
        counter = 0
        should_exit = await self.on_tick(counter)
        while not should_exit:
            counter = (counter + 1) % 864000
            await _real_sleep(0.05)
            should_exit = await self.on_tick(counter)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class FakeServer:
    """``with FakeServer(app) as srv:`` serves ``app``; ``srv.url``, ``srv.host``, ``srv.requests``."""

    app: ASGIApp
    port: int = 0
    requests: list[RecordedRequest] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.port = self.port or free_port()
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.port}"

    @property
    def url(self) -> str:
        return f"http://{self.host}"

    async def _recording(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        more = True
        while more:
            message = await receive()
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)
        self.requests.append(RecordedRequest(
            method=scope["method"], path=scope["path"],
            headers={k.decode().lower(): v.decode() for k, v in scope["headers"]},
            body=body, query=scope.get("query_string", b"").decode()))
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    def start(self) -> Self:
        config = uvicorn.Config(self._recording, host="127.0.0.1", port=self.port, log_level="warning",
                                lifespan="off", access_log=False, interface="asgi3")
        self._server = _Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError(f"fake server on port {self.port} did not start")
            time.sleep(0.01)
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def responder(handler):
    """A minimal ASGI app from ``handler(method, path, headers) -> (status, headers, body)``."""

    async def app(scope, receive, send) -> None:
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        status, out_headers, body = handler(scope["method"], scope["path"], headers)
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            out_headers = {"content-type": "application/json", **out_headers}
        elif isinstance(body, str):
            body = body.encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(k.encode(), str(v).encode()) for k, v in out_headers.items()]})
        await send({"type": "http.response.body", "body": body})

    return app
