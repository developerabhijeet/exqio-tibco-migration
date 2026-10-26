"""Target stack: project field, Settings default, ``GET /stacks``, the migration."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from api.app.db import asyncpg_url, make_engine
from api.app.routers import stacks as stacks_mod
from api.tests.conftest import TEST_DSN
from api.tests.test_auth import (  # noqa: F401  fixtures and helper
    as_roles,
    fake_entra,
    oidc_app,
    oidc_client,
    oidc_settings,
    signer,
)

ADMIN = {"X-User": "ops.sam"}
REPO_ROOT = Path(__file__).resolve().parents[2]


async def test_create_project_defaults_to_settings_default(client):
    r = await client.put("/settings", json={"default_stack": "dotnet"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["default_stack"] == "dotnet"
    p = (await client.post("/projects", json={"name": "a"})).json()
    assert p["target_stack"] == "dotnet"
    assert (await client.get(f"/projects/{p['id']}")).json()["target_stack"] == "dotnet"


async def test_unavailable_stack_is_refused_with_reason(client, monkeypatch):
    monkeypatch.setattr(stacks_mod, "_toolchains", lambda: {"dotnet": {"present": True}})
    r = await client.post("/projects", json={"name": "a", "target_stack": "java"})
    assert r.status_code == 422 and r.json()["detail"].startswith("java is not available: ")
    r = await client.put("/settings", json={"default_stack": "go"}, headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"].startswith("go is not available: ")


async def test_patch_stack_needs_architect_or_admin(client, project_id):
    r = await client.patch(f"/projects/{project_id}", json={"target_stack": "dotnet"},
                           headers={"X-User": "sme.vicky"})
    assert r.status_code == 403 and "architect or admin" in r.json()["detail"]
    r = await client.patch(f"/projects/{project_id}", json={"target_stack": "dotnet"},
                           headers={"X-User": "architect.lee"})
    assert r.status_code == 200 and r.json()["target_stack"] == "dotnet"


async def test_patch_stack_refuses_unavailable_and_audits_change(client, project_id, monkeypatch):
    monkeypatch.setattr(stacks_mod, "_toolchains",
                        lambda: {"dotnet": {"present": True}, "java": {"present": True}, "mvn": {"present": True}})
    lee = {"X-User": "architect.lee"}
    r = await client.patch(f"/projects/{project_id}", json={"target_stack": "java"}, headers=lee)
    assert r.status_code == 422 and r.json()["detail"] == "java is not available: not implemented yet"
    r = await client.patch(f"/projects/{project_id}", json={"target_stack": "cobol"}, headers=lee)
    assert r.status_code == 422
    assert (await client.patch("/projects/nope", json={"target_stack": "dotnet"}, headers=lee)).status_code == 404


async def test_viewer_cannot_patch_in_oidc(oidc_client):  # noqa: F811
    pid = (await oidc_client.post("/projects", json={"name": "v"}, headers=as_roles("engineer"))).json()["id"]
    r = await oidc_client.patch(f"/projects/{pid}", json={"target_stack": "dotnet"}, headers=as_roles("viewer"))
    assert r.status_code == 403 and r.json()["detail"] == "viewer is read only"
    r = await oidc_client.patch(f"/projects/{pid}", json={"target_stack": "dotnet"}, headers=as_roles("engineer"))
    assert r.status_code == 403 and "architect or admin" in r.json()["detail"]
    r = await oidc_client.patch(f"/projects/{pid}", json={"target_stack": "dotnet"}, headers=as_roles("architect"))
    assert r.status_code == 200


async def test_stacks_endpoint_lists_four_with_status(client):
    body = (await client.get("/stacks")).json()
    assert body["default"] == "dotnet"
    assert [s["key"] for s in body["stacks"]] == ["dotnet", "java", "go", "python"]
    assert all({"available", "reason", "label", "required_tools", "default"} <= s.keys() for s in body["stacks"])
    assert [s["default"] for s in body["stacks"]] == [True, False, False, False]
    assert next(s for s in body["stacks"] if s["key"] == "java")["reason"] == "not implemented yet"


async def test_settings_view_carries_stacks(client):
    view = (await client.get("/settings")).json()
    assert view["default_stack"] == "dotnet" and view["stacks"][0]["key"] == "dotnet"


def _alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": asyncpg_url(TEST_DSN)}
    subprocess.run([sys.executable, "-m", "alembic", "-c", "api/alembic.ini", *args], cwd=REPO_ROOT, env=env,
                   check=True, capture_output=True, text=True)


async def test_existing_projects_and_builds_default_to_dotnet():
    engine = make_engine(asyncpg_url(TEST_DSN))
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS api CASCADE"))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"Postgres is not available: {exc}")
    try:
        _alembic("upgrade", "0007")
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO api.projects (id, name) VALUES ('p1', 'old')"))
        _alembic("upgrade", "head")
        async with engine.connect() as conn:
            assert await conn.scalar(text("select target_stack from api.projects where id='p1'")) == "dotnet"
            assert await conn.scalar(text(
                "select column_default from information_schema.columns "
                "where table_schema='api' and table_name='builds' and column_name='stack'")) == "'dotnet'::text"
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS api CASCADE"))
        await engine.dispose()
