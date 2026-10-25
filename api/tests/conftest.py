"""Shared fixtures for the API tests: a real Postgres (schema ``api`` reset per test) and an app.

``TEST_DATABASE_URL`` (default the local ``exiqo_test``) is converted to the asyncpg URL. The
app's lifespan runs inside the test's event loop so the job workers run there too.
"""
from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.app.config import Settings
from api.app.db import asyncpg_url, make_engine, reset_schema
from api.app.main import create_app

TEST_DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://exiqo:exiqo@localhost:5432/exiqo_test")


@pytest.fixture
def settings(tmp_path: Path, request: pytest.FixtureRequest) -> Settings:
    """Role ``api`` (no in-process worker) when the test asks for ``app_no_worker``."""
    return Settings(database_url=asyncpg_url(TEST_DSN), data_dir=tmp_path / "data", job_workers=2,
                    provider="fake", upload_allowed_roots=[tmp_path, REPO_ROOT / "samples"],
                    exiqo_role="api" if "app_no_worker" in request.fixturenames else "all",
                    _env_file=None)


@pytest.fixture
def memory_settings(settings: Settings) -> Settings:
    """``settings`` pointing at the test Neo4j; skipped when NEO4J_TEST_URI is unset or empty."""
    uri = os.environ.get("NEO4J_TEST_URI", "")
    if not uri:
        pytest.skip("NEO4J_TEST_URI is not set: run make test (starts neo4j-test) or point it at "
                    "a Neo4j 5.26")
    return settings.model_copy(update={
        "neo4j_uri": uri, "neo4j_user": os.environ.get("NEO4J_TEST_USER", "neo4j"),
        "neo4j_password": os.environ.get("NEO4J_TEST_PASSWORD", "exiqo-test-neo4j")})


@pytest.fixture
async def app(settings: Settings):
    engine = make_engine(settings.database_url)
    try:
        await reset_schema(engine)
    except Exception as exc:  # noqa: BLE001  no database: skip, do not fail the suite
        await engine.dispose()
        pytest.skip(f"Postgres is not available: {exc}")
    await engine.dispose()
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
def app_no_worker(app):
    """The ``app`` fixture with ``EXIQO_ROLE=api``: jobs queue, and only the workers a test starts
    run them."""
    assert app.state.runner.worker is None
    return app


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        yield c


@pytest.fixture
async def project_id(client: httpx.AsyncClient) -> str:
    r = await client.post("/projects", json={"name": "fixture"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.fixture
async def memory_app(memory_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    """The app with the test Neo4j as its memory: the test Neo4j is reset first, and embeddings use
    the hash backend (no model download; one backend across the suite's memory tests)."""
    from engine.knowledge import embeddings

    monkeypatch.setenv("EMBEDDINGS_BACKEND", "hash")
    embeddings.reset_backend()
    engine = make_engine(memory_settings.database_url)
    try:
        await reset_schema(engine)
    except Exception as exc:  # noqa: BLE001  no database: skip, do not fail the suite
        await engine.dispose()
        pytest.skip(f"Postgres is not available: {exc}")
    await engine.dispose()
    application = create_app(memory_settings)
    async with application.router.lifespan_context(application):
        await application.state.memory.reset()
        yield application
    embeddings.reset_backend()


@pytest.fixture
async def memory_client(memory_app) -> AsyncIterator[httpx.AsyncClient]:
    """A client of ``memory_app``; ``memory_client.app`` is that app (its state, for assertions)."""
    transport = httpx.ASGITransport(app=memory_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        c.app = memory_app  # type: ignore[attr-defined]
        yield c


@pytest.fixture
def sample_upload() -> Path:
    """The synthetic GMF estate, uploaded by server path (``upload_allowed_roots`` covers it)."""
    return REPO_ROOT / "samples" / "synthetic-gmf"


class Neo4jDown:
    """``memory_app``'s memory graph swapped for one whose driver points at a closed port (the
    managed-transaction retries off, so each attempt fails at once); ``restore()`` puts it back."""

    def __init__(self, app, down) -> None:
        self.app, self.down, self.real = app, down, app.state.memory
        self._set(down)

    def _set(self, memory) -> None:
        self.app.state.memory = memory
        self.app.state.worker_services.memory = memory  # the worker copies it into each job
        self.app.state.knowledge.memory = memory  # agents, Ask and readiness read through it

    def restore(self) -> None:
        self._set(self.real)


@pytest.fixture
async def neo4j_down(memory_app):
    import socket

    from neo4j import AsyncGraphDatabase

    from engine.memory.graph import MemoryGraph

    with socket.socket() as sock:  # a port nothing listens on
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    uri = f"bolt://127.0.0.1:{port}"
    real = memory_app.state.memory
    driver = AsyncGraphDatabase.driver(uri, auth=("neo4j", "unused"), connection_timeout=1,
                                       max_transaction_retry_time=0)
    handle = Neo4jDown(memory_app, MemoryGraph(driver, uri, real.database, real.exact_scan_max))
    try:
        yield handle
    finally:
        handle.restore()
        await driver.close()
