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
async def memory_app(memory_settings: Settings):
    engine = make_engine(memory_settings.database_url)
    try:
        await reset_schema(engine)
    except Exception as exc:  # noqa: BLE001  no database: skip, do not fail the suite
        await engine.dispose()
        pytest.skip(f"Postgres is not available: {exc}")
    await engine.dispose()
    application = create_app(memory_settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def memory_client(memory_app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=memory_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        yield c
