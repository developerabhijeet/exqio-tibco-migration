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

from api.app.connectors import registry
from api.app.connectors.audit import audit_connector
from api.app.connectors.base import ConnectorDisabled, ConnectorError, HostNotAllowed
from api.app.connectors.config import load_install, secret, validate_app
from api.app.connectors.http import ConnectorClient, redact, ssl_context
from api.app.connectors.links import links_for, upsert_link
from api.app.connectors.registry import BaseConnector, get_connector
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


def test_plain_field_is_a_fallback_only_with_the_loopback_flag(monkeypatch, settings):
    """A developer's exported ``FIGMA_TOKEN`` is read only for local development (the loopback flag)."""
    plain = settings.model_copy(update={"figma_token": "plain"})
    assert secret("figma_token", plain) == ""
    assert secret("figma_token", plain.model_copy(update={"connectors_allow_insecure_loopback": True})) == "plain"
    monkeypatch.setenv("FIGMA_TOKEN", "from-the-shell")
    assert secret("figma_token") == ""
    monkeypatch.setenv("CONNECTORS_ALLOW_INSECURE_LOOPBACK", "true")
    assert secret("figma_token") == "from-the-shell"


def test_install_url_off_its_allowlist_is_logged_at_start_and_not_ready(settings, caplog):
    from api.app.connectors.base import ConnectorMisconfigured
    from api.app.main import check_connector_settings

    install = {"github": {"enabled": True, "baseUrl": "https://github.gmf.example",
                          "allowedHosts": ["git.other.example"]},
               "figma": {"enabled": True, "apiUrl": "https://api.figma.example", "allowedHosts": ["api.figma.example"]},
               "atlassian": {"enabled": False, "confluenceUrl": "https://wiki.off.example"}}
    s = settings.model_copy(update={"connectors": json.dumps(install)})
    with caplog.at_level("ERROR", logger="api.app.main"):
        check_connector_settings(s)  # logged, not raised: the other connectors still start
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    expected = "connector github is not ready: connectors.github.baseUrl: host github.gmf.example not in allowedHosts"
    assert errors == [expected]
    stored = {"connectors": {"github": {"enabled": True}, "figma": {"enabled": True}}}
    with pytest.raises(ConnectorMisconfigured, match="GitHub connector is not ready: .*not in allowedHosts"):
        get_connector("github", settings=s, stored=stored).ready()
    assert get_connector("figma", settings=s, stored=stored).config.install_problem() is None


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
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["account"] == "exiqo-bot"
    last = (await c.get("/settings/connectors", headers=admin)).json()["github"]["last_test"]
    assert last["actor"] == "ops.sam" and last["result"]["account"] == "exiqo-bot"
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


# ------------------------------------------------------------------ review fixes (round 1)


def _ca_and_leaf(tmp_path):
    """A private CA (like GMF's) and a server certificate it signed for 127.0.0.1."""
    import datetime as dt
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = dt.datetime.now(dt.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "GMF Test Root CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
          .not_valid_after(now + dt.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                       content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False, encipher_only=False,
                                       decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))
    ca_pem = tmp_path / "gmf-ca.pem"
    ca_pem.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_pem = tmp_path / "server.pem"
    cert_pem.write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return ca_pem, cert_pem


@pytest.fixture
def tls_server(tmp_path):
    """An https server on 127.0.0.1 whose certificate only the private CA verifies."""
    import ssl
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    ca_pem, cert_pem = _ca_and_leaf(tmp_path)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_pem)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host = f"127.0.0.1:{httpd.server_address[1]}"
    yield f"https://{host}", host, str(ca_pem)
    httpd.shutdown()
    httpd.server_close()


async def test_private_ca_bundle_is_trusted(tls_server, monkeypatch):
    url, host, ca = tls_server
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    r = await ConnectorClient(url, [host], dict, ca_bundle=ca).request("GET", "/")
    assert r.status_code == 200 and r.json() == {"ok": True}
    with pytest.raises(ConnectorError, match="(?i)certificate"):  # certifi alone does not know the CA
        await ConnectorClient(url, [host], dict).request("GET", "/")


async def test_ssl_cert_file_is_the_default_bundle(tls_server, monkeypatch):
    url, host, ca = tls_server
    monkeypatch.setenv("SSL_CERT_FILE", ca)
    assert (await ConnectorClient(url, [host], dict).request("GET", "/")).status_code == 200


def test_ca_bundle_setting_reaches_the_client(settings, tmp_path):
    ca_pem, _ = _ca_and_leaf(tmp_path)
    s = settings.model_copy(update={"connectors": json.dumps(GITHUB_INSTALL), "connectors_ca_bundle": str(ca_pem)})
    connector = get_connector("github", settings=s, stored={"connectors": {"github": {"enabled": True}}})
    client = connector.client("https://github.gmf.example.com")
    assert client.ca_bundle == str(ca_pem)
    names = [dict(x[0] for x in c["subject"]).get("commonName") for c in ssl_context(client.ca_bundle).get_ca_certs()]
    assert names == ["GMF Test Root CA"]


def test_disabled_connector_has_no_client(settings):
    connector = get_connector("github", settings=settings.model_copy(update={"connectors": json.dumps(GITHUB_INSTALL)}),
                              stored={})
    with pytest.raises(ConnectorDisabled):
        connector.client("https://github.gmf.example.com")


async def test_streamed_oversized_response_is_refused():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})  # no content-length
        chunk = b"x" * (1024 * 1024)
        for _ in range(21):
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b""})

    with FakeServer(app) as srv, pytest.raises(ConnectorError, match="20 MB"):
        await ConnectorClient(srv.url, [srv.host], dict, allow_insecure_loopback=True).request("GET", "/")


async def test_post_is_not_retried_on_502(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda s: _noop())
    with FakeServer(responder(lambda m, p, h: (502, {"retry-after": "1"}, b"bad gateway"))) as srv:
        c = ConnectorClient(srv.url, [srv.host], dict, allow_insecure_loopback=True)
        assert (await c.request("POST", "/issue", json={"a": 1})).status_code == 502
        assert len(srv.requests) == 1


async def test_post_is_retried_on_503_with_retry_after_and_429(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda s: _noop())
    answers = iter([(503, {"retry-after": "1"}, b""), (429, {}, b""), (201, {}, {"id": 1})])
    with FakeServer(responder(lambda m, p, h: next(answers))) as srv:
        c = ConnectorClient(srv.url, [srv.host], dict, allow_insecure_loopback=True)
        assert (await c.request("POST", "/issue", json={"a": 1})).status_code == 201
        assert len(srv.requests) == 3


async def test_params_are_not_reapplied_after_a_redirect():
    def handle(method, path, headers):
        return (302, {"location": "/b?page=2"}, b"") if path == "/a" else (200, {}, {"ok": True})

    with FakeServer(responder(handle)) as srv:
        c = ConnectorClient(srv.url, [srv.host], dict, allow_insecure_loopback=True)
        assert (await c.request("GET", "/a", params={"page": "1"})).status_code == 200
        assert [(r.path, r.query) for r in srv.requests] == [("/a", "page=1"), ("/b", "page=2")]


async def test_userinfo_and_bad_urls_are_refused(fake_ok, settings):
    c = ConnectorClient(fake_ok.url, [fake_ok.host], dict, allow_insecure_loopback=True)
    with pytest.raises(HostNotAllowed, match="credentials"):
        await c.request("GET", f"http://user:pw@{fake_ok.host}/")
    with pytest.raises(ConnectorError):
        await c.request("GET", "http://127.0.0.1:99999/")
    assert fake_ok.requests == []
    install = load_install(settings.model_copy(update={"connectors": json.dumps(GITHUB_INSTALL)}))
    with pytest.raises(ValueError, match="credentials in the URL"):
        validate_app("github", install["github"], {"base_url": "https://u:p@github.gmf.example.com"})


async def test_architect_cannot_test(client_with_github_install, client_oidc):
    r = await client_with_github_install.post("/connectors/github/test", headers=ARCHITECT)
    assert r.status_code == 403 and r.json()["detail"] == "this needs the admin role"
    r = await client_oidc.post("/connectors/github/test", headers=as_roles("architect"))
    assert r.status_code == 403 and r.json()["detail"] == "this needs the admin role"


async def test_unexpected_test_error_is_failed_redacted_and_recorded(client_with_fake_github, admin, monkeypatch):
    class Broken(BaseConnector):
        async def test(self):
            raise RuntimeError(f"boom with {self.credential()}")

    monkeypatch.setitem(registry._CLASSES, "github", Broken)
    c = client_with_fake_github
    assert (await c.put("/settings/connectors", json={"github": {"enabled": True}}, headers=admin)).status_code == 200
    r = await c.post("/connectors/github/test", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and "RuntimeError" in body["message"] and "t0k-secret" not in r.text
    view = (await c.get("/settings/connectors", headers=admin)).json()["github"]
    assert view["last_test"]["result"]["ok"] is False
    async with c.app.state.sessionmaker() as s:
        row = (await s.execute(select(AuditLog).where(AuditLog.action == "connector github test"))).scalar_one()
    assert row.detail["outcome"] == "failed" and "t0k-secret" not in json.dumps(row.detail)
