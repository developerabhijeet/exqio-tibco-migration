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
def settings(tmp_path: Path) -> Settings:
    return Settings(database_url=asyncpg_url(TEST_DSN), data_dir=tmp_path / "data", job_workers=2,
                    provider="fake", upload_allowed_roots=[tmp_path, REPO_ROOT / "samples"],
                    _env_file=None)


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
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        yield c
