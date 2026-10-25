# Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Settings → Connectors tab and functional GitHub (push builds, open PRs, act as a source), Confluence (publish architecture pages), Jira (issues from findings, open questions and parity differences) and Figma (attach frames as design references) connectors that call GMF-configured hosts only, off by default, with credentials from Key Vault mounted files.

**Architecture:** `api/app/connectors/` holds a two-layer configuration (Helm install ceiling + admin Settings), an allowlisting `httpx` client, an audit helper and a `connector_links` table; each connector is a small class over that client (and the `git` CLI for GitHub pushes and clones). Publishing runs as worker jobs; the connection test runs inline. Tests run every connector against local fake servers that speak the real wire formats, including git smart HTTP.

**Tech Stack:** Python 3.11, FastAPI, httpx, SQLAlchemy async, Alembic, git CLI (`git http-backend` in tests), uvicorn (fake servers), Helm 3 with helm-unittest, Next.js 15, React 19, TanStack Query, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-25-connectors-design.md`

## Global Constraints

- Branch: work and commit on `feat/gmf-deploy` only; never create a branch; never push this repository. Stage only the files each task names; never `git add -A`, stash, reset or checkout.
- No outbound hosts except configured GMF-controlled ones: no connector URL has a default; `api.github.com`, `github.com` (as an API base), `atlassian.net`, `api.figma.com` never appear in `api/` or `engine/` code; every request host must be in the connector's `allowedHosts`; `make egress-audit` passes.
- Off by default: `connectors.<kind>.enabled: false` in Helm; the application toggle cannot exceed the install ceiling; a disabled connector makes zero requests (asserted against the fakes).
- Viewer read-only server-side: Settings writes and tests `admin`; actions by the roles in spec section 8 via `require_roles` (sub-project 6 Task 1; if absent, Task 1 here adds it to `api/app/deps.py` with the same messages).
- Secrets only via `<NAME>_FILE`: `GITHUB_TOKEN`, `ATLASSIAN_TOKEN`, `ATLASSIAN_USER`, `FIGMA_TOKEN` in `SECRET_FILE_FIELDS`; read per call; never in the database, logs, job payloads, audit `detail`, exceptions (redacted) or responses.
- HTTP client: `https` only (`http://127.0.0.1` only with `CONNECTORS_ALLOW_INSECURE_LOOPBACK=true`, rejected by the chart schema); timeouts connect 10 s, read 30 s; ≤ 3 redirects each re-checked; ≤ 3 retries on 429/502/503/504 honouring `Retry-After` (capped at 30 s); responses > 20 MB refused; inline test 15 s overall.
- Git: credentials only via `GIT_CONFIG_COUNT/KEY_0/VALUE_0` (`http.extraHeader`); `GIT_TERMINAL_PROMPT=0`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_LFS_SKIP_SMUDGE=1`, `protocol.allow=never` + `protocol.https.allow=always` (`protocol.http.allow=always` only with the loopback flag), `core.hooksPath=/dev/null`; pushes only to `exiqo/` branches with `--force-with-lease`; commit identity `<BRAND_NAME> Migrate <exiqo-migrate@invalid>` unless `connectors.github.commitEmail` is set.
- Alembic: new revision files named `<next free number>_<slug>.py`, `down_revision` = the single head from `.venv/bin/alembic -c api/alembic.ini heads` at execution time.
- Existing suites green: `make test`, `.venv/bin/ruff check .`, `cd web && npm run typecheck && npm run lint`, `make e2e`, `make chart-check`.

## Review Focus

- A base URL whose host is allowlisted, answering with a 302 to another host (an SSO page or an attacker-controlled redirect): the client raises `HostNotAllowed`, the token is never sent to the second host, the test result says which host was refused. Test in Task 1.
- A token file rotated by the CSI driver while the API runs: the next call uses the new value without a restart. Test in Task 1.
- Re-publishing a Confluence page that someone edited in between (version moved by 2): the publisher refetches, updates on top of the newest version once, and records the new version; a second conflict fails the job with the Confluence message. Test in Task 4.
- A Jira issue linked to a finding was deleted in Jira: re-publish marks the link stale and does not create a new issue unless "create again" is asked. Test in Task 5.
- A pushed build containing a file named like a credential helper or a `.git/` entry from an edited upload: the push copies only the build tree with `.git` removed, and no file outside the temp repository is written. Test in Task 3.

## File Structure

| Path | Responsibility |
| --- | --- |
| `api/app/connectors/__init__.py`, `base.py`, `config.py`, `http.py`, `registry.py`, `audit.py`, `links.py` (create) | framework |
| `api/app/connectors/github.py`, `gitcli.py` (create) | GitHub REST and git transport |
| `api/app/connectors/confluence.py`, `confluence_page.py` (create) | publisher flavors, storage-format renderer |
| `api/app/connectors/jira.py` (create) | issues from findings, questions, parity diffs |
| `api/app/connectors/figma.py` (create) | frames, text, images |
| `api/app/models/connector.py`, `api/alembic/versions/<n>_connectors.py` (create) | `connector_links`, `last_test` storage |
| `api/app/routers/connectors.py`, `api/app/schemas/connector.py` (create) | routes |
| `api/app/jobs/handlers.py`, `api/app/config.py`, `api/app/secrets.py`, `api/app/main.py` (modify) | jobs, settings, secret fields |
| `api/app/services/design.py`, `engine/agents/designer.py`, `engine/agents/prompts/**/designer.md` (modify) | Figma references in the designer input |
| `api/tests/fakes/{__init__,server,github,githttp,confluence,jira,figma}.py`, `api/tests/conftest.py` (create/modify) | fake servers and fixtures |
| `deploy/helm/exiqo/{values.yaml,values.schema.json,values.gmf.example.yaml,templates/configmap.yaml,templates/networkpolicies.yaml,templates/_helpers.tpl,tests/connectors_test.yaml}` (modify/create) | install layer |
| `web/app/settings/page.tsx`, `web/components/settings/{SettingsTabs,ConnectorsTab,ConnectorCard}.tsx`, `web/components/connectors/{PushToGitHub,PublishConfluence,JiraIssues,FigmaReferences,LinkChips}.tsx`, `web/lib/connectors.ts` (create/modify) | UI |
| `web/e2e/connectors.spec.ts`, `web/playwright.config.ts`, `scripts/fake_connectors.py` (create/modify) | e2e with fakes |
| `docs/deploy/GMF-INSTALL.md`, `docs/deploy/RUNBOOK.md`, `scripts/demo_check.py` (modify) | docs and demo step |

---

### Task 1: Preflight, connector framework: configuration, allowlisting client, secrets, audit, links, Settings API

**Files:**
- Create: `api/app/connectors/{__init__,base,config,http,registry,audit,links}.py`, `api/app/models/connector.py`, `api/alembic/versions/<n>_connectors.py`, `api/app/routers/connectors.py`, `api/app/schemas/connector.py`, `api/tests/test_connectors_framework.py`
- Modify: `api/app/config.py` (`connectors: str = ""` JSON, `connector_secret_names: str = ""` JSON, `connectors_allow_insecure_loopback: bool = False`, `github_token`, `atlassian_token`, `atlassian_user`, `figma_token: str = ""`), `api/app/secrets.py`, `api/app/main.py`, `api/app/models/__init__.py`, `api/app/deps.py` (only if `require_roles` is absent)

**Interfaces:**
- Consumes (verify, Step 1): `api.app.secrets.SECRET_FILE_FIELDS`, `read_secret_file`; `api.app.services.settings.load_stored`; `api.app.services.gates.audit` signature; `api.app.deps.require_roles` (sub-project 6) or add it; `AuditLog`.
- Produces in `base.py`: `ConnectorKind = Literal["github","atlassian","figma"]`; `ConnectionTest(BaseModel)`: `ok: bool`, `account: str | None = None`, `site: str | None = None`, `scopes: list[str] = []`, `latency_ms: int = 0`, `message: str = ""`; exceptions `ConnectorDisabled(kind)` (message `"the <Name> connector is not enabled"`), `ConnectorNotConfigured(kind, secret_name)` (`"the <Name> connector has no credential: mount <secret name>"`), `ConnectorError(message: str, status: int | None = None)`, `HostNotAllowed(host)` (`"host <host> is not in this connector's allowed hosts"`); `NAMES = {"github": "GitHub", "atlassian": "Atlassian", "figma": "Figma"}`.
- `config.py`: `InstallConnector(BaseModel)` per kind (spec section 1 fields), `AppConnector(BaseModel)` (toggle, URLs, defaults), `ConnectorConfig` = merged; `load_install(settings) -> dict[kind, InstallConnector]` (from `CONNECTORS` JSON; empty → all disabled); `validate_app(kind, install, patch) -> AppConnector` raising `ValueError` with the spec's 422 texts; `secret(settings_field: str) -> str` reads the `_FILE` path from the environment on every call (falls back to the plain field).
- `http.py`: `ConnectorClient(base_url: str, allowed_hosts: list[str], auth_header: Callable[[], dict[str, str]], *, allow_insecure_loopback: bool = False, transport: httpx.AsyncBaseTransport | None = None)` with `async request(method, path_or_url, **kw) -> httpx.Response`, `async json(method, path, **kw) -> Any`; `redact(text: str, secrets: list[str]) -> str`.
- `registry.py`: `get_connector(kind, *, settings, stored) -> Connector` (Tasks 3–6 register the classes; until then a `_Pending` connector whose `test()` returns `ConnectionTest(ok=False, message="not implemented yet")`); `async connector_for(session, settings, kind) -> Connector`; `connectors_view(settings, stored, *, admin: bool) -> dict`.
- `audit.py`: `audit_connector(session, actor, kind, op, *, project_id=None, process_id=None, target: str = "", outcome: Literal["ok","failed","refused"], detail: dict | None = None) -> None`.
- `links.py` / model `ConnectorLink` (spec section 2 columns, unique constraint); `async upsert_link(session, **fields) -> ConnectorLink`; `async links_for(session, process_id) -> list[ConnectorLink]`. Stored test results: `settings` key `connectors_last_test` `{kind: {result, at, actor}}`.
- Routes: `GET /settings/connectors`, `PUT /settings/connectors` (admin), `POST /connectors/{kind}/test` (admin), `GET /connectors`, `GET /processes/{id}/links`.

- [ ] **Step 1: Verify** `.venv/bin/python -c "from api.app.secrets import SECRET_FILE_FIELDS, read_secret_file; from api.app.services.settings import load_stored; from api.app.services.gates import audit; from api.app.models import AuditLog; print('ok')"` and `.venv/bin/python -c "from api.app.deps import require_roles" || echo 'add require_roles'` — Expected: `ok` (and either nothing or `add require_roles`).
- [ ] **Step 2: Write failing tests** in `api/tests/test_connectors_framework.py`

```python
async def test_off_site_redirect_is_refused_and_token_not_forwarded(two_fake_hosts):             # Review Focus 1
    a, b = two_fake_hosts            # a answers 302 to b
    c = ConnectorClient(a.url, [a.host], lambda: {"Authorization": "Bearer t0k"}, allow_insecure_loopback=True)
    with pytest.raises(HostNotAllowed, match=b.host):
        await c.request("GET", "/x")
    assert b.requests == []

async def test_https_required_without_loopback_flag():
    with pytest.raises(HostNotAllowed):
        await ConnectorClient("http://127.0.0.1:1", ["127.0.0.1"], dict).request("GET", "/")

async def test_retry_after_is_honoured(fake_429_then_200, monkeypatch):
    sleeps = []; monkeypatch.setattr(asyncio, "sleep", lambda s: sleeps.append(s) or _noop())
    r = await ConnectorClient(fake_429_then_200.url, [fake_429_then_200.host], dict, allow_insecure_loopback=True).request("GET", "/")
    assert r.status_code == 200 and sleeps == [2]

def test_secret_is_read_per_call(tmp_path, monkeypatch):                                              # Review Focus 2
    f = tmp_path / "github_token"; f.write_text("one\n"); monkeypatch.setenv("GITHUB_TOKEN_FILE", str(f))
    assert secret("github_token") == "one"
    f.write_text("two\n")
    assert secret("github_token") == "two"

def test_errors_are_redacted():
    assert "t0k" not in redact("401 for Bearer t0k at /user", ["t0k"])

async def test_app_layer_cannot_exceed_install(client, admin):
    r = await client.put("/settings/connectors", json={"github": {"enabled": True}}, headers=admin)
    assert r.status_code == 422 and r.json()["detail"] == "enable it in the install first (connectors.github.enabled)"

async def test_base_url_must_be_allowlisted_https(client_with_github_install, admin):
    r = await client_with_github_install.put("/settings/connectors", json={"github": {"base_url": "https://evil.example.org"}}, headers=admin)
    assert r.status_code == 422 and "not in allowedHosts" in r.json()["detail"]

async def test_view_never_contains_secret_values(client_with_github_install, admin, monkeypatch, tmp_path):
    (tmp_path / "t").write_text("s3cr3t"); monkeypatch.setenv("GITHUB_TOKEN_FILE", str(tmp_path / "t"))
    body = (await client_with_github_install.get("/settings/connectors", headers=admin)).text
    assert "s3cr3t" not in body and '"secrets_present"' in body

async def test_viewer_cannot_test_in_oidc(client_oidc, viewer_token):
    assert (await client_oidc.post("/connectors/github/test", headers=viewer_token)).status_code == 403
```

`two_fake_hosts`, `fake_429_then_200` use the `FakeServer` helper of Task 2; write a minimal version here in `api/tests/fakes/server.py` (uvicorn in a thread on a free port, `requests` list) and extend it in Task 2.

- [ ] **Step 3: Run** `.venv/bin/pytest api/tests/test_connectors_framework.py -v` — Expected: FAIL.
- [ ] **Step 4: Implement** the framework, model, migration, settings fields, secret fields, routes.
- [ ] **Step 5: Run** the file and `.venv/bin/pytest api/tests/test_config_secrets.py api/tests/test_settings_propagation.py -q` — Expected: PASS.
- [ ] **Step 6: Commit** `git add api/app/connectors api/app/models/connector.py api/app/models/__init__.py api/alembic/versions/<n>_connectors.py api/app/routers/connectors.py api/app/schemas/connector.py api/app/config.py api/app/secrets.py api/app/main.py api/app/deps.py api/tests/fakes/server.py api/tests/test_connectors_framework.py && git commit -m "feat(connectors): framework, allowlisting client, secrets, audit and links"`

---

### Task 2: Fake servers for GitHub (REST and git smart HTTP), Confluence, Jira and Figma

**Files:**
- Create: `api/tests/fakes/{__init__,github,githttp,confluence,jira,figma}.py`, `api/tests/test_fakes.py`, `scripts/fake_connectors.py` (starts all four on fixed loopback ports for e2e and manual runs)
- Modify: `api/tests/fakes/server.py`, `api/tests/conftest.py` (fixtures `fake_github`, `fake_confluence`, `fake_jira`, `fake_figma`, `fake_figma_images`, and `connectors_settings(...)` building `Settings` with `CONNECTORS` JSON pointing at them, the loopback flag on, token files in `tmp_path`)

**Interfaces:**
- Produces: `FakeServer(app)` context with `url`, `host` (`127.0.0.1:<port>`), `requests: list[RecordedRequest(method, path, headers, json, body)]`.
- `FakeGitHub`: REST `GET /api/v3/user` (`{"login": "exiqo-bot"}`), `/api/v3/rate_limit`, `GET /api/v3/repos/{o}/{r}` (`default_branch`), branches list and get, `GET/POST/PATCH /api/v3/repos/{o}/{r}/pulls` (numbers from 1, `html_url = <url>/<o>/<r>/pull/<n>`); git smart HTTP at `/{o}/{r}.git/*` via `githttp.wsgi_http_backend(project_root)` (runs `git http-backend` with `GIT_PROJECT_ROOT`, `GIT_HTTP_EXPORT_ALL=1`, `http.receivepack=true`); every request checks `Authorization: Bearer <token>` (401 otherwise, git included); helpers `add_repo(full_name, branch="main", tree: Path | dict[str, str] | None = None)`, `commit(branch, changes: dict[str, str | Callable[[str], str]], repo: str | None = None)` (default: the last added repo), `branch_files(repo, branch) -> dict[str, str]`.
- `FakeConfluence(flavor)`: v1 `/rest/api/content` (POST create, GET by id with `expand=version`, PUT update requiring `version.number == current + 1` else 409), `/rest/api/content/{id}/child/attachment`, v2 `/wiki/api/v2/pages` (POST, GET, PUT with the same version rule), `GET /rest/api/user/current` and `/wiki/rest/api/user/current`; `bump_version(page_id, by)`; Bearer for datacenter, basic for cloud.
- `FakeJira`: `/rest/api/2/myself`, `POST /rest/api/2/issue` (keys `GMF-1`…), `GET`/`PUT /rest/api/2/issue/{key}`, `delete(key)` helper → later 404.
- `FakeFigma`: `/v1/me`, `/v1/files/{key}` (two pages, three frames from a fixture JSON), `/v1/files/{key}/nodes`, `/v1/images/{key}` returning URLs on `fake_figma_images` (a second `FakeServer` serving PNG bytes); `X-Figma-Token` required.

- [ ] **Step 1: Write failing tests** in `api/tests/test_fakes.py`

```python
async def test_fake_github_rejects_missing_token_on_git(fake_github, tmp_path):
    fake_github.add_repo("gmf/svc")
    proc = subprocess.run(["git", "ls-remote", f"{fake_github.url}/gmf/svc.git"], capture_output=True, text=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    assert proc.returncode != 0 and "401" in proc.stderr + proc.stdout or "Authentication failed" in proc.stderr

async def test_fake_github_accepts_bearer_git_push(fake_github, tmp_path, token):
    fake_github.add_repo("gmf/svc"); repo = init_repo(tmp_path, {"a.txt": "1"})
    push(repo, f"{fake_github.url}/gmf/svc.git", "exiqo/x", token)
    assert fake_github.branch_files("gmf/svc", "exiqo/x") == {"a.txt": "1"}

async def test_fake_confluence_version_rule(fake_confluence_dc):
    pid = fake_confluence_dc.create_page("SPACE", "T", "<p/>")
    assert (await put_page(fake_confluence_dc, pid, version=3)).status_code == 409

async def test_fake_figma_images_point_at_the_second_host(fake_figma, fake_figma_images):
    urls = (await get_json(fake_figma, "/v1/images/FILE1?ids=1:2&format=png"))["images"]
    assert urlparse(urls["1:2"]).netloc == fake_figma_images.host
```

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_fakes.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the fakes (response shapes copied from the public API documentation field names the connectors read; nothing more), the fixtures and `scripts/fake_connectors.py` (`--ports github=9711,confluence=9712,jira=9713,figma=9714,figma-images=9715`, writes token files into a temp dir and prints the `CONNECTORS` JSON to use).
- [ ] **Step 4: Run** the file — Expected: PASS.
- [ ] **Step 5: Commit** `git add api/tests/fakes api/tests/conftest.py api/tests/test_fakes.py scripts/fake_connectors.py && git commit -m "test(connectors): local fake GitHub, Confluence, Jira and Figma servers"`

---

### Task 3: GitHub connector: test, branches, clone, push a build, open a pull request

**Files:**
- Create: `api/app/connectors/github.py`, `api/app/connectors/gitcli.py`, `api/tests/test_connector_github.py`
- Modify: `api/app/connectors/registry.py`, `api/app/jobs/handlers.py` (`@job("connector.github.push")`), `api/app/routers/connectors.py` (`POST /builds/{id}/github/push`)

**Interfaces:**
- Consumes: Task 1 framework; `engine.child_env.child_env`; `api.app.services.edits.baseline_manifest` (sub-project 6 Task 6; if absent, write `EXPORTED_FROM.md` only and note it); `get_stack(build.stack).download_excluded` (sub-project 5; else `DOWNLOAD_EXCLUDED`).
- Produces `GitHubConnector` (`kind="github"`): `enabled: bool`; `async test() -> ConnectionTest` (`account = login`, `scopes` from `X-OAuth-Scopes` split); `async head(repo, branch) -> str`; `async list_branches(repo) -> list[str]` (`per_page=100`, `Link` pagination, ≤ 1 000); `async clone(repo, branch, dest: Path, *, depth=1, timeout_s=300) -> str`; `async push_build(build_root: Path, repo: str, branch: str, message: str, *, base_branch: str | None = None) -> str` (refuses `ValueError("pushes go to exiqo/ branches only")` for other branch names; temp repo under `/tmp/exiqo-push-<uuid>` removed in `finally`; copies regular files only, never a `.git` path, never follows symlinks); `async open_pull_request(repo, head, base, title, body) -> dict` (`{"number", "url"}`; reuses an open PR for the head and PATCHes its body).
- `gitcli.py`: `git_env(token: str, *, allow_http_loopback: bool) -> dict[str, str]`; `run_git(args: list[str], cwd: Path, env: dict, timeout_s: int) -> str` (raises `ConnectorError(redacted stderr tail)`).
- Job payload `{build_id, repo, branch, open_pr, base, actor}`; result `{branch, commit, pull_request}`; links `branch` and `pull_request` upserted with `local_ref = build_id`; audits `connector github push` and `connector github pull-request`.
- Route: `POST /builds/{build_id}/github/push` (`require_roles("engineer","admin")`) body `{repo: str (owner/name), branch: str | None, open_pr: bool = True, base: str | None}` → 202 `{"job_id"}`; default branch `exiqo/<process slug>/build-<build id[:8]>`; 409 when disabled or missing credential.

- [ ] **Step 1: Write failing tests** in `api/tests/test_connector_github.py`

```python
async def test_test_connection_reports_login(gh):                 # gh = connector bound to fake_github
    assert (await gh.test()).account == "exiqo-bot"

async def test_push_build_and_open_pr(client, fake_github, finished_build, engineer, wait_terminal):
    fake_github.add_repo("gmf/credit", tree={"README.md": "x"})
    job = (await client.post(f"/builds/{finished_build}/github/push", json={"repo": "gmf/credit"}, headers=engineer)).json()
    out = await wait_terminal(client, job["job_id"])
    files = fake_github.branch_files("gmf/credit", out["branch"])
    assert out["branch"].startswith("exiqo/") and "EXPORTED_FROM.md" in files and "README.md" in files   # on top of base
    assert out["pull_request"]["number"] == 1
    links = (await client.get(f"/processes/{PROC}/links")).json()
    assert {l["object_type"] for l in links} >= {"branch", "pull_request"}

async def test_second_push_reuses_the_open_pr(client, pushed_once, engineer, wait_terminal):
    out = await wait_terminal(client, (await client.post(f"/builds/{pushed_once}/github/push", json={"repo": "gmf/credit"}, headers=engineer)).json()["job_id"])
    assert out["pull_request"]["number"] == 1

async def test_push_refuses_non_exiqo_branch(gh, tmp_path):
    with pytest.raises(ValueError, match="exiqo/ branches only"):
        await gh.push_build(tmp_path, "gmf/credit", "main", "m")

async def test_push_never_copies_git_paths_or_symlinks(gh, fake_github, tmp_path):                   # Review Focus 5
    root = tmp_path / "b"; (root / ".git").mkdir(parents=True); (root / ".git" / "config").write_text("[credential]\nhelper=!evil")
    (root / "ok.txt").write_text("1"); (root / "link").symlink_to("/etc/passwd")
    fake_github.add_repo("gmf/x")
    await gh.push_build(root, "gmf/x", "exiqo/t", "m")
    assert set(fake_github.branch_files("gmf/x", "exiqo/t")) == {"ok.txt"}

async def test_token_never_in_job_log_or_audit(client, fake_github, finished_build, engineer, wait_terminal, token):
    fake_github.add_repo("gmf/credit")
    out = await wait_terminal(client, (await client.post(f"/builds/{finished_build}/github/push", json={"repo": "gmf/credit"}, headers=engineer)).json()["job_id"])
    assert token not in json.dumps(out) and token not in await job_log_text(client) and token not in await audit_text(client)

async def test_disabled_makes_no_request(client_github_disabled, fake_github, finished_build, engineer):
    r = await client_github_disabled.post(f"/builds/{finished_build}/github/push", json={"repo": "gmf/credit"}, headers=engineer)
    assert r.status_code == 409 and fake_github.requests == []

async def test_clone_and_head(gh, fake_github, tmp_path):
    fake_github.add_repo("gmf/estate", tree={"a.process": "<x/>"})
    sha = await gh.clone("gmf/estate", "main", tmp_path / "c")
    assert sha == await gh.head("gmf/estate", "main") and (tmp_path / "c" / "a.process").is_file()
```

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_connector_github.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `gitcli.py`, `github.py`, registration, job and route.
- [ ] **Step 4: Run** the file — Expected: PASS.
- [ ] **Step 5: Commit** `git add api/app/connectors/github.py api/app/connectors/gitcli.py api/app/connectors/registry.py api/app/jobs/handlers.py api/app/routers/connectors.py api/tests/test_connector_github.py && git commit -m "feat(connectors): GitHub push, pull requests, branches and clone"`

---

### Task 4: Confluence: architecture page renderer and publisher (Data Center and Cloud)

**Files:**
- Create: `api/app/connectors/confluence.py`, `api/app/connectors/confluence_page.py`, `api/tests/test_connector_confluence.py`, `api/tests/golden/confluence_dfr.xhtml`
- Modify: `api/app/connectors/registry.py` (the `atlassian` connector gains `.confluence`), `api/app/jobs/handlers.py` (`@job("connector.confluence.publish")`), `api/app/routers/connectors.py`

**Interfaces:**
- Consumes: `DesignRecord` (latest, its `views`), gate rows, latest build, `Project`, `Build.stack` (optional), Figma links (Task 6; empty until then).
- Produces: `render_architecture_page(ctx: PageContext) -> str` (`PageContext(BaseModel)`: `app_name`, `process_name`, `ir_id`, `source_path`, `design: dict`, `design_version: int`, `gates: list[dict]`, `build: dict | None`, `stack: str`, `iteration: int`, `figma: list[dict]`); `page_title(app, process) -> str` = `f"{app} · {process} · Architecture"`; XML escaping via `xml.sax.saxutils.escape` on every value; sections in spec section 4 order with `<h2>` titles exactly: `Overview`, `Target stack and solution layout`, `Decisions`, `Open questions`, `Mapping`, `Contracts`, `Configuration`, `Messaging`, `Resilience`, `Sequence`, `Data model`, `Gate record`, `Figma references`, `Source`.
- `ConfluencePublisher` with `DataCenterAdapter` (`/rest/api/content`, Bearer) and `CloudAdapter` (`/wiki/api/v2/pages`, basic `ATLASSIAN_USER:ATLASSIAN_TOKEN`; attachments via v1 under `/wiki`): `async test() -> ConnectionTest` (`/rest/api/user/current` or `/wiki/rest/api/user/current`, `account = displayName`); `async publish(ctx, *, space_key, parent_page_id, existing: ConnectorLink | None) -> tuple[str, str, int]` (page id, url, version); on 409 refetch the version once and retry; a second 409 → `ConnectorError("Confluence rejected the update twice: <message>")`; attachment `design-<design hash[:12]>.json` uploaded with `X-Atlassian-Token: no-check`.
- Route: `POST /processes/{id}/confluence/publish` (`require_roles("architect","admin")`) → 202; `GET /processes/{id}/confluence` → link or 404. 409 when no design record exists: `"design the process before publishing it"`.

- [ ] **Step 1: Write failing tests** in `api/tests/test_connector_confluence.py`

```python
def test_page_golden(page_ctx_dfr):
    _golden("confluence_dfr.xhtml", render_architecture_page(page_ctx_dfr))

def test_page_escapes_markup_from_any_field(page_ctx_dfr):
    ctx = page_ctx_dfr.model_copy(update={"design": {**page_ctx_dfr.design, "narrative": "<script>x</script> & <ac:macro/>"}})
    html = render_architecture_page(ctx)
    assert "<script>" not in html and "&lt;script&gt;" in html and "<ac:macro" not in html

def test_page_never_contains_config_values(page_ctx_dfr):
    assert "Password=" not in render_architecture_page(page_ctx_dfr)

@pytest.mark.parametrize("flavor", ["datacenter", "cloud"])
async def test_publish_creates_then_updates_in_place(client_atlassian, flavor, designed_process, architect, wait_terminal):
    c, fake = client_atlassian(flavor)
    await wait_terminal(c, (await c.post(f"/processes/{designed_process}/confluence/publish", headers=architect)).json()["job_id"])
    await wait_terminal(c, (await c.post(f"/processes/{designed_process}/confluence/publish", headers=architect)).json()["job_id"])
    assert fake.page_count() == 1 and fake.only_page().version == 2 and fake.only_page().attachments

async def test_concurrent_edit_is_merged_once_then_fails(client_atlassian, published_once, architect, wait_terminal):  # Review Focus 3
    c, fake = client_atlassian("datacenter"); fake.bump_version(fake.only_page().id, by=2)
    ok = await wait_terminal(c, (await c.post(f"/processes/{published_once}/confluence/publish", headers=architect)).json()["job_id"])
    assert ok["version"] == 4
    fake.conflict_always = True
    failed = await wait_terminal(c, (await c.post(f"/processes/{published_once}/confluence/publish", headers=architect)).json()["job_id"], expect="failed")
    assert "rejected the update twice" in failed["error"]
```

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_connector_confluence.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the renderer, adapters, publisher, job and routes; write the golden with `UPDATE_GOLDEN=1` and review it.
- [ ] **Step 4: Run** the file — Expected: PASS.
- [ ] **Step 5: Commit** `git add api/app/connectors/confluence.py api/app/connectors/confluence_page.py api/app/connectors/registry.py api/app/jobs/handlers.py api/app/routers/connectors.py api/tests/test_connector_confluence.py api/tests/golden/confluence_dfr.xhtml && git commit -m "feat(connectors): publish architecture pages to Confluence"`

---

### Task 5: Jira: issues from findings, open questions and parity differences

**Files:**
- Create: `api/app/connectors/jira.py`, `api/tests/test_connector_jira.py`
- Modify: `api/app/connectors/registry.py` (`.jira`), `api/app/jobs/handlers.py` (`@job("connector.jira.issues")`), `api/app/routers/connectors.py`

**Interfaces:**
- Consumes: `Finding` rows (`finding_key`, title, severity, decision, citations), the latest design's `open_questions`, `FieldDiff` rows of the latest build's `differs` scenario runs.
- Produces: `IssueItem(BaseModel)`: `type: Literal["finding","question","parity"]`, `ref: str` (finding key, question index as string, field diff id); `issue_fields(item, process, app, *, project_key, issue_type, labels) -> dict` (summary ≤ 255 characters, wiki-markup description escaped with `wiki_escape(text)` for `{`, `[`, `|`, `*`, `_`, `-`, `+`, `^`, `~`, `!`; back-reference line `ExiQO: project <id>, process <ir id>, <type> <ref>`); `JiraConnector` with `async test()` (`/rest/api/2/myself`), `async create(fields) -> (key, url)`, `async update(key, fields)`, `async exists(key) -> bool`; job: for each item with a live link → `update`; with a stale link → skip and report `stale` unless `create_again=True`; none → `create` and upsert link (`object_type="jira_issue"`, `local_ref=f"{type}:{ref}"`); audits per issue `connector jira create|update`.
- Route: `POST /processes/{id}/jira/issues` (`require_roles("architect","sme","qa","admin")`) body `{items: [IssueItem] (1..50), create_again: bool = False}` → 202; result `{created: [key], updated: [key], stale: [ref]}`; finding items must be decision `fix` or undecided (422 `"finding <key> is decided <decision>: nothing to track"`).

- [ ] **Step 1: Write failing tests** in `api/tests/test_connector_jira.py`

```python
async def test_create_then_republish_updates(client_atlassian, process_with_findings, sme, wait_terminal):
    c, _ = client_atlassian("datacenter"); body = {"items": [{"type": "finding", "ref": "F1"}, {"type": "question", "ref": "0"}]}
    first = await wait_terminal(c, (await c.post(f"/processes/{process_with_findings}/jira/issues", json=body, headers=sme)).json()["job_id"])
    second = await wait_terminal(c, (await c.post(f"/processes/{process_with_findings}/jira/issues", json=body, headers=sme)).json()["job_id"])
    assert first["created"] == ["GMF-1", "GMF-2"] and second["updated"] == ["GMF-1", "GMF-2"] and second["created"] == []

async def test_deleted_issue_is_stale_not_duplicated(client_atlassian, jira_linked_finding, sme, wait_terminal):  # Review Focus 4
    c, fake = client_atlassian("datacenter"); fake.jira.delete("GMF-1")
    out = await wait_terminal(c, (await c.post(f"/processes/{jira_linked_finding}/jira/issues", json={"items": [{"type": "finding", "ref": "F1"}]}, headers=sme)).json()["job_id"])
    assert out["stale"] == ["finding:F1"] and out["created"] == [] and fake.jira.issue_count() == 0

def test_description_is_escaped_and_has_back_reference():
    f = issue_fields(IssueItem(type="finding", ref="F1"), proc_with_text("{code}x[link|http://a]"), app(), project_key="GMF", issue_type="Task", labels=["exiqo"])
    assert "{code}" not in f["fields"]["description"] and f["fields"]["description"].endswith("ExiQO: project P1, process dealerfunding.DealerFundingRequest, finding F1")

async def test_kept_finding_is_refused(client_atlassian, process_with_kept_finding, sme):
    c, _ = client_atlassian("datacenter")
    r = await c.post(f"/processes/{process_with_kept_finding}/jira/issues", json={"items": [{"type": "finding", "ref": "F2"}]}, headers=sme)
    assert r.status_code == 422 and "is decided keep" in r.json()["detail"]
```

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_connector_jira.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the connector, job and route.
- [ ] **Step 4: Run** the file — Expected: PASS.
- [ ] **Step 5: Commit** `git add api/app/connectors/jira.py api/app/connectors/registry.py api/app/jobs/handlers.py api/app/routers/connectors.py api/tests/test_connector_jira.py && git commit -m "feat(connectors): Jira issues from findings, open questions and parity differences"`

---

### Task 6: Figma: frames, text and optional thumbnails as design references

**Files:**
- Create: `api/app/connectors/figma.py`, `api/tests/test_connector_figma.py`
- Modify: `api/app/connectors/registry.py`, `api/app/jobs/handlers.py` (`@job("connector.figma.sync")`), `api/app/routers/connectors.py`, `api/app/services/design.py` (passes `design_references` of the process's figma links to the designer for REST/HTTP starters), `engine/agents/designer.py` (`design_narrative(..., references: list[dict] | None = None)` rendered inside `<source>`), every `engine/agents/prompts/**/designer.md` (`{references}` placeholder), `api/app/connectors/confluence_page.py` (fills `Figma references`)

**Interfaces:**
- Produces: `Frame(BaseModel)`: `node_id`, `name`, `page`, `width`, `height`; `FigmaConnector` with `async test()` (`/v1/me`, `account = handle`), `async frames(file_key) -> list[Frame]` (422 `"file <key> is not in this install's Figma files"` when `file_keys` is set and excludes it), `async frame_text(file_key, node_ids) -> dict[str, str]` (TEXT nodes depth-first, `characters`, joined by newlines, ≤ 4 000 characters, masked), `async render(file_key, node_ids) -> dict[str, Path | None]` (download only when the URL host is in `imageHosts`, ≤ 5 MB, `image/png` content type required).
- Job `connector.figma.sync` payload `{process_id, file_key, node_ids (1..20)}` → upserts links (`object_type="figma_frame"`, `local_ref=f"{file_key}:{node_id}"`, `remote_url = f"{web_url}/design/{file_key}?node-id={node_id}"` with `web_url` from `connectors.figma.webUrl`; empty → `remote_url = ""` and the UI shows the frame name without a link) plus `detail` JSON `{name, page, text, image_path}`.
- Routes: `GET /figma/files/{key}/frames` (`require_roles("architect","admin")`), `POST /processes/{id}/figma/frames` → 202, `GET /processes/{id}/figma/frames`, `GET /figma/frames/{link_id}/image` (signed in; 404 when none).

No Figma host is written in code; `webUrl` is part of the install model (spec section 1, Task 7 values).

- [ ] **Step 1: Write failing tests** in `api/tests/test_connector_figma.py`

```python
async def test_frames_and_text(figma):
    frames = await figma.frames("FILE1")
    assert [f.name for f in frames] == ["Dealer search", "Funding status", "Errors"]
    assert "Dealer ID" in (await figma.frame_text("FILE1", [frames[0].node_id]))[frames[0].node_id]

async def test_thumbnails_off_without_image_hosts(figma_without_image_hosts, fake_figma_images):
    out = await figma_without_image_hosts.render("FILE1", ["1:2"])
    assert out == {"1:2": None} and fake_figma_images.requests == []

async def test_attach_feeds_the_designer(client_figma, rest_process, architect, recording_provider, wait_terminal):
    await wait_terminal(client_figma, (await client_figma.post(f"/processes/{rest_process}/figma/frames", json={"file_key": "FILE1", "node_ids": ["1:2"]}, headers=architect)).json()["job_id"])
    await run_design(client_figma, rest_process)
    prompt = recording_provider.prompt_for("designer")
    assert "Dealer search" in prompt[prompt.index("<source>"):]

async def test_file_key_outside_allowed_list_is_refused(client_figma_restricted, architect):
    r = await client_figma_restricted.get("/figma/files/OTHER/frames", headers=architect)
    assert r.status_code == 422
```

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_connector_figma.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the connector, job, routes, designer references (all prompt packs together, keeping the pack parity test green) and the Confluence section.
- [ ] **Step 4: Run** the file plus `.venv/bin/pytest api/tests/test_connector_confluence.py engine/tests/test_agents.py -q` (and `engine/tests/test_prompt_packs.py` when present) — Expected: PASS (re-run the Confluence golden update only for the `Figma references` section, reviewed).
- [ ] **Step 5: Commit** `git add api/app/connectors/figma.py api/app/connectors/registry.py api/app/connectors/confluence_page.py api/app/jobs/handlers.py api/app/routers/connectors.py api/app/services/design.py engine/agents/designer.py engine/agents/prompts api/tests/test_connector_figma.py api/tests/golden/confluence_dfr.xhtml && git commit -m "feat(connectors): Figma frames as design references"`

---

### Task 7: Install layer: Helm values, schema, secrets, NetworkPolicy and egress

**Files:**
- Create: `deploy/helm/exiqo/tests/connectors_test.yaml`
- Modify: `deploy/helm/exiqo/values.yaml`, `deploy/helm/exiqo/values.schema.json`, `deploy/helm/exiqo/values.gmf.example.yaml` (commented example: GHES at `https://github.gmf.example.com`, Data Center at `https://confluence.gmf.example.com` and `https://jira.gmf.example.com`; RFC 2606 names, ignored by the egress audit), `deploy/helm/exiqo/templates/_helpers.tpl` (`exiqo.secretTable` rows `githubToken`, `atlassianToken`, `atlassianUser`, `figmaToken` with `when: github|atlassian|figma`; `exiqo.platformSecrets` handles the new `when` values), `deploy/helm/exiqo/templates/configmap.yaml` (`CONNECTORS`, `CONNECTOR_SECRET_NAMES`, `EGRESS_ALLOWLIST` merge), `deploy/helm/exiqo/templates/networkpolicies.yaml` (`exiqo-connectors-egress`), `api/tests/test_no_saas_hosts.py` (create)

**Interfaces:**
- Produces values exactly as spec section 1 plus `connectors.github.commitEmail: ""` and `connectors.figma.webUrl: ""`.
- `CONNECTORS` ConfigMap value: JSON `{kind: {enabled, flavor?, baseUrl/apiUrl/confluenceUrl/jiraUrl/webUrl, allowedHosts, imageHosts?, commitEmail?}}` for every kind (disabled ones `{"enabled": false}`); `CONNECTOR_SECRET_NAMES`: JSON `{kind: {"token": <kv name>, "user": <kv name>?}}`.
- NetworkPolicy `exiqo-connectors-egress` (rendered only when at least one connector is enabled): `podSelector` api and worker; egress to each enabled connector's `cidrs` as `ipBlock` on TCP 443.
- Schema: URL fields `pattern: "^https://"`; enabled ⇒ token secret name `minLength: 1`; `atlassian.flavor = cloud` and enabled ⇒ `atlassianUser`; enabled ⇒ `cidrs` non-empty or `egress.allowCidrs` non-empty; no property allows the loopback flag.

- [ ] **Step 1: Write failing helm-unittest cases** in `deploy/helm/exiqo/tests/connectors_test.yaml` and the grep test

```yaml
- it: renders no connector policy or secret when all are off
  template: templates/networkpolicies.yaml
  asserts: [{notContains: {path: metadata.name, content: exiqo-connectors-egress}, documentSelector: {path: kind, value: NetworkPolicy}}]
- it: GitHub enabled mounts the token file and opens 443 to its CIDR
  set: {connectors.github: {enabled: true, baseUrl: "https://github.gmf.example.com", allowedHosts: [github.gmf.example.com], cidrs: [10.20.0.0/24]}, keyVault.secrets.githubToken: exiqo-github-token}
  asserts:
    - template: templates/api-deployment.yaml
      contains: {path: spec.template.spec.containers[0].env, content: {name: GITHUB_TOKEN_FILE, value: /mnt/secrets/github_token}}
    - template: templates/networkpolicies.yaml
      contains: {path: spec.egress[0].to, content: {ipBlock: {cidr: 10.20.0.0/24}}}
- it: refuses http URLs
  set: {connectors.github: {enabled: true, baseUrl: "http://github.gmf.example.com"}}
  asserts: [{failedTemplate: {}}]
- it: refuses Atlassian cloud without the user secret
  set: {connectors.atlassian: {enabled: true, flavor: cloud, confluenceUrl: "https://gmf.example.net/wiki", allowedHosts: [gmf.example.net], cidrs: [10.0.0.0/24]}, keyVault.secrets.atlassianToken: t}
  asserts: [{failedTemplate: {}}]
```

```python
# api/tests/test_no_saas_hosts.py
def test_no_public_saas_host_in_code():
    hits = grep_strings(["api", "engine"], [r"api\.github\.com", r"atlassian\.net", r"api\.figma\.com", r"www\.figma\.com"])
    assert hits == []
```

- [ ] **Step 2: Run** `make chart-check && .venv/bin/pytest api/tests/test_no_saas_hosts.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** values, schema, helpers, ConfigMap, NetworkPolicy, example values. (The Figma web URL in Task 6 comes from `connectors.figma.webUrl`; the grep test enforces it.)
- [ ] **Step 4: Run** `make chart-check && .venv/bin/pytest api/tests/test_no_saas_hosts.py -v && make egress-audit` — Expected: PASS, `egress-audit: OK`.
- [ ] **Step 5: Commit** `git add deploy/helm/exiqo api/tests/test_no_saas_hosts.py && git commit -m "feat(deploy): connector values, secrets, NetworkPolicy and schema"`

---

### Task 8: Web: Settings → Connectors tab

**Files:**
- Create: `web/components/settings/SettingsTabs.tsx`, `web/components/settings/ConnectorsTab.tsx`, `web/components/settings/ConnectorCard.tsx`, `web/lib/connectors.ts`, `web/e2e/connectors.spec.ts`
- Modify: `web/app/settings/page.tsx` (tabs `General` and `Connectors`, `?tab=connectors` deep link), `web/lib/api.ts`, `web/lib/buildTypes.ts`, `web/playwright.config.ts` (starts `scripts/fake_connectors.py` and passes its `CONNECTORS` JSON, the loopback flag and token files to the e2e API), `Makefile` (`e2e` depends on nothing new; the config's `webServer` list starts the fakes)

**Interfaces:**
- Consumes: `GET/PUT /settings/connectors`, `POST /connectors/{kind}/test`, `GET /connectors`.
- Produces: `useConnectorSettings()`, `useConnectors()` (key `["connectors"]`), `testConnector(kind)`; test ids `settings-tab-general`, `settings-tab-connectors`, `connector-<kind>`, `connector-<kind>-install`, `connector-<kind>-toggle`, `connector-<kind>-url`, `connector-<kind>-secret-<name>`, `connector-<kind>-test`, `connector-<kind>-result`, `connector-<kind>-save`.

- [ ] **Step 1: Write the failing Playwright spec** `web/e2e/connectors.spec.ts` (settings part)

```ts
test("admin enables GitHub against the fake and tests it", async ({ page }) => {
  await actAs(page, "ops.sam"); await page.goto("/settings?tab=connectors");
  await expect(page.getByTestId("connector-github-install")).toContainText("Enabled by the install");
  await page.getByTestId("connector-github-toggle").check();
  await page.getByTestId("connector-github-save").click();
  await page.getByTestId("connector-github-test").click();
  await expect(page.getByTestId("connector-github-result")).toContainText("exiqo-bot");
  await expect(page.getByTestId("connector-github-secret-token")).toContainText("present");
});

test("figma is off in this install and cannot be toggled", async ({ page }) => {
  await actAs(page, "ops.sam"); await page.goto("/settings?tab=connectors");
  await expect(page.getByTestId("connector-figma-toggle")).toBeDisabled();
});

test("non-admin sees read-only cards", async ({ page }) => {
  await actAs(page, "architect.lee"); await page.goto("/settings?tab=connectors");
  await expect(page.getByTestId("connector-github-test")).toBeDisabled();
});
```

The e2e install enables GitHub and Atlassian (datacenter) against the fakes and leaves Figma off, so both states are exercised.

- [ ] **Step 2: Run** `cd web && npx playwright test e2e/connectors.spec.ts` — Expected: FAIL.
- [ ] **Step 3: Implement** the tabs, cards, client and the Playwright web servers.
- [ ] **Step 4: Run** `cd web && npm run typecheck && npm run lint && npx playwright test e2e/connectors.spec.ts e2e/standards-identity.spec.ts` — Expected: PASS (the second spec guards the existing Settings panels).
- [ ] **Step 5: Commit** `git add web/app/settings/page.tsx web/components/settings/SettingsTabs.tsx web/components/settings/ConnectorsTab.tsx web/components/settings/ConnectorCard.tsx web/lib/connectors.ts web/lib/api.ts web/lib/buildTypes.ts web/playwright.config.ts web/e2e/connectors.spec.ts && git commit -m "feat(web): Settings connectors tab with connection tests"`

---

### Task 9: Web: publish actions where the work is

**Files:**
- Create: `web/components/connectors/{PushToGitHub,PublishConfluence,JiraIssues,FigmaReferences,LinkChips}.tsx`
- Modify: `web/components/tabs/build/BuildTab.tsx` (Push to GitHub, links), `web/components/tabs/design/DesignTab.tsx` (Publish to Confluence, Figma references, Jira from open questions), `web/components/tabs/validate/*` (Jira from findings: the findings table gains a selection column when Jira is enabled), `web/components/tabs/verify/ParityTable.tsx` (Jira from differences), `web/lib/api.ts`, `web/lib/types.ts`, `web/e2e/connectors.spec.ts` (actions part)

**Interfaces:**
- Consumes: Task 3–6 routes, `GET /processes/{id}/links`, `useConnectors()` (Task 8), `usePermission`.
- Produces: test ids `push-github`, `push-github-dialog`, `push-github-repo`, `push-github-submit`, `link-pull_request`, `publish-confluence`, `link-confluence_page`, `jira-create`, `jira-select-<type>-<ref>`, `link-jira_issue-<key>`, `figma-browse`, `figma-frame-<node>`, `figma-attach`; each action hidden when its connector is disabled, disabled with the role reason otherwise.

- [ ] **Step 1: Extend the failing Playwright spec**

```ts
test("engineer pushes a build and sees the PR link", async ({ page }) => {
  await actAs(page, "engineer.ravi"); await openFinishedBuild(page);
  await page.getByTestId("push-github").click();
  await page.getByTestId("push-github-repo").fill("gmf/credit");
  await page.getByTestId("push-github-submit").click();
  await expect(page.getByTestId("link-pull_request")).toHaveAttribute("href", /\/gmf\/credit\/pull\/1$/);
});

test("architect publishes to Confluence", async ({ page }) => {
  await actAs(page, "architect.lee"); await openDesignedProcess(page);
  await page.getByTestId("publish-confluence").click();
  await expect(page.getByTestId("link-confluence_page")).toBeVisible();
});

test("viewer sees links but no actions", async ({ page }) => {
  await actAs(page, "viewer.val"); await openFinishedBuild(page);
  await expect(page.getByTestId("push-github")).toBeDisabled();
});
```

The fake GitHub's `add_repo("gmf/credit")` is done by `scripts/fake_connectors.py --seed-e2e`.

- [ ] **Step 2: Run** `cd web && npx playwright test e2e/connectors.spec.ts` — Expected: FAIL on the new cases.
- [ ] **Step 3: Implement** the components and tab wiring.
- [ ] **Step 4: Run** `cd web && npm run typecheck && npm run lint && npx playwright test e2e/connectors.spec.ts e2e/validate-design.spec.ts e2e/build-verify-deploy.spec.ts` — Expected: PASS.
- [ ] **Step 5: Commit** `git add web/components/connectors web/components/tabs/build/BuildTab.tsx web/components/tabs/design/DesignTab.tsx web/components/tabs/validate web/components/tabs/verify/ParityTable.tsx web/lib/api.ts web/lib/types.ts web/e2e/connectors.spec.ts scripts/fake_connectors.py && git commit -m "feat(web): push to GitHub, publish to Confluence, Jira issues and Figma references"`

---

### Task 10: Guides, demo check and final verification

**Files:**
- Modify: `docs/deploy/GMF-INSTALL.md` (Connectors section: what each connector does, values, Key Vault secrets and the token scopes — GitHub `contents:write`, `pull_requests:write`, `metadata:read` for fine-grained tokens; Confluence space permission "add page" on the space; Jira "create issues" in the project; Figma `file_content:read` — firewall FQDNs and CIDRs, rotation), `docs/deploy/RUNBOOK.md` (test fails: 401, host not allowed, redirect to SSO, Confluence 409, Jira 404 stale links), `scripts/demo_check.py` (`connectors` step: skipped with `connectors: SKIP (none enabled)` when `GET /connectors` shows none; otherwise runs each enabled connector's test and, with `--publish`, one push, one page, one issue), `scripts/tests/test_demo_check.py`, `README.md` (local fakes: `python scripts/fake_connectors.py`)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the failing test** in `scripts/tests/test_demo_check.py`

```python
def test_connectors_step_skips_when_none_enabled(fake_api):
    assert "connectors: SKIP (none enabled)" in run_demo_check(["--base-url", fake_api.url])

def test_connectors_step_tests_enabled_ones(fake_api_with_github):
    out = run_demo_check(["--base-url", fake_api_with_github.url])
    assert "connectors: github test ok (exiqo-bot)" in out
```

- [ ] **Step 2: Run** `.venv/bin/pytest scripts/tests/test_demo_check.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the step and the documentation.
- [ ] **Step 4: Final verification.** Run: `make test && .venv/bin/ruff check . && (cd web && npm run typecheck && npm run lint) && make e2e && make chart-check && make egress-audit && make demo-check` — Expected: all green; `egress-audit: OK`; demo check prints `connectors: SKIP (none enabled)` on the default install.
- [ ] **Step 5: Commit** `git add docs/deploy/GMF-INSTALL.md docs/deploy/RUNBOOK.md scripts/demo_check.py scripts/tests/test_demo_check.py README.md && git commit -m "docs(connectors): install guide, runbook and demo check step"`
