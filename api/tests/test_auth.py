"""Entra ID sign-in, JWT validation, the session cookie and server-side roles (``AUTH_MODE``).

No test touches the network: a fake Entra (``FakeEntra``) answers the tenant's discovery
document, its JWKS and the token endpoint through ``httpx.MockTransport``, signing with an RSA
key generated per test.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from api.app.auth import roles as roles_mod
from api.app.auth import session as session_mod
from api.app.auth.jwt import JwksCache, TokenInvalid, validate_jwt
from api.app.auth.oidc import Oidc
from api.app.auth.roles import GATE_OWNERS, Principal, roles_from_claims, roles_from_groups
from api.app.config import Settings
from api.app.main import create_app
from api.app.services import hosting

TENANT = "tid-0001"
CLIENT = "cid-0001"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
DISCOVERY = f"{ISSUER}/.well-known/openid-configuration"
JWKS_URI = f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"
TOKEN_URI = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
AUTHORIZE_URI = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize"
ROLE_GROUPS = {"engineer": ["g-eng"], "qa": ["g-qa"], "admin": ["g-admin"], "sme": ["Exiqo.Sme"],
               "viewer": ["g-view"]}
SECRET = "s" * 40
SYNTHETIC = Path(__file__).resolve().parents[2] / "samples" / "synthetic-gmf"


class Signer:
    """One RSA key published under ``kid``; ``token(**claims)`` signs a valid token by default."""

    def __init__(self, kid: str = "k1") -> None:
        self.kid = kid
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwk(self) -> dict:
        return {**RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True), "kid": self.kid,
                "use": "sig", "alg": "RS256"}

    def token(self, **claims) -> str:
        now = int(time.time())
        body = {"iss": ISSUER, "aud": CLIENT, "iat": now, "nbf": now, "exp": now + 600,
                "oid": "oid-1", "preferred_username": "ravi@gmf.example", "name": "Ravi",
                "groups": ["g-eng"], **claims}
        body = {k: v for k, v in body.items() if v is not None}
        return jwt.encode(body, self.key, algorithm="RS256", headers={"kid": self.kid})


class FakeEntra:
    """Discovery, JWKS and the token endpoint of one tenant, counting the JWKS fetches."""

    def __init__(self, *signers: Signer) -> None:
        self.signers = list(signers)
        self.jwks_fetches = 0
        self.token_requests: list[dict[str, list[str]]] = []
        self.id_claims: dict = {}
        self.nonce = ""

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == DISCOVERY:
            return httpx.Response(200, json={"issuer": ISSUER, "authorization_endpoint": AUTHORIZE_URI,
                                             "token_endpoint": TOKEN_URI, "jwks_uri": JWKS_URI})
        if url == JWKS_URI:
            self.jwks_fetches += 1
            return httpx.Response(200, json={"keys": [s.jwk() for s in self.signers]})
        if url == TOKEN_URI and request.method == "POST":
            form = parse_qs(request.content.decode())
            self.token_requests.append(form)
            if form.get("code") != ["the-code"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"id_token": self.signers[0].token(
                nonce=self.nonce, **self.id_claims), "token_type": "Bearer"})
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def signer() -> Signer:
    return Signer()


@pytest.fixture
def fake_entra(signer) -> FakeEntra:
    return FakeEntra(signer)


def jwks_for(fake: FakeEntra) -> JwksCache:
    oidc = Oidc(_oidc_settings(), http=fake.client())
    return oidc.jwks


def _oidc_settings(**extra) -> Settings:
    return Settings(auth_mode="oidc", oidc_tenant_id=TENANT, oidc_client_id=CLIENT,
                    oidc_client_secret="client-secret", session_secret=SECRET,
                    role_groups=ROLE_GROUPS, _env_file=None, **extra)


# ------------------------------------------------------------------ JWT validation
async def test_valid_jwt_accepted(signer, fake_entra):
    claims = await validate_jwt(signer.token(), tenant_id=TENANT, audience=CLIENT,
                                jwks=jwks_for(fake_entra))
    assert claims["oid"] == "oid-1"


async def test_api_audience_accepted(signer, fake_entra):
    token = signer.token(aud=f"api://{CLIENT}")
    claims = await validate_jwt(token, tenant_id=TENANT, audience=[CLIENT, f"api://{CLIENT}"],
                                jwks=jwks_for(fake_entra))
    assert claims["aud"] == f"api://{CLIENT}"


# Times are offsets from when the test runs, not from collection: a full suite reaches this file
# more than ten minutes after collecting it, by which point a collection-time "nbf = now + 600"
# is already in the past and the token is valid.
@pytest.mark.parametrize("mutate,reason", [
    ({"iss": "https://login.microsoftonline.com/other/v2.0"}, "issuer"),
    ({"aud": "someone-else"}, "audience"),
    ({"exp": -120}, "expired"),
    ({"nbf": 600}, "not yet valid"),
    ({"exp": None}, "exp")])
async def test_jwt_rejected(mutate, reason, signer, fake_entra):
    now = int(time.time())
    claims = {k: (now + v if k in ("exp", "nbf") and v is not None else v) for k, v in mutate.items()}
    with pytest.raises(TokenInvalid) as exc:
        await validate_jwt(signer.token(**claims), tenant_id=TENANT, audience=CLIENT,
                           jwks=jwks_for(fake_entra))
    assert reason in str(exc.value).lower()


async def test_jwt_leeway_is_60_seconds(signer, fake_entra):
    token = signer.token(exp=int(time.time()) - 30)
    assert (await validate_jwt(token, tenant_id=TENANT, audience=CLIENT,
                               jwks=jwks_for(fake_entra)))["oid"] == "oid-1"


async def test_jwt_signed_by_another_key_rejected(signer, fake_entra):
    stranger = Signer(kid=signer.kid)  # same kid, different key
    with pytest.raises(TokenInvalid) as exc:
        await validate_jwt(stranger.token(), tenant_id=TENANT, audience=CLIENT,
                           jwks=jwks_for(fake_entra))
    assert "signature" in str(exc.value).lower()


async def test_jwt_hs256_rejected(signer, fake_entra):
    token = jwt.encode({"iss": ISSUER, "aud": CLIENT, "exp": int(time.time()) + 60}, "x" * 32,
                       algorithm="HS256", headers={"kid": signer.kid})
    with pytest.raises(TokenInvalid):
        await validate_jwt(token, tenant_id=TENANT, audience=CLIENT, jwks=jwks_for(fake_entra))


async def test_unknown_kid_refetches_jwks_once(signer):
    rotated = Signer(kid="k2")
    fake = FakeEntra(signer)
    jwks = jwks_for(fake)
    await validate_jwt(signer.token(), tenant_id=TENANT, audience=CLIENT, jwks=jwks)
    assert fake.jwks_fetches == 1
    fake.signers.append(rotated)  # the tenant rotated its keys after our first fetch
    claims = await validate_jwt(rotated.token(), tenant_id=TENANT, audience=CLIENT, jwks=jwks)
    assert claims["oid"] == "oid-1" and fake.jwks_fetches == 2
    # a kid the tenant never published: one more refetch is not spent on it right away
    with pytest.raises(TokenInvalid):
        await validate_jwt(Signer(kid="k3").token(), tenant_id=TENANT, audience=CLIENT, jwks=jwks)
    assert fake.jwks_fetches == 2


# ------------------------------------------------------------------ roles
def test_groups_map_to_roles():
    assert roles_from_groups(["g-eng"], {"engineer": ["g-eng"]}) == {"engineer"}
    assert roles_from_groups(["g-eng", "g-qa", "x"], ROLE_GROUPS) == {"engineer", "qa"}
    assert roles_from_groups([], ROLE_GROUPS) == frozenset()


def test_app_roles_claim_maps_too():
    assert roles_from_claims({"groups": ["g-qa"], "roles": ["Exiqo.Sme"]}, ROLE_GROUPS) == {"qa", "sme"}


def test_group_overage_uses_app_roles_only(caplog):
    claims = {"_claim_names": {"groups": "src1"}, "_claim_sources": {"src1": {}},
              "roles": ["Exiqo.Sme"]}
    assert roles_from_claims(claims, ROLE_GROUPS) == {"sme"}
    assert "overage" in caplog.text.lower()


def test_unknown_role_keys_are_ignored():
    assert roles_from_groups(["g-x"], {"superuser": ["g-x"]}) == frozenset()


def test_gate_owners_mirror_the_web():
    assert GATE_OWNERS == {"G1": "sme", "G2": "sme", "G3": "architect", "G4": "engineer",
                           "G5": "qa", "G6": "ops"}


@pytest.mark.parametrize("user,expected", [
    ("sme.vicky", {"sme"}), ("architect.lee", {"architect"}), ("engineer.ravi", {"engineer"}),
    ("qa.priya", {"qa"}), ("ops.sam", {"ops", "admin"}), ("viewer.val", {"viewer"}),
    ("someone", set())])
def test_dev_persona_roles(user, expected):
    assert roles_mod.dev_principal(user).roles == expected


# ------------------------------------------------------------------ the app in oidc mode
def test_oidc_mode_refuses_to_start_without_secrets(settings):
    for missing in ("session_secret", "oidc_tenant_id", "oidc_client_id"):
        s = settings.model_copy(update={"auth_mode": "oidc", "session_secret": SECRET,
                                        "oidc_tenant_id": TENANT, "oidc_client_id": CLIENT,
                                        missing: ""})
        with pytest.raises(RuntimeError, match=missing.upper()):
            create_app(s)


@pytest.fixture
def oidc_settings(settings):
    return settings.model_copy(update={
        "auth_mode": "oidc", "oidc_tenant_id": TENANT, "oidc_client_id": CLIENT,
        "oidc_client_secret": "client-secret", "session_secret": SECRET,
        "oidc_redirect_uri": "https://exiqo.gmf.example/api/auth/callback",
        "role_groups": ROLE_GROUPS, "cors_origins": "https://web.gmf.example"})


@pytest.fixture
async def oidc_app(oidc_settings, fake_entra, app):  # ``app`` resets the schema first
    application = create_app(oidc_settings)
    async with application.router.lifespan_context(application):
        await application.state.oidc.aclose()
        application.state.oidc = Oidc(oidc_settings, http=fake_entra.client())
        yield application


@pytest.fixture
async def oidc_client(oidc_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=oidc_app),
                                 base_url="https://exiqo.gmf.example", timeout=60) as c:
        yield c


def cookie_for(principal: Principal) -> dict[str, str]:
    return {"Cookie": f"{session_mod.SESSION_COOKIE}={session_mod.dump_session(principal, SECRET)}"}


def as_roles(*roles: str) -> dict[str, str]:
    return cookie_for(Principal(oid="oid-9", upn="pat@gmf.example", name="Pat",
                                roles=frozenset(roles)))


async def test_oidc_mode_ignores_x_user(oidc_client):
    assert (await oidc_client.get("/auth/me", headers={"X-User": "ops.sam"})).status_code == 401


async def test_oidc_mode_refuses_anonymous_api_calls(oidc_client):
    assert (await oidc_client.get("/projects", headers={"X-User": "ops.sam"})).status_code == 401
    assert (await oidc_client.get("/projects", headers=as_roles("viewer"))).status_code == 200
    # signed in, but no role the platform knows: refused, not read-only
    assert (await oidc_client.get("/projects", headers=as_roles())).status_code == 403
    assert (await oidc_client.get("/system/brand")).status_code == 200
    assert (await oidc_client.get("/health")).status_code == 200


async def test_viewer_is_read_only(oidc_client):
    viewer = as_roles("viewer")
    assert (await oidc_client.get("/projects", headers=viewer)).status_code == 200
    r = await oidc_client.post("/projects", json={"name": "v"}, headers=viewer)
    assert r.status_code == 403 and r.json()["detail"] == "viewer is read only"
    r = await oidc_client.post("/jobs/nope/cancel", headers=viewer)
    assert r.status_code == 403 and r.json()["detail"] == "viewer is read only"
    r = await oidc_client.post("/builds/nope/run/proxy/x", headers=viewer)
    assert r.status_code == 403
    # a viewer who also holds a write role writes; admin counts as one
    assert (await oidc_client.post("/projects", json={"name": "a"},
                                   headers=as_roles("viewer", "qa"))).status_code == 201
    assert (await oidc_client.post("/projects", json={"name": "b"},
                                   headers=as_roles("admin"))).status_code == 201


async def test_settings_and_tokenomics_writes_need_admin(oidc_client):
    engineer, admin = as_roles("engineer"), as_roles("admin")
    assert (await oidc_client.get("/settings", headers=engineer)).status_code == 200
    r = await oidc_client.put("/settings", json={"langfuse_enabled": False}, headers=engineer)
    assert r.status_code == 403 and r.json()["detail"] == "this needs the admin role"
    r = await oidc_client.post("/providers/eval", json={"process_ids": ["p"]}, headers=engineer)
    assert r.status_code == 403
    r = await oidc_client.put("/settings", json={"langfuse_enabled": False}, headers=admin)
    assert 200 <= r.status_code < 300, r.text
    pid = (await oidc_client.post("/projects", json={"name": "t"}, headers=engineer)).json()["id"]
    r = await oidc_client.delete(f"/history/model-calls?project_id={pid}", headers=engineer)
    assert r.status_code == 403
    r = await oidc_client.delete(f"/history/model-calls?project_id={pid}", headers=admin)
    assert r.status_code == 200, r.text


async def test_dev_mode_writes_unchanged_for_any_persona(client):
    r = await client.put("/settings", json={"langfuse_enabled": False}, headers={"X-User": "qa.priya"})
    assert r.status_code == 200, r.text
    assert (await client.post("/projects", json={"name": "d"})).status_code == 201


async def test_auth_me_from_cookie(oidc_client):
    r = await oidc_client.get("/auth/me", headers=as_roles("qa", "engineer"))
    assert r.status_code == 200
    assert r.json() == {"oid": "oid-9", "upn": "pat@gmf.example", "name": "Pat",
                        "roles": ["engineer", "qa"]}


async def test_tampered_or_expired_cookie_refused(oidc_client, monkeypatch):
    good = session_mod.dump_session(Principal(oid="o", upn="u", name="n", roles=frozenset({"qa"})),
                                    SECRET)
    bad = good[:-2] + ("AA" if not good.endswith("AA") else "BB")
    r = await oidc_client.get("/auth/me", headers={"Cookie": f"{session_mod.SESSION_COOKIE}={bad}"})
    assert r.status_code == 401
    other = session_mod.dump_session(Principal(oid="o", upn="u", name="n", roles=frozenset()), "t" * 40)
    r = await oidc_client.get("/auth/me", headers={"Cookie": f"{session_mod.SESSION_COOKIE}={other}"})
    assert r.status_code == 401
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + session_mod.SESSION_MAX_AGE + 5)
    r = await oidc_client.get("/auth/me", headers={"Cookie": f"{session_mod.SESSION_COOKIE}={good}"})
    assert r.status_code == 401


async def test_bearer_token_accepted(oidc_client, signer):
    r = await oidc_client.get("/auth/me", headers={"Authorization": f"Bearer {signer.token(aud=f'api://{CLIENT}')}"})
    assert r.status_code == 200
    assert r.json()["upn"] == "ravi@gmf.example" and r.json()["roles"] == ["engineer"]
    r = await oidc_client.get("/auth/me", headers={"Authorization": f"Bearer {signer.token(aud='x')}"})
    assert r.status_code == 401


async def test_x_agent_still_forbidden_in_oidc(oidc_client):
    r = await oidc_client.post("/gates/p/G4", json={"state": "approved"},
                               headers={**as_roles("engineer"), "X-Agent": "analyst"})
    assert r.status_code == 403 and "agent" in r.json()["detail"]


async def test_gate_owner_enforced(oidc_client):
    r = await oidc_client.post("/gates/nope/G4", json={"state": "approved"}, headers=as_roles("qa"))
    assert r.status_code == 403 and r.json()["detail"] == "G4 is decided by the engineer role"
    # the owner gets past the role check (and then on to the process lookup)
    r = await oidc_client.post("/gates/nope/G4", json={"state": "approved"},
                               headers=as_roles("engineer"))
    assert r.status_code == 404


async def test_gate_decision_audit_records_oid_and_upn(oidc_app, oidc_client):
    from sqlalchemy import select

    from api.app.models import AuditLog

    sme = as_roles("sme")
    pid = (await oidc_client.post("/projects", json={"name": "gmf"}, headers=sme)).json()["id"]
    r = await oidc_client.post(f"/projects/{pid}/upload", json={"path": str(SYNTHETIC)}, headers=sme)
    assert r.status_code == 201, r.text
    job_id = (await oidc_client.post(f"/projects/{pid}/scan", json={}, headers=sme)).json()["job_id"]
    for _ in range(600):
        job = (await oidc_client.get(f"/jobs/{job_id}", headers=sme)).json()
        if job["status"] in ("done", "failed"):
            break
        await asyncio.sleep(0.2)
    assert job["status"] == "done", job
    proc_id = (await oidc_client.get(f"/projects/{pid}/processes", headers=sme)).json()[0]["id"]
    r = await oidc_client.post(f"/gates/{proc_id}/G1", json={"state": "rejected", "note": "no"},
                               headers=sme)
    assert r.status_code == 200, r.text
    assert r.json()["decided_by"] == "pat@gmf.example"
    async with oidc_app.state.sessionmaker() as s:
        row = (await s.execute(select(AuditLog).where(AuditLog.action == "gate rejected"))).scalar_one()
    assert row.actor == "pat@gmf.example"
    assert row.detail["actor_oid"] == "oid-9" and row.detail["actor_upn"] == "pat@gmf.example"


async def test_csrf_origin_check_on_cookie_writes(oidc_client):
    headers = as_roles("engineer")
    evil = await oidc_client.post("/gates/nope/G4", json={"state": "approved"},
                                  headers={**headers, "Origin": "https://evil.example"})
    assert evil.status_code == 403 and "origin" in evil.json()["detail"].lower()
    for origin in ("https://exiqo.gmf.example", "https://web.gmf.example"):
        r = await oidc_client.post("/gates/nope/G4", json={"state": "approved"},
                                   headers={**headers, "Origin": origin})
        assert r.status_code == 404, origin
    # no Origin at all (curl, an older browser on a same-origin GET form): allowed
    r = await oidc_client.post("/gates/nope/G4", json={"state": "approved"}, headers=headers)
    assert r.status_code == 404
    # reads are not checked
    assert (await oidc_client.get("/projects", headers={**headers, "Origin": "https://evil.example"})
            ).status_code == 200


async def test_csrf_check_skipped_for_bearer(oidc_client, signer):
    r = await oidc_client.post("/gates/nope/G4", json={"state": "approved"},
                               headers={"Authorization": f"Bearer {signer.token()}",
                                        "Origin": "https://evil.example"})
    assert r.status_code == 404


async def test_cancel_requires_a_principal(oidc_client):
    assert (await oidc_client.post("/jobs/nope/cancel")).status_code == 401
    assert (await oidc_client.post("/jobs/nope/cancel", headers=as_roles("engineer"))).status_code == 404


async def test_system_status_requires_admin(oidc_client):
    assert (await oidc_client.get("/system/status")).status_code == 401
    assert (await oidc_client.get("/system/status", headers=as_roles("engineer"))).status_code == 403
    r = await oidc_client.get("/system/status", headers=as_roles("admin"))
    assert r.status_code == 200 and r.json()["auth_mode"] == "oidc"


# ------------------------------------------------------------------ sign-in flow
def _cookies(response: httpx.Response) -> dict[str, str]:
    return {h.split("=", 1)[0]: h for h in response.headers.get_list("set-cookie")}


async def test_login_redirects_with_pkce_state_and_nonce(oidc_client):
    r = await oidc_client.get("/auth/login", params={"return": "/?step=build"})
    assert r.status_code in (302, 303, 307)
    loc = urlsplit(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == AUTHORIZE_URI
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    assert q["client_id"] == CLIENT and q["response_type"] == "code"
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 43
    assert q["state"] and q["nonce"] and "openid" in q["scope"].split()
    assert q["redirect_uri"] == "https://exiqo.gmf.example/api/auth/callback"
    login = _cookies(r)[session_mod.LOGIN_COOKIE]
    assert "HttpOnly" in login and "Secure" in login and "samesite=lax" in login.lower()


async def _sign_in(client: httpx.AsyncClient, fake: FakeEntra, return_to: str = "/?step=build"):
    r = await client.get("/auth/login", params={"return": return_to})
    q = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    fake.nonce = q["nonce"]
    login_cookie = r.cookies.get(session_mod.LOGIN_COOKIE) or _cookies(r)[session_mod.LOGIN_COOKIE] \
        .split(";")[0].split("=", 1)[1]
    cb = await client.get("/auth/callback", params={"code": "the-code", "state": q["state"]},
                          headers={"Cookie": f"{session_mod.LOGIN_COOKIE}={login_cookie}"})
    return q, cb


async def test_callback_sets_secure_cookie(oidc_client, fake_entra):
    q, cb = await _sign_in(oidc_client, fake_entra)
    assert cb.status_code in (302, 303) and cb.headers["location"] == "/?step=build"
    session = _cookies(cb)[session_mod.SESSION_COOKIE]
    assert "HttpOnly" in session and "Secure" in session
    assert "samesite=lax" in session.lower() and "Path=/" in session
    assert f"Max-Age={8 * 3600}" in session
    # PKCE: the verifier sent to the token endpoint hashes to the challenge sent to Entra
    verifier = fake_entra.token_requests[0]["code_verifier"][0]
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert digest == q["code_challenge"]
    value = session.split(";")[0].split("=", 1)[1]
    me = await oidc_client.get("/auth/me", headers={"Cookie": f"{session_mod.SESSION_COOKIE}={value}"})
    assert me.json() == {"oid": "oid-1", "upn": "ravi@gmf.example", "name": "Ravi", "roles": ["engineer"]}


async def test_callback_refuses_wrong_state_and_nonce(oidc_client, fake_entra):
    r = await oidc_client.get("/auth/login")
    login = _cookies(r)[session_mod.LOGIN_COOKIE].split(";")[0].split("=", 1)[1]
    cookie = {"Cookie": f"{session_mod.LOGIN_COOKIE}={login}"}
    bad = await oidc_client.get("/auth/callback", params={"code": "the-code", "state": "forged"},
                                headers=cookie)
    assert bad.status_code == 400 and session_mod.SESSION_COOKIE not in _cookies(bad)
    q = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    fake_entra.nonce = "replayed"
    bad = await oidc_client.get("/auth/callback", params={"code": "the-code", "state": q["state"]},
                                headers=cookie)
    assert bad.status_code == 400 and session_mod.SESSION_COOKIE not in _cookies(bad)
    no_cookie = await oidc_client.get("/auth/callback", params={"code": "the-code", "state": q["state"]})
    assert no_cookie.status_code == 400


@pytest.mark.parametrize("target,expected", [
    ("/history", "/history"), ("/?step=deploy", "/?step=deploy"), ("//evil.example", "/"),
    ("/\\evil.example", "/"), ("https://evil.example/", "/"), ("", "/"), ("history", "/")])
async def test_return_path_is_same_origin_only(oidc_client, fake_entra, target, expected):
    _, cb = await _sign_in(oidc_client, fake_entra, target)
    assert cb.headers["location"] == expected


async def test_logout_clears_the_cookie(oidc_client):
    r = await oidc_client.post("/auth/logout", headers=as_roles("qa"))
    assert r.status_code == 204
    cleared = _cookies(r)[session_mod.SESSION_COOKIE]
    assert "Max-Age=0" in cleared or "expires=Thu, 01 Jan 1970" in cleared


async def test_dev_mode_has_no_auth_routes(client):
    # the web reads a 404 here as AUTH_MODE=dev and keeps the persona switcher
    assert (await client.get("/auth/me")).status_code == 404


# ------------------------------------------------------------------ run proxy
async def test_run_proxy_uses_the_session_cookie_not_the_bearer(oidc_client):
    # Authorization belongs to the generated service; with no session the proxy refuses
    r = await oidc_client.get("/builds/nope/run/proxy/swagger/index.html",
                              headers={"Authorization": "Bearer service-dev-token"})
    assert r.status_code == 401
    r = await oidc_client.get("/builds/nope/run/proxy/swagger/index.html",
                              headers={**as_roles("qa"), "Authorization": "Bearer service-dev-token"})
    assert r.status_code == 404  # past auth, no such build


def test_proxy_drops_platform_headers():
    out = dict(hosting.request_headers(
        [("Authorization", "Bearer svc"), ("X-User", "ops.sam"), ("X-Agent", "a"),
         ("Cookie", "exiqo_session=abc"), ("Accept", "application/json")],
        host="h", proto="https", prefix="/p"))
    assert out["Authorization"] == "Bearer svc" and out["Accept"] == "application/json"
    assert not {"X-User", "X-Agent", "Cookie"} & set(out)


async def test_proxy_strips_set_cookie_from_the_service():
    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=[("Set-Cookie", "exiqo_session=evil; Path=/"),
                                            ("Content-Type", "text/plain")], content=b"ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        resp = await hosting.forward(http, "GET", "http://svc/x", [], b"")
        names = [k.decode().lower() for k, _ in resp.raw_headers]
    assert "set-cookie" not in names and "content-type" in names


async def test_oidc_memory_viewer_reads_and_cannot_curate(oidc_client):
    owner = as_roles("sme")
    pid = (await oidc_client.post("/projects", json={"name": "m"}, headers=owner)).json()["id"]
    viewer = as_roles("viewer")
    read_routes = ["status", "search?q=x", "graph", "node?uid=a", "revisions", "revisions/1/diff"]
    for route in read_routes:  # neo4j is not configured here: 503/200, never an auth refusal
        assert (await oidc_client.get(f"/memory/{pid}/{route}", headers=viewer)).status_code not in (401, 403)
        assert (await oidc_client.get(f"/memory/{pid}/{route}")).status_code == 401
    body = {"action": "note", "target_uid": "a", "reason": "r", "text": "t"}
    r = await oidc_client.post(f"/memory/{pid}/curations", json=body, headers=viewer)
    assert r.status_code == 403  # the router's write guard answers before require_curator
    assert (await oidc_client.post(f"/memory/{pid}/curations", json=body)).status_code == 401
    assert (await oidc_client.post(f"/memory/{pid}/reingest", headers=viewer)).status_code == 403
    assert (await oidc_client.post(f"/memory/{pid}/reingest", headers=owner)).status_code == 403
