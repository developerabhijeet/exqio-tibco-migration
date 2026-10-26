"""Target stack: project field, Settings default, ``GET /stacks``, the migration."""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from api.app.db import asyncpg_url, make_engine
from api.app.routers import stacks as stacks_mod
from api.app.services import settings as settings_svc
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


async def test_unavailable_stack_is_refused_with_reason(client):
    r = await client.post("/projects", json={"name": "a", "target_stack": "go"})
    assert r.status_code == 422 and r.json()["detail"] == "go is not available: not implemented yet"
    r = await client.put("/settings", json={"default_stack": "go"}, headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"].startswith("go is not available: ")


async def test_not_enabled_stack_is_refused(client, app):
    app.state.settings.enabled_stacks = "java"
    r = await client.post("/projects", json={"name": "a", "target_stack": "dotnet"})
    assert r.status_code == 422
    assert r.json()["detail"] == "dotnet is not available: not enabled on this install (stacks.enabled)"


async def test_toolchain_presence_is_not_checked_on_the_api(client, monkeypatch):
    monkeypatch.setattr(settings_svc, "detect_toolchains", dict)
    p = await client.post("/projects", json={"name": "a", "target_stack": "dotnet"})
    assert p.status_code == 201
    stacks = (await client.get("/stacks")).json()["stacks"]
    assert stacks[0]["reason"] == "toolchain missing: dotnet"  # shown, not enforced
    assert stacks[0]["available"] is False and stacks[0]["selectable"] is True
    assert stacks[0]["toolchain_missing"] == stacks[0]["required_tools"]
    go = next(s for s in stacks if s["key"] == "go")
    assert go["selectable"] is False  # not implemented: refused whatever the tools


async def test_only_an_admin_changes_the_default_stack(client):
    ok = await client.put("/settings", json={"default_stack": "java"}, headers=ADMIN)
    assert ok.status_code == 200 and ok.json()["default_stack"] == "java"
    for user in ("architect.lee", "viewer.val"):
        r = await client.put("/settings", json={"default_stack": "dotnet"}, headers={"X-User": user})
        assert r.status_code == 403 and r.json()["detail"] == "this needs the admin role", user
    # a request that leaves the default as it is stays open to the roles that may edit other settings
    same = await client.put("/settings", json={"default_stack": "java", "langfuse_enabled": False},
                            headers={"X-User": "architect.lee"})
    assert same.status_code == 200 and same.json()["default_stack"] == "java"
    await client.put("/settings", json={"default_stack": "dotnet"}, headers=ADMIN)


async def test_stacks_report_what_the_caller_may_do(client):
    for user, change, default in [("architect.lee", True, False), ("ops.sam", True, True), ("viewer.val", False, False)]:
        body = (await client.get("/stacks", headers={"X-User": user})).json()
        assert (body["can_change_stack"], body["can_set_default"]) == (change, default), user


async def test_patch_stack_needs_architect_or_admin(client, project_id):
    r = await client.patch(f"/projects/{project_id}", json={"target_stack": "dotnet"},
                           headers={"X-User": "sme.vicky"})
    assert r.status_code == 403 and "architect or admin" in r.json()["detail"]
    r = await client.patch(f"/projects/{project_id}", json={"target_stack": "dotnet"},
                           headers={"X-User": "architect.lee"})
    assert r.status_code == 200 and r.json()["target_stack"] == "dotnet"


async def test_patch_stack_changes_it_and_audits_old_and_new(client, project_id, monkeypatch):
    real = stacks_mod.stack_status
    monkeypatch.setattr(stacks_mod, "stack_status", lambda key, tc, en: real("dotnet", tc, en).model_copy(
        update={"key": key}))
    lee = {"X-User": "architect.lee"}
    r = await client.patch(f"/projects/{project_id}", json={"target_stack": "java"}, headers=lee)
    assert r.status_code == 200 and r.json()["target_stack"] == "java"
    assert (await client.get(f"/projects/{project_id}")).json()["target_stack"] == "java"
    audit = [a for a in (await client.get(f"/projects/{project_id}/audit")).json()
             if a["action"] == "project stack changed"]
    assert len(audit) == 1 and audit[0]["detail"] == {"old": "dotnet", "new": "java"}
    assert audit[0]["actor"] == "architect.lee"


async def test_patch_stack_refuses_unavailable_and_unknown(client, project_id):
    lee = {"X-User": "architect.lee"}
    r = await client.patch(f"/projects/{project_id}", json={"target_stack": "go"}, headers=lee)
    assert r.status_code == 422 and r.json()["detail"] == "go is not available: not implemented yet"
    r = await client.patch(f"/projects/{project_id}", json={"target_stack": "cobol"}, headers=lee)
    assert r.status_code == 422
    assert (await client.patch("/projects/nope", json={"target_stack": "dotnet"}, headers=lee)).status_code == 404


def test_invalid_stored_default_stack_falls_back_to_dotnet(settings, caplog):
    settings_svc.apply_stored(settings, {"default_stack": "cobol"})
    assert settings.default_stack == "dotnet" and "cobol" in caplog.text


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
    assert next(s for s in body["stacks"] if s["key"] == "go")["reason"] == "not implemented yet"


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


async def test_design_job_builds_for_the_project_stack(client, app, monkeypatch):
    from api.app.models import Project
    from api.app.services import design as design_svc
    from api.tests.test_build_verify import _validated, _wait_job

    pid, proc_id = await _validated(client)
    r = await client.post(f"/processes/{proc_id}/design")
    assert (await _wait_job(client, r.json()["job_id"]))["status"] == "done"
    stored = (await client.get(f"/processes/{proc_id}/design")).json()
    assert stored["stack"] == "dotnet"

    # the project's stack reaches the skeleton (java is not implemented, so the real call gets dotnet)
    async with app.state.sessionmaker() as s:
        (await s.get(Project, pid)).target_stack = "java"
        await s.commit()
    seen: list[str] = []
    real = design_svc.build_design_skeleton

    def spy(*args, **kwargs):  # however the job passes it, positionally or by name
        bound = inspect.signature(real).bind(*args, **kwargs)
        bound.apply_defaults()
        seen.append(bound.arguments["stack"])
        bound.arguments["stack"] = "dotnet"
        return real(*bound.args, **bound.kwargs)

    monkeypatch.setattr(design_svc, "build_design_skeleton", spy)
    r = await client.post(f"/processes/{proc_id}/design")
    job = await _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done" and seen == ["java"]
