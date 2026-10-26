"""Shared fixtures for the API tests: a real Postgres (schema ``api`` reset per test) and an app.

``TEST_DATABASE_URL`` (default the local ``exiqo_test``) is converted to the asyncpg URL. The
app's lifespan runs inside the test's event loop so the job workers run there too.
"""
from __future__ import annotations

import json
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
from api.tests.fakes.atlassian import AtlassianFakes
from api.tests.fakes.confluence import FakeConfluence
from api.tests.fakes.figma import FakeFigma, images_server
from api.tests.fakes.github import FakeGitHub
from api.tests.fakes.jira import FakeJira

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


LEGACY_SCHEMAS = ("knowledge", "knowledge_retired")


async def drop_legacy_knowledge(engine) -> None:
    """Drop the Postgres knowledge schema of earlier releases (and its retired copy)."""
    from sqlalchemy import text

    async with engine.begin() as conn:
        for schema in LEGACY_SCHEMAS:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


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
        await drop_legacy_knowledge(engine)  # else the lifespan would queue memory.migrate
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


# ------------------------------------------------------------------ connector fakes


@pytest.fixture
def fake_github():
    with FakeGitHub() as srv:
        yield srv


@pytest.fixture
def token(fake_github: FakeGitHub) -> str:
    """The bearer token the fake GitHub accepts."""
    return fake_github.token


@pytest.fixture
def fake_confluence_dc():
    with FakeConfluence("datacenter") as srv:
        yield srv


@pytest.fixture
def fake_confluence_cloud():
    with FakeConfluence("cloud") as srv:
        yield srv


@pytest.fixture
def fake_confluence(fake_confluence_dc: FakeConfluence) -> FakeConfluence:
    return fake_confluence_dc


@pytest.fixture
def fake_jira():
    with FakeJira("datacenter") as srv:
        yield srv


@pytest.fixture
def fake_jira_cloud():
    with FakeJira("cloud") as srv:
        yield srv


@pytest.fixture
def fake_figma_images():
    with images_server() as srv:
        yield srv


@pytest.fixture
def fake_figma(fake_figma_images):
    with FakeFigma(fake_figma_images) as srv:
        yield srv


@pytest.fixture
def connectors_settings(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``connectors_settings(github=, confluence=, jira=, figma=)``: ``settings`` with ``CONNECTORS``
    pointing at the fakes given (install layer on, loopback flag on); each token is written to a
    file in ``tmp_path`` and named by ``<FIELD>_FILE`` (read on every call). The admin layer
    (``connectors`` in the settings table) is still off: a test turns it on through the API."""

    def secret_file(field: str, value: str) -> None:
        path = tmp_path / f"{field}.secret"
        path.write_text(value, encoding="utf-8")
        monkeypatch.setenv(f"{field.upper()}_FILE", str(path))

    def build(*, github: FakeGitHub | None = None, confluence: FakeConfluence | None = None,
              jira: FakeJira | None = None, figma: FakeFigma | None = None) -> Settings:
        install: dict[str, dict] = {}
        if github is not None:
            install["github"] = {"enabled": True, "baseUrl": github.url, "apiUrl": f"{github.url}/api/v3",
                                 "allowedHosts": [github.host], "commitEmail": "exiqo-bot@example.org"}
            secret_file("github_token", github.token)
        atlassian = [s for s in (confluence, jira) if s is not None]
        if atlassian:
            first = atlassian[0]
            install["atlassian"] = {
                "enabled": True, "flavor": first.flavor,
                "confluenceUrl": confluence.url if confluence else "",
                "jiraUrl": jira.url if jira else "", "allowedHosts": [s.host for s in atlassian]}
            secret_file("atlassian_token", first.token)
            if first.flavor == "cloud":
                secret_file("atlassian_user", first.user)
        if figma is not None:
            install["figma"] = {"enabled": True, "apiUrl": figma.url, "webUrl": "https://www.figma.example",
                                "allowedHosts": [figma.host], "imageHosts": [figma.images.host]}
            secret_file("figma_token", figma.token)
        return settings.model_copy(update={"connectors": json.dumps(install),
                                           "connectors_allow_insecure_loopback": True})

    return build


@pytest.fixture
async def client_github_disabled(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """A client of an app with every connector off (the install default)."""
    github_disabled = settings.model_copy(update={"connectors": ""})
    engine = make_engine(github_disabled.database_url)
    try:
        await reset_schema(engine)
    except Exception as exc:  # noqa: BLE001  no database: skip, do not fail the suite
        await engine.dispose()
        pytest.skip(f"Postgres is not available: {exc}")
    await engine.dispose()
    application = create_app(github_disabled)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
            c.app = application  # type: ignore[attr-defined]
            yield c


@pytest.fixture
def github_disabled(client_github_disabled: httpx.AsyncClient) -> httpx.AsyncClient:
    """The name sub-project 6 uses for ``client_github_disabled``."""
    return client_github_disabled


ATLASSIAN_SPACE = "GMF"
ATLASSIAN_PARENT_PAGE = "1001"  # the first page each Confluence fake makes: the seeded parent


@pytest.fixture
async def atlassian_app(settings: Settings):
    """An app whose admin layer has Atlassian on (space ``GMF``, parent page ``1001``); the install
    layer is set by ``client_atlassian(flavor)``, so processes can be seeded before a flavor is
    chosen."""
    from api.app.connectors.config import STORED_KEY
    from api.app.models import Setting

    own = settings.model_copy(update={"connectors": "", "connectors_allow_insecure_loopback": True})
    engine = make_engine(own.database_url)
    try:
        await reset_schema(engine)
    except Exception as exc:  # noqa: BLE001  no database: skip, do not fail the suite
        await engine.dispose()
        pytest.skip(f"Postgres is not available: {exc}")
    await engine.dispose()
    application = create_app(own)
    async with application.router.lifespan_context(application):
        async with application.state.sessionmaker() as s:
            s.add(Setting(key=STORED_KEY, updated_by="ops.sam", value={"atlassian": {
                "enabled": True, "space_key": ATLASSIAN_SPACE, "parent_page_id": ATLASSIAN_PARENT_PAGE}}))
            await s.commit()
        yield application


@pytest.fixture
async def atlassian_client(atlassian_app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=atlassian_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        c.app = atlassian_app  # type: ignore[attr-defined]
        yield c


@pytest.fixture
def client_atlassian(atlassian_app, atlassian_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``client_atlassian(flavor) -> (client, fake)``: points the install layer at that flavor's
    Confluence and Jira fakes (started once per flavor, each Confluence seeded with the parent
    page) and mounts the Atlassian secret files. ``fake`` is an ``AtlassianFakes``."""
    started: dict[str, AtlassianFakes] = {}
    settings = atlassian_app.state.settings

    def make(flavor: str = "datacenter") -> tuple[httpx.AsyncClient, AtlassianFakes]:
        if flavor not in started:
            conf, jira = FakeConfluence(flavor).start(), FakeJira(flavor).start()
            assert conf.create_page(ATLASSIAN_SPACE, "Architecture", "<p>parent</p>") == ATLASSIAN_PARENT_PAGE
            started[flavor] = AtlassianFakes(conf, jira)
        fakes = started[flavor]
        conf, jira = fakes.confluence, fakes.jira
        settings.connectors = json.dumps({"atlassian": {
            "enabled": True, "flavor": flavor, "confluenceUrl": conf.url, "jiraUrl": jira.url,
            "allowedHosts": [conf.host, jira.host]}})
        for field, value in (("atlassian_token", conf.token), ("atlassian_user", conf.user)):
            path = tmp_path / f"{field}.secret"
            path.write_text(value, encoding="utf-8")
            monkeypatch.setenv(f"{field.upper()}_FILE", str(path))
        return atlassian_client, fakes

    yield make
    for fakes in started.values():
        fakes.confluence.stop()
        fakes.jira.stop()
