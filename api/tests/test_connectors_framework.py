"""Connector framework: the allowlisting client, per-call secrets, the two configuration layers,
the Settings API, audit lines and links. No test reaches a real external service: remote hosts are
``FakeServer`` instances on 127.0.0.1 (``allow_insecure_loopback``)."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import select

from api.app.connectors.audit import audit_connector
from api.app.connectors.base import ConnectorError, HostNotAllowed
from api.app.connectors.config import load_install, secret, validate_app
from api.app.connectors.http import ConnectorClient, redact
from api.app.connectors.links import links_for, upsert_link
from api.app.deps import require_roles
from api.app.main import create_app
from api.app.models import Application, AuditLog, Process, Project, Upload
from api.tests.fakes.server import FakeServer, responder
from api.tests.test_auth import CLIENT, ROLE_GROUPS, SECRET, TENANT, as_roles

ADMIN = {"X-User": "ops.sam"}
ARCHITECT = {"X-User": "architect.lee"}


async def _noop() -> None:
    return None


# ------------------------------------------------------------------ fakes


@pytest.fixture
def two_fake_hosts():
    """``a`` answers every request with a 302 to ``b``; ``b`` answers 200."""
    b = FakeServer(responder(lambda m, p, h: (200, {}, {"ok": True}))).start()
    a = FakeServer(responder(lambda m, p, h: (302, {"location": f"{b.url}/landing"}, b""))).start()
    yield a, b
    a.stop()
    b.stop()


@pytest.fixture
def fake_429_then_200():
    calls = {"n": 0}

    def handle(method, path, headers):
        calls["n"] += 1
        if calls["n"] == 1:
            return 429, {"retry-after": "2"}, {"message": "slow down"}
        return 200, {}, {"ok": True}

    with FakeServer(responder(handle)) as srv:
        yield srv


@pytest.fixture
def fake_ok():
    with FakeServer(responder(lambda m, p, h: (200, {}, {"login": "exiqo-bot"}))) as srv:
        yield srv


# ------------------------------------------------------------------ apps


@pytest.fixture
def admin() -> dict[str, str]:
    return ADMIN


GITHUB_INSTALL = {"github": {"enabled": True, "baseUrl": "https://github.gmf.example.com",
                             "allowedHosts": ["github.gmf.example.com"]}}
SECRET_NAMES = {"github": {"token": "exiqo-github-token"},
                "atlassian": {"token": "exiqo-atlassian-token", "user": "exiqo-atlassian-user"}}


async def _client_for(app):
    async with (app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                  timeout=60) as c):
        c.app = app  # type: ignore[attr-defined]
        yield c


@pytest.fixture
async def client_with_github_install(settings, app):  # ``app`` resets the schema first
    s = settings.model_copy(update={"connectors": json.dumps(GITHUB_INSTALL),
                                    "connector_secret_names": json.dumps(SECRET_NAMES)})
    async for c in _client_for(create_app(s)):
        yield c


@pytest.fixture
async def client_with_fake_github(settings, app, fake_ok, tmp_path, monkeypatch):
    """GitHub enabled in the install against ``fake_ok`` (loopback allowed), token mounted."""
    (tmp_path / "gh").write_text("t0k-secret\n")
    monkeypatch.setenv("GITHUB_TOKEN_FILE", str(tmp_path / "gh"))
    install = {"github": {"enabled": True, "baseUrl": fake_ok.url, "allowedHosts": [fake_ok.host]}}
    s = settings.model_copy(update={"connectors": json.dumps(install),
                                    "connectors_allow_insecure_loopback": True})
    async for c in _client_for(create_app(s)):
        yield c


@pytest.fixture
async def client_oidc(settings, app):
    s = settings.model_copy(update={
        "auth_mode": "oidc", "oidc_tenant_id": TENANT, "oidc_client_id": CLIENT,
        "oidc_client_secret": "client-secret", "session_secret": SECRET, "role_groups": ROLE_GROUPS,
        "connectors": json.dumps(GITHUB_INSTALL)})
    async for c in _client_for(create_app(s)):
        yield c


@pytest.fixture
def viewer_token() -> dict[str, str]:
    return as_roles("viewer")


# ------------------------------------------------------------------ client


async def test_off_site_redirect_is_refused_and_token_not_forwarded(two_fake_hosts):  # Review Focus 1
    a, b = two_fake_hosts  # a answers 302 to b
    c = ConnectorClient(a.url, [a.host], lambda: {"Authorization": "Bearer t0k"},
                        allow_insecure_loopback=True)
    with pytest.raises(HostNotAllowed, match=b.host):
        await c.request("GET", "/x")
    assert b.requests == []
    assert [r.headers.get("authorization") for r in a.requests] == ["Bearer t0k"]


async def test_on_host_redirects_are_followed_up_to_three(fake_ok):
    hops = {"n": 0}

    def handle(method, path, headers):
        hops["n"] += 1
        return 302, {"location": f"/hop{hops['n']}"}, b""

    with FakeServer(responder(handle)) as loop:
        c = ConnectorClient(loop.url, [loop.host], dict, allow_insecure_loopback=True)
        with pytest.raises(ConnectorError, match="too many redirects"):
            await c.request("GET", "/")
        assert len(loop.requests) == 4  # the request and three redirects


async def test_https_required_without_loopback_flag():
    with pytest.raises(HostNotAllowed):
        await ConnectorClient("http://127.0.0.1:1", ["127.0.0.1"], dict).request("GET", "/")


async def test_host_outside_the_allowlist_is_refused_before_any_request(fake_ok):
    c = ConnectorClient(fake_ok.url, ["github.gmf.example.com"], dict, allow_insecure_loopback=True)
    with pytest.raises(HostNotAllowed, match=fake_ok.host):
        await c.request("GET", "/")
    with pytest.raises(HostNotAllowed):  # an absolute URL to another host, too
        await ConnectorClient(fake_ok.url, [fake_ok.host], dict, allow_insecure_loopback=True).request(
            "GET", "https://elsewhere.gmf.example.com/x")
    assert fake_ok.requests == []


async def test_retry_after_is_honoured(fake_429_then_200, monkeypatch):
    sleeps = []
    monkeypatch.setattr(asyncio, "sleep", lambda s: sleeps.append(s) or _noop())
    r = await ConnectorClient(fake_429_then_200.url, [fake_429_then_200.host], dict,
                              allow_insecure_loopback=True).request("GET", "/")
    assert r.status_code == 200 and sleeps == [2]


async def test_retries_stop_after_three(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda s: _noop())
    with FakeServer(responder(lambda m, p, h: (503, {"retry-after": "120"}, b"down"))) as srv:
        r = await ConnectorClient(srv.url, [srv.host], dict, allow_insecure_loopback=True).request("GET", "/")
        assert r.status_code == 503 and len(srv.requests) == 4


async def test_oversized_response_is_refused():
    big = b"x" * (20 * 1024 * 1024 + 1)
    with FakeServer(responder(lambda m, p, h: (200, {}, big))) as srv, pytest.raises(ConnectorError, match="20 MB"):
        await ConnectorClient(srv.url, [srv.host], dict, allow_insecure_loopback=True).request("GET", "/")


async def test_json_error_is_redacted(fake_ok):
    def handle(method, path, headers):
        return 401, {}, {"message": f"bad credentials {headers.get('authorization')}"}

    with FakeServer(responder(handle)) as srv:
        c = ConnectorClient(srv.url, [srv.host], lambda: {"Authorization": "Bearer t0k"},
                            allow_insecure_loopback=True)
        with pytest.raises(ConnectorError) as err:
            await c.json("GET", "/user")
        assert err.value.status == 401 and "t0k" not in str(err.value)


def test_secret_is_read_per_call(tmp_path, monkeypatch):  # Review Focus 2
    f = tmp_path / "github_token"
    f.write_text("one\n")
    monkeypatch.setenv("GITHUB_TOKEN_FILE", str(f))
    assert secret("github_token") == "one"
    f.write_text("two\n")
    assert secret("github_token") == "two"


def test_secret_falls_back_to_the_plain_field(monkeypatch, settings):
    monkeypatch.delenv("FIGMA_TOKEN_FILE", raising=False)
    assert secret("figma_token", settings.model_copy(update={"figma_token": "plain"})) == "plain"


def test_errors_are_redacted():
    assert "t0k" not in redact("401 for Bearer t0k at /user", ["t0k"])


# ------------------------------------------------------------------ configuration


def test_empty_install_disables_everything(settings):
    install = load_install(settings)
    assert set(install) == {"github", "atlassian", "figma"}
    assert not any(c.enabled for c in install.values())


def test_install_reads_the_chart_json(settings):
    install = load_install(settings.model_copy(update={"connectors": json.dumps(GITHUB_INSTALL)}))
    assert install["github"].enabled and install["github"].allowed_hosts == ["github.gmf.example.com"]


def test_app_url_must_be_https(settings):
    install = load_install(settings.model_copy(update={"connectors": json.dumps(GITHUB_INSTALL)}))
    with pytest.raises(ValueError, match="https required"):
        validate_app("github", install["github"], {"base_url": "http://github.gmf.example.com"})


# ------------------------------------------------------------------ Settings API


async def test_app_layer_cannot_exceed_install(client, admin):
    r = await client.put("/settings/connectors", json={"github": {"enabled": True}}, headers=admin)
    assert r.status_code == 422 and r.json()["detail"] == "enable it in the install first (connectors.github.enabled)"


async def test_base_url_must_be_allowlisted_https(client_with_github_install, admin):
    r = await client_with_github_install.put("/settings/connectors",
                                             json={"github": {"base_url": "https://evil.example.org"}},
                                             headers=admin)
    assert r.status_code == 422 and "not in allowedHosts" in r.json()["detail"]


async def test_settings_put_stores_and_audits(client_with_github_install, admin):
    c = client_with_github_install
    r = await c.put("/settings/connectors",
                    json={"github": {"enabled": True, "default_owner": "gmf"}}, headers=admin)
    assert r.status_code == 200, r.text
    gh = r.json()["github"]
    assert gh["enabled"] and gh["install_enabled"] and gh["default_owner"] == "gmf"
    assert gh["base_url"] == "https://github.gmf.example.com"
    assert (await c.get("/connectors")).json()["github"] == {"enabled": True}
    async with c.app.state.sessionmaker() as s:
        rows = (await s.execute(select(AuditLog).where(AuditLog.action == "connector settings changed"))).scalars().all()
    assert rows and rows[0].detail == {"fields": {"github": ["default_owner", "enabled"]}}


async def test_settings_put_needs_admin(client_with_github_install):
    r = await client_with_github_install.put("/settings/connectors", json={"github": {"enabled": True}},
                                             headers=ARCHITECT)
    assert r.status_code == 403


async def test_view_never_contains_secret_values(client_with_github_install, admin, monkeypatch, tmp_path):
    (tmp_path / "t").write_text("s3cr3t")
    monkeypatch.setenv("GITHUB_TOKEN_FILE", str(tmp_path / "t"))
    body = (await client_with_github_install.get("/settings/connectors", headers=admin)).text
    assert "s3cr3t" not in body and '"secrets_present"' in body
    gh = json.loads(body)["github"]
    assert gh["secrets_present"] == {"token": True} and gh["secret_names"] == {"token": "exiqo-github-token"}


async def test_non_admin_view_has_flags_only(client_with_github_install):
    gh = (await client_with_github_install.get("/settings/connectors", headers=ARCHITECT)).json()["github"]
    assert gh == {"install_enabled": True, "enabled": False}


async def test_connectors_flags(client):
    assert (await client.get("/connectors")).json() == {
        "github": {"enabled": False},
        "atlassian": {"enabled": False, "confluence": False, "jira": False},
        "figma": {"enabled": False}}


async def test_viewer_cannot_test_in_oidc(client_oidc, viewer_token):
    assert (await client_oidc.post("/connectors/github/test", headers=viewer_token)).status_code == 403


async def test_disabled_connector_test_is_409_and_audited(client_with_fake_github, admin, fake_ok):
    r = await client_with_fake_github.post("/connectors/github/test", headers=admin)
    assert r.status_code == 409 and r.json()["detail"] == "the GitHub connector is not enabled"
    assert fake_ok.requests == []
    async with client_with_fake_github.app.state.sessionmaker() as s:
        row = (await s.execute(select(AuditLog).where(AuditLog.action == "connector github test"))).scalar_one()
    assert row.detail["outcome"] == "refused"


async def test_missing_credential_is_409(client_with_fake_github, admin, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN_FILE")
    c = client_with_fake_github
    assert (await c.put("/settings/connectors", json={"github": {"enabled": True}}, headers=admin)).status_code == 200
    r = await c.post("/connectors/github/test", headers=admin)
    assert r.status_code == 409
    assert r.json()["detail"] == "the GitHub connector has no credential: mount GITHUB_TOKEN_FILE"


async def test_enabled_test_runs_and_records_last_test(client_with_fake_github, admin):
    c = client_with_fake_github
    assert (await c.put("/settings/connectors", json={"github": {"enabled": True}}, headers=admin)).status_code == 200
    r = await c.post("/connectors/github/test", headers=admin)
    assert r.status_code == 200 and r.json()["ok"] is False  # Task 3 implements GitHub
    last = (await c.get("/settings/connectors", headers=admin)).json()["github"]["last_test"]
    assert last["actor"] == "ops.sam" and last["result"]["message"] == "not implemented yet"
    assert "t0k-secret" not in (await c.get("/settings/connectors", headers=admin)).text


async def test_unknown_kind_is_422(client, admin):
    assert (await client.post("/connectors/gitlab/test", headers=admin)).status_code == 422


# ------------------------------------------------------------------ links, roles


async def _process(app) -> Process:
    async with app.state.sessionmaker() as s:
        project = Project(name="links")
        s.add(project)
        await s.flush()
        upload = Upload(project_id=project.id, source_kind="path", source_name="x", root_dir="/tmp")
        s.add(upload)
        await s.flush()
        application = Application(project_id=project.id, upload_id=upload.id, app_key="a", name="a",
                                  dialect="bw5", ir={})
        s.add(application)
        await s.flush()
        proc = Process(project_id=project.id, application_id=application.id, upload_id=upload.id,
                       ir_id="p", name="p", dialect="bw5", source_path="p.process", ir={}, spec_hash="h")
        s.add(proc)
        await s.commit()
        return proc


async def test_links_upsert_once_per_local_ref(client, app):
    proc = await _process(app)
    async with app.state.sessionmaker() as s:
        fields = {"project_id": proc.project_id, "process_id": proc.id, "kind": "atlassian",
                  "object_type": "confluence_page", "local_ref": "architecture", "created_by": "architect.lee"}
        await upsert_link(s, **fields, remote_id="1", remote_url="https://c.gmf.example.com/1", remote_version=1)
        await upsert_link(s, **fields, remote_id="1", remote_url="https://c.gmf.example.com/1", remote_version=2)
        await s.commit()
        links = await links_for(s, proc.id)
    assert len(links) == 1 and links[0].remote_version == 2 and links[0].stale is False
    body = (await client.get(f"/processes/{proc.id}/links")).json()
    assert [(x["object_type"], x["remote_version"]) for x in body] == [("confluence_page", 2)]
    assert (await client.get("/processes/nope/links")).status_code == 404


async def test_audit_connector_writes_the_action(app):
    proc = await _process(app)
    async with app.state.sessionmaker() as s:
        await audit_connector(s, "architect.lee", "atlassian", "publish", project_id=proc.project_id,
                              process_id=proc.id, target="page 1", outcome="ok", detail={"version": 2})
        await s.commit()
        row = (await s.execute(select(AuditLog).where(AuditLog.action == "connector atlassian publish"))).scalar_one()
    assert (row.process_id, row.actor) == (proc.id, "architect.lee")
    assert row.detail == {"target": "page 1", "outcome": "ok", "version": 2}


async def test_require_roles_messages(client_oidc):
    probe = FastAPI()

    @probe.post("/probe", dependencies=[Depends(require_roles("architect", "admin"))])
    async def probe_route() -> dict:
        return {}

    probe.state = client_oidc.app.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=probe), base_url="http://test") as c:
        r = await c.post("/probe", headers=as_roles("viewer"))
        assert r.status_code == 403 and r.json()["detail"] == "viewer is read only"
        r = await c.post("/probe", headers=as_roles("sme"))
        assert r.status_code == 403 and r.json()["detail"] == "this needs one of: architect, admin"
        assert (await c.post("/probe", headers=as_roles("architect"))).status_code == 200
        assert (await c.post("/probe", headers=as_roles("admin"))).status_code == 200
