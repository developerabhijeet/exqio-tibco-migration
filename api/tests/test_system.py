"""Health, readiness, status and brand endpoints, the same-origin mount and the vendor-free web.

In the cluster the web and the API share one origin and the ingress sends ``/api`` to the API
without rewriting, so the API serves everything under ``API_BASE_PATH``. Probes need an answer
that does not depend on the database (liveness) and one that does (readiness).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi import Request

from api.app.config import Settings
from api.app.main import create_app
from api.app.routers.run_proxy import proxy_root

REPO = Path(__file__).resolve().parents[2]


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30)


async def test_healthz_without_database(tmp_path):
    bad = Settings(database_url="postgresql+asyncpg://nobody:x@127.0.0.1:1/none",
                   data_dir=tmp_path / "data", provider="fake", _env_file=None)
    app = create_app(bad)
    async with app.router.lifespan_context(app), _client(app) as c:
        r = await c.get("/healthz")
        assert r.status_code == 200 and r.json() == {"status": "ok"}
        assert (await c.get("/readyz")).status_code == 503


async def test_readyz_ok_and_neo4j_disabled(client):
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"postgres": "ok", "neo4j": "disabled", "secrets": "ok"}


async def test_readyz_reports_neo4j_down(app, client):
    app.state.settings.neo4j_uri = "bolt://127.0.0.1:1"
    r = await client.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["neo4j"] != "ok" and body["neo4j"] != "disabled"
    assert body["postgres"] == "ok" and body["secrets"] == "ok"


async def test_readyz_reports_unreadable_secret_file(app, client, monkeypatch, tmp_path):
    monkeypatch.setenv("APP_SECRET_FILE", str(tmp_path / "missing"))
    r = await client.get("/readyz")
    assert r.status_code == 503
    assert "app_secret" in r.json()["secrets"].lower()
    secret = tmp_path / "present"
    secret.write_text("x")
    monkeypatch.setenv("APP_SECRET_FILE", str(secret))
    assert (await client.get("/readyz")).status_code == 200


async def test_system_status_is_admin_only_in_dev(client):
    # Ruling B: ops.sam holds admin; every other persona is refused, nobody at all is 401
    assert (await client.get("/system/status")).status_code == 401
    for user in ("sme.vicky", "architect.lee", "engineer.ravi", "qa.priya"):
        assert (await client.get("/system/status", headers={"X-User": user})).status_code == 403
    assert (await client.get("/system/status", headers={"X-User": "ops.sam"})).status_code == 200


async def test_system_status_and_brand(app, client):
    app.state.settings.support_contact = "platform-team@example.com"
    s = (await client.get("/system/status", headers={"X-User": "ops.sam"})).json()
    assert s["role"] == "all" and s["auth_mode"] == "dev" and s["brand_name"] == "ExiQO"
    assert s["version"] and s["support_contact"] == "platform-team@example.com"
    assert set(s) >= {"deploy_credential_mode", "image_digests", "egress_allowlist", "checks"}
    assert s["checks"]["postgres"] == "ok"
    assert (await client.get("/system/brand")).json() == {
        "brand_name": "ExiQO", "support_contact": "platform-team@example.com",
        "deploy_credential_mode": "local"}


@pytest.fixture
async def mounted(settings):
    s = settings.model_copy(update={"api_base_path": "/api"})
    app = create_app(s)
    async with app.router.lifespan_context(app), _client(app) as c:
        c.mounted_app = app  # type: ignore[attr-defined]
        yield c


async def test_base_path_mount(mounted):
    assert (await mounted.get("/api/projects")).status_code == 200
    assert (await mounted.get("/projects")).status_code == 404
    assert (await mounted.get("/healthz")).json() == {"status": "ok"}
    assert (await mounted.get("/api/healthz")).json() == {"status": "ok"}
    assert (await mounted.get("/api/readyz")).status_code == 200


async def test_run_proxy_urls_carry_the_mount(mounted):
    """request.url_for in the run proxy must include the mount path, or the browser's links
    to the running service would skip the ingress route."""
    seen: dict[str, str] = {}

    inner = next(r.app for r in mounted.mounted_app.routes if getattr(r, "path", "") == "/api")

    @inner.get("/_probe")
    async def probe(request: Request) -> dict[str, str]:
        seen["url"] = proxy_root(request, "b1")
        return {}

    assert (await mounted.get("/api/_probe")).status_code == 200
    assert seen["url"] == "http://test/api/builds/b1/run/proxy/"


def test_no_vendor_strings_in_web():
    pattern = r"R Systems|rsystems|rsi-logo|\bRSI\b"
    out = subprocess.run(
        ["grep", "-rniE", pattern, "web/app", "web/components", "web/lib", "web/public"],
        cwd=REPO, capture_output=True, text=True, check=False)
    assert out.stdout == "", out.stdout[:500]
    assert not list((REPO / "web/public").glob("rsi-logo*"))


def test_web_base_url_honours_same_origin():
    cfg = (REPO / "web/scripts/root-env.ts").read_text()
    assert re.search(r"NEXT_PUBLIC_SAME_ORIGIN", cfg) and '"/api"' in cfg
    api_ts = (REPO / "web/lib/api.ts").read_text()
    assert "NEXT_PUBLIC_API_URL" in api_ts and "requireApiUrl" in api_ts


def _route_paths(app) -> set[str]:
    """Every route path, including those of an app mounted under ``api_base_path``."""
    paths: set[str] = set()
    for route in app.routes:
        sub = getattr(route, "app", None)
        if hasattr(sub, "routes") and hasattr(route, "path"):
            paths |= {route.path + p for p in _route_paths(sub)}
        elif hasattr(route, "path"):
            paths.add(route.path)
    return paths


DOC_PATHS = {"/api/docs", "/api/redoc", "/api/openapi.json"}


def test_oidc_mode_serves_no_api_docs(tmp_path):
    # The installed product signs callers in; the schema and the docs UI are not served to anyone.
    s = Settings(auth_mode="oidc", session_secret="s" * 32, oidc_tenant_id="t", oidc_client_id="c",
                 api_base_path="/api", data_dir=tmp_path, _env_file=None)
    assert not DOC_PATHS & _route_paths(create_app(s))


def test_dev_mode_keeps_api_docs(tmp_path):
    s = Settings(auth_mode="dev", api_base_path="/api", data_dir=tmp_path, _env_file=None)
    assert DOC_PATHS <= _route_paths(create_app(s))
