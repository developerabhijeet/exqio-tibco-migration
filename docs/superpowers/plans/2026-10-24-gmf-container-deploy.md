# GMF Containers, AKS Install, Help & Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package ExiQO Migrate as hardened containers with a Helm chart for AKS, so GMF installs and runs it in its own tenant with Key Vault secrets, Workload Identity for the Deploy step, Entra ID sign-in, GMF-only egress, and an in-app Help & Support guide.

**Architecture:** One `exiqo-platform` image runs as `api` (stateless, 2+ replicas) or `worker` (claims jobs from Postgres with `SKIP LOCKED`, hosts the generated services Verify starts), plus an `exiqo-web` image. Cross-process state that lives in API memory today (job queue, subscribers, the `scan`/`analyse`/`scenario` locks, the running-services registry) moves to Postgres (rows, `LISTEN/NOTIFY`, advisory locks) and to a worker-internal listener the API proxies to. The web is served same-origin with the API under `/api`, and the API is the OIDC backend-for-frontend for Entra ID.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async + asyncpg, Alembic, PyJWT[crypto], azure-identity, azure-keyvault-secrets, neo4j (driver), Next.js 15 standalone, Helm 3, kubeconform, hadolint, Trivy, syft, cosign, Terraform (azurerm), kind.

**Spec:** `docs/superpowers/specs/2026-10-24-gmf-container-deploy-design.md`

## Global Constraints

- Images: UID 10001, `readOnlyRootFilesystem: true`, writable only `/tmp` (emptyDir) and `DATA_DIR`; base images from `BASE_IMAGE_*` build args pinned by digest; no package manager in final stage.
- No secret value in a pod spec, Helm values, image, log, job payload or database, except `DEPLOY_CREDENTIAL_MODE=local` (local development only; the chart schema rejects it).
- Secrets read from `<NAME>_FILE` when set, else `<NAME>`.
- Deploy step default auth: Workload Identity, Terraform with `ARM_USE_OIDC=true`; no client secret stored.
- `AUTH_MODE` is `dev` or `oidc`; the chart schema rejects `dev` except via the CI-only values overlay `deploy/helm/exiqo/ci/values.kind.yaml`.
- Egress only to: Postgres, Neo4j, Key Vault, Entra ID, GMF Azure OpenAI, Azure Resource Manager, GMF package mirrors. Claude off unless `providers.claude.enabled=true`.
- No R Systems name, URL, email or logo in the installed UI; `BRAND_NAME` default `ExiQO`; `SUPPORT_CONTACT` from values; seed source label `rsi-seed` becomes `seed`.
- Telemetry off: `NEXT_TELEMETRY_DISABLED=1`, Langfuse off unless pointed at a GMF host.
- Job defaults: heartbeat every 10 s, `JOB_STALE_SECONDS=90`, `JOB_MAX_ATTEMPTS=2`, `terminationGracePeriodSeconds: 600` for workers.
- Worker internal listener: port `9100`, cluster-internal, reachable only from api pods, bearer `INTERNAL_TOKEN`.
- Local development keeps working: `EXIQO_ROLE=all` (default), `AUTH_MODE=dev` (default), `make api`, `make web`, `make test`, `make e2e` unchanged in behaviour.
- Deviation from spec wording: `/readiness` already names the SME readiness screen, so infrastructure checks are `/healthz` (liveness, no dependencies), `/readyz` (dependency checks) and `/system/status` (About panel). Every place the spec says "`/readiness` reports X" means `/readyz` and `/system/status`.

## Review Focus

- A worker pod is killed mid-build while a browser streams the job: the stream must end with an `error` event "worker lost" within `JOB_STALE_SECONDS` + 30 s, never hang. Test in Task 3.
- The browser's session cookie expires during a long build: SSE and fetches get 401, and the web must send the user to sign in again, not show a silent spinner. Test in Task 7.
- A Key Vault service-principal secret is rotated between two plans: the second plan must use the new value (read at plan time, never cached). Test in Task 8.
- A 2 GB estate zip upload and a 20-minute SSE stream through the ingress: the ingress must allow the body size and not buffer or time out SSE. Test (rendered annotations) in Task 13.
- Service started on worker A, Verify job claimed while worker A is busy: the verify job must wait for worker A (affinity), not run on worker B and fail with "service not running". Test in Task 5.

## File Structure

| Path | Responsibility |
| --- | --- |
| `api/app/config.py` (modify) | new settings, `*_FILE` secret loading |
| `api/app/secrets.py` (create) | `read_secret_file`, `FileSecretsSource` for pydantic-settings |
| `api/app/jobs/registry.py` (create) | `@job` handler registry and `WorkerServices` |
| `api/app/jobs/runner.py` (modify) | submit stores rows only; `JobWorker` claims, heartbeats, recovers, requeues; NOTIFY |
| `api/app/jobs/listener.py` (create) | one `LISTEN job_events` connection per API process, fan-out to subscribers |
| `api/app/jobs/locks.py` (create) | Postgres advisory locks replacing `scan_lock`, `analyse_lock`, `scenario_lock` |
| `api/app/jobs/handlers.py` (create) | the registered handlers: thin adapters over the existing `*_job` factories |
| `api/app/worker.py` (create) | worker entrypoint (`python -m api.app.worker`), internal listener on 9100 |
| `api/app/services/hosting.py` (create) | the per-worker registry of running generated services and the internal API |
| `api/app/routers/run_proxy.py` (create) | `/builds/{id}/run/proxy/{path}` and run status/logs/stop through the owning worker |
| `api/app/auth/` (create: `oidc.py`, `jwt.py`, `session.py`, `roles.py`, `router.py`) | Entra OIDC BFF, JWT validation, session cookie, group-to-role mapping |
| `api/app/deps.py` (modify) | `require_user` returns a `Principal`; gate-owner enforcement in `oidc` mode |
| `api/app/services/deploy_creds.py` (create) | Workload Identity / Key Vault reference / local credentials for Terraform |
| `api/app/routers/system.py` (create) | `/healthz`, `/readyz`, `/system/status` |
| `api/alembic/versions/0003_*.py`, `0004_*.py` (create) | job columns, build run host, Azure connection auth mode, seed label |
| `web/lib/api.ts`, `web/lib/auth.ts` (modify/create) | same-origin `/api`, cookie credentials, 401 handling |
| `web/app/help/` (create) | Help & Support screen |
| `web/lib/brand.ts` (create) | brand name and logo from runtime config |
| `deploy/docker/*` | Dockerfiles, `.dockerignore`, `compose.pilot.yml` |
| `deploy/helm/exiqo/**` | the chart |
| `deploy/azure/**` | optional prerequisite Terraform |
| `deploy/pipelines/**` | release pipelines |
| `deploy/egress-allowlist.txt`, `scripts/egress_audit.py` | egress audit |
| `docs/deploy/*.md` | install guide, Deploy-step secrets, runbook |

---

### Task 1: Settings and file-mounted secrets

**Files:**
- Create: `api/app/secrets.py`
- Modify: `api/app/config.py`, `.env.example`, `README.md` (environment table)
- Test: `api/tests/test_config_secrets.py`

**Interfaces:**
- Produces: `Settings` fields `exiqo_role: Literal["api","worker","all"]="all"`, `auth_mode: Literal["dev","oidc"]="dev"`, `deploy_credential_mode: Literal["workload_identity","local"]="local"`, `api_base_path: str=""`, `internal_token: str=""`, `internal_port: int=9100`, `worker_host: str=""` (pod IP; empty means `127.0.0.1`), `job_stale_seconds: int=90`, `job_heartbeat_seconds: int=10`, `job_max_attempts: int=2`, `neo4j_uri: str=""`, `neo4j_user: str="neo4j"`, `neo4j_password: str=""`, `brand_name: str="ExiQO"`, `support_contact: str=""`, `azure_openai_auth: Literal["key","managed_identity"]="key"`, `oidc_tenant_id: str=""`, `oidc_client_id: str=""`, `oidc_client_secret: str=""`, `oidc_redirect_uri: str=""`, `session_secret: str=""`, `role_groups: dict[str, list[str]]={}` (JSON env), `deploy_environments: list[DeployEnvironment]=[]` (JSON env), `image_digests: dict[str,str]={}`, `egress_allowlist: list[str]=[]`.
- Produces: `DeployEnvironment(BaseModel)`: `name: str`, `tenant: str`, `subscription: str`, `client_id: str`, `auth: Literal["workload_identity","keyvault_sp"]`, `key_vault_uri: str=""`, `secret_name: str=""`.
- Produces: `read_secret_file(path: str) -> str` (strips one trailing newline) in `api/app/secrets.py`.
- Secret fields with `_FILE` support: `database_url`, `test_database_url`, `app_secret`, `neo4j_password`, `azure_openai_api_key`, `claude_code_oauth_token`, `oidc_client_secret`, `session_secret`, `internal_token`, `langfuse_secret_key`.

- [ ] **Step 1: Write the failing tests**

```python
def test_file_secret_wins_over_env(tmp_path, monkeypatch):
    f = tmp_path / "db"; f.write_text("postgresql+asyncpg://u:p@h/db\n")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://env/db")
    monkeypatch.setenv("DATABASE_URL_FILE", str(f))
    assert Settings(_env_file=None).database_url == "postgresql+asyncpg://u:p@h/db"

def test_missing_secret_file_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_SECRET_FILE", str(tmp_path / "absent"))
    with pytest.raises(ValueError, match="APP_SECRET_FILE"):
        Settings(_env_file=None)

def test_deploy_environments_parse(monkeypatch):
    monkeypatch.setenv("DEPLOY_ENVIRONMENTS", '[{"name":"dev","tenant":"t","subscription":"s","client_id":"c","auth":"workload_identity"}]')
    assert Settings(_env_file=None).deploy_environments[0].auth == "workload_identity"

def test_role_groups_parse(monkeypatch):
    monkeypatch.setenv("ROLE_GROUPS", '{"engineer":["11111111-1111-1111-1111-111111111111"]}')
    assert Settings(_env_file=None).role_groups["engineer"] == ["11111111-1111-1111-1111-111111111111"]

def test_defaults_keep_local_dev():
    s = Settings(_env_file=None)
    assert (s.exiqo_role, s.auth_mode, s.deploy_credential_mode) == ("all", "dev", "local")
```

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_config_secrets.py -v` — expect FAIL (fields and `_FILE` support missing).
- [ ] **Step 3: Implement** a custom pydantic-settings source in `api/app/secrets.py` placed first in `settings_customise_sources`, reading `<FIELD>_FILE` for the listed fields; raise `ValueError("<FIELD>_FILE points at <path>, which does not exist")`. Add the fields to `Settings`.
- [ ] **Step 4: Run** the test file — expect PASS; run `.venv/bin/pytest api/tests -q` — expect no regressions.
- [ ] **Step 5: Document** the new variables in `.env.example` and the README environment table (one row each, same style as existing rows).
- [ ] **Step 6: Commit** `feat(api): file-mounted secrets and install settings`.

---

### Task 2: Named job handlers and Postgres locks

**Files:**
- Create: `api/app/jobs/registry.py`, `api/app/jobs/handlers.py`, `api/app/jobs/locks.py`
- Modify: `api/app/jobs/runner.py` (`submit` signature), every caller: `api/app/routers/{analysis,domains,build,verify,infra,design,settings,projects}.py`, `api/app/main.py` (remove `scan_lock`, `analyse_lock`, `scenario_lock` from `app.state`), services that take `scenario_lock`
- Test: `api/tests/test_job_registry.py`, `api/tests/test_locks.py`, existing `api/tests/test_jobs.py`

**Interfaces:**
- Produces: `WorkerServices` dataclass: `settings: Settings`, `knowledge: Knowledge`, `sessionmaker`, `services: dict[str, RunInfo]` (today's `app.state.services`, shared by reference with the routers in role `all`; Task 5 replaces it with `hosting: Hosting`), `fernet: Fernet`.
- Produces: `job(kind: str) -> decorator` registering `Handler = Callable[[JobContext, WorkerServices], Awaitable[dict[str, Any]]]`; `handler_for(kind: str) -> Handler` raising `KeyError(f"no handler for job kind {kind!r}")`.
- Produces: `JobRunner.submit(kind: str, payload: dict[str, Any], *, project_id: str | None = None, process_id: str | None = None, affinity: str | None = None) -> str` — no `run` argument.
- Produces: `advisory_lock(sessionmaker, name: str) -> AsyncContextManager[None]` in `locks.py`, using `pg_advisory_lock(hashtext(name))` on a dedicated connection held for the block; lock names `"scan"`, `"analyse:<process_id>"`, `"scenario"`.
- Handlers: one per kind `scan, discover, analyse, design, build, verify, plan, eval, service_start` (the last added in Task 5), each building the existing closure from the payload, e.g. `@job("build") async def build(ctx, w): return await svc.build_job(p["build_id"], w.settings, w.knowledge)(ctx)`.
- The `plan` payload carries `build_id`, `plan_id`, `environment` only; credentials are resolved in the handler (Task 8; until then the handler decrypts the stored secret itself via `w.fernet`).

- [ ] **Step 1: Write the failing tests**

```python
async def test_submit_stores_payload_only(app):
    runner = app.state.runner
    job_id = await runner.submit("noop-test", {"x": 1})
    async with app.state.sessionmaker() as s:
        job = await s.get(Job, job_id)
    assert job.payload == {"x": 1} and job.status in ("queued", "running", "done")

async def test_unknown_kind_fails_job_with_reason(app):
    job_id = await app.state.runner.submit("does-not-exist", {})
    job = await wait_terminal(app, job_id)
    assert job.status == "failed" and "no handler for job kind 'does-not-exist'" in job.error

async def test_advisory_lock_excludes_second_holder(app):
    order = []
    async def hold(tag, delay):
        async with advisory_lock(app.state.sessionmaker, "scenario"):
            order.append(f"{tag}-in"); await asyncio.sleep(delay); order.append(f"{tag}-out")
    await asyncio.gather(hold("a", 0.3), hold("b", 0))
    assert order in (["a-in","a-out","b-in","b-out"], ["b-in","b-out","a-in","a-out"])
```

Register `noop-test` in a test-only fixture (`registry.register("noop-test", handler)` with teardown).

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_job_registry.py api/tests/test_locks.py -v` — expect FAIL.
- [ ] **Step 3: Implement** registry, locks, handlers; change `submit`; convert the eight call sites; replace `app.state.scan_lock` (the check-and-submit in `projects.py` uses `advisory_lock(sm, "scan")`), `analyse_lock`, and `scenario_lock` (passed through as a callable `lambda: advisory_lock(sm, "scenario")` where services currently take an `asyncio.Lock`). In-process execution still works for now: `submit` enqueues the id to the existing in-process queue, which looks up the handler by kind.
- [ ] **Step 4: Run** `.venv/bin/pytest api/tests -q` and `make e2e` — expect PASS (behaviour unchanged).
- [ ] **Step 5: Commit** `refactor(api): named job handlers and Postgres advisory locks`.

---

### Task 3: Claiming workers, heartbeat, recovery, cancel, NOTIFY

**Files:**
- Create: `api/alembic/versions/0003_job_ownership.py`, `api/app/jobs/listener.py`
- Modify: `api/app/models/job.py`, `api/app/jobs/runner.py`, `api/app/routers/jobs.py` (subscribe via listener), `api/app/main.py`
- Test: `api/tests/test_job_worker.py`

**Interfaces:**
- Migration adds to `jobs`: `owner TEXT NULL`, `heartbeat_at TIMESTAMPTZ NULL`, `cancel_requested BOOLEAN NOT NULL DEFAULT false`, `attempts INT NOT NULL DEFAULT 0`, `affinity TEXT NULL`; index on `(status, created_at)`.
- Produces: `JobWorker(sessionmaker, services: WorkerServices, *, name: str, slots: int, heartbeat_s: int, stale_s: int, max_attempts: int)` with `start()`, `drain(timeout_s: float) -> None` (stop claiming, wait, requeue unfinished), `stop()`.
- Claim SQL (one statement):

```sql
UPDATE jobs SET status='running', owner=:me, heartbeat_at=now(), started_at=coalesce(started_at, now()),
       attempts=attempts+1
WHERE id = (SELECT id FROM jobs WHERE status='queued' AND (affinity IS NULL OR affinity=:me)
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id, kind, payload
```

- Recovery (`JobWorker.reap()`, run at start and every 30 s): `running` jobs with `heartbeat_at < now() - stale_s` become `failed`, `error='worker lost'`, plus a terminal `error` event. Queued jobs whose `affinity` names a worker with no running heartbeat for `stale_s` also fail with `'worker lost'`. `RESTART_REASON` is retired.
- Cancel: `POST /jobs/{id}/cancel` sets `cancel_requested`; the heartbeat loop cancels the job's task when it reads true; outcome `failed`, `error='cancelled'` (existing cleanup paths run on `CancelledError`).
- Drain: unfinished jobs get `status='queued', owner=NULL` when `attempts < max_attempts`, else `failed` with `'worker stopped'`.
- Events: `JobContext.emit` runs `SELECT pg_notify('job_events', :job_id || ':' || :seq)` in the same transaction as the insert. `JobListener(dsn)` holds one asyncpg connection with `add_listener('job_events', ...)`, and `subscribe/unsubscribe` move from `JobRunner` to it. The 500 ms database poll in `stream_events` stays.
- `JobRunner` keeps `submit` (row insert only) and, in role `all`, owns one in-process `JobWorker` named `local-<pid>`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_two_workers_never_claim_the_same_job(app_no_worker):
    ids = [await app_no_worker.state.runner.submit("noop-test", {"i": i}) for i in range(20)]
    w1, w2 = make_worker(app_no_worker, "w1"), make_worker(app_no_worker, "w2")
    await asyncio.gather(w1.start(), w2.start()); await wait_all_terminal(app_no_worker, ids)
    owners = await owners_of(app_no_worker, ids)
    assert len(owners) == 20 and all(o in ("w1", "w2") for o in owners.values())
    assert await count_events(app_no_worker, ids, "done") == 20   # each ran exactly once

async def test_stale_running_job_fails_live_one_kept(app_no_worker):
    stale = await insert_job(app_no_worker, status="running", owner="dead", heartbeat_ago_s=200)
    live = await insert_job(app_no_worker, status="running", owner="w1", heartbeat_ago_s=1)
    await make_worker(app_no_worker, "w2", stale_s=90).reap()
    assert (await get(app_no_worker, stale)).error == "worker lost"
    assert (await get(app_no_worker, live)).status == "running"

async def test_stream_ends_when_worker_dies(client, app_no_worker):   # Review Focus 1
    job = await insert_job(app_no_worker, status="running", owner="dead", heartbeat_ago_s=0)
    reaper = make_worker(app_no_worker, "w2", stale_s=1); await reaper.start()
    events = await collect_sse(client, job, timeout=10)
    assert events[-1]["event"] == "error" and events[-1]["text"] == "worker lost"

async def test_cancel_flag_stops_running_job(client, app_with_slow_handler): ...
    # asserts status failed, error "cancelled" within heartbeat_s + 2

async def test_drain_requeues_until_max_attempts(app_no_worker): ...
    # attempts 1 -> queued again; attempts 2 -> failed "worker stopped"

async def test_notify_reaches_other_api_instance(app_pair):
    # two app instances on one database; job submitted on A, worker in A, SSE read from B
    # asserts B streams every event in order and finishes with "done" in < 2 s
```

Fixture `app_no_worker` is the `app` fixture with `EXIQO_ROLE=api` (no in-process worker).

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_job_worker.py -v` — expect FAIL.
- [ ] **Step 3: Implement** migration, model columns, `JobWorker`, `JobListener`, cancel route, NOTIFY in `emit`; `main.py` starts the listener in roles `api`/`all` and the worker in `all`.
- [ ] **Step 4: Run** `.venv/bin/pytest api/tests -q`, `make e2e` — expect PASS.
- [ ] **Step 5: Commit** `feat(api): claiming job workers with heartbeat, recovery, cancel and NOTIFY`.

---

### Task 4: Process roles and the worker entrypoint

**Files:**
- Create: `api/app/worker.py`
- Modify: `api/app/main.py`, `Makefile` (target `worker`)
- Test: `api/tests/test_roles.py`

**Interfaces:**
- `create_app(settings)` in role `worker` raises `RuntimeError("EXIQO_ROLE=worker runs python -m api.app.worker, not the HTTP app")`.
- `python -m api.app.worker` builds `WorkerServices`, starts `JobWorker(name=os.environ["POD_NAME"] or hostname, slots=settings.job_workers, ...)`, serves the internal app (Task 5; until then only `GET /internal/healthz`) with uvicorn on `0.0.0.0:{internal_port}`, and on `SIGTERM` calls `drain(timeout_s=570)` then exits 0.
- `make worker` runs it locally with `EXIQO_ROLE=worker`.

- [ ] **Step 1: Write failing tests**: `test_api_role_has_no_local_worker` (role `api`: a submitted job stays `queued` for 1 s); `test_worker_role_rejects_http_app`; `test_worker_sigterm_drains` (subprocess with a slow handler, send SIGTERM, assert exit code 0 and job requeued with `attempts == 1`).
- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_roles.py -v` — expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — expect PASS.
- [ ] **Step 5: Commit** `feat(api): api and worker roles`.

---

### Task 5: Generated services hosted on workers, proxied by the API

**Files:**
- Create: `api/app/services/hosting.py`, `api/app/routers/run_proxy.py`, `api/alembic/versions/0004_build_run_host.py` (add `builds.run_host TEXT NULL`, `builds.run_owner TEXT NULL`)
- Modify: `api/app/routers/build.py` (run/start/status/logs/stop), `api/app/routers/verify.py`, `api/app/services/verify.py` (`run_verify` takes `Hosting`), `api/app/jobs/handlers.py`, `api/app/worker.py`, `api/app/main.py` (drop `app.state.services`, `start_locks`), `web/components/tabs/verify/SwaggerFrame.tsx`, `web/lib/generatedService.ts`
- Test: `api/tests/test_hosting.py`, existing `api/tests/test_build_verify.py`

**Interfaces:**
- `Hosting` (per worker process): `start(build_id) -> RunInfo` (wraps existing `verify_svc.start_service`), `get(build_id) -> RunInfo | None`, `stop(build_id) -> bool`, `stop_all()`.
- Worker internal app (bearer `INTERNAL_TOKEN`, 401 otherwise): `GET /internal/services/{build_id}` → `run_status` + `log_tail`; `GET /internal/services/{build_id}/logs`; `DELETE /internal/services/{build_id}`; `ANY /internal/services/{build_id}/proxy/{path:path}` → forwards to `http://127.0.0.1:{port}/{path}` with method, query, body and headers (minus hop-by-hop), streaming the response.
- `POST /builds/{id}/run` submits `service_start` (handler starts it via `w.hosting`, writes `run_host=f"{worker_host}:{internal_port}"`, `run_owner=<worker name>`, `run_port`, `run_pid`) and waits for the job up to the existing start timeout, returning the same body as today; 409 with log tail on failure.
- `GET/DELETE /builds/{id}/run`, `/run/logs` call the owning worker's internal API via `run_host`; when the worker is unreachable, clear `run_host`/`run_owner`/`run_port` and answer `running: false` with reason `"the worker hosting the service is gone"`.
- `ANY /builds/{id}/run/proxy/{path:path}` (requires a signed-in user) forwards to the internal proxy. `run_status` now returns `url` and `swagger_url` as `"{API public base}/builds/{id}/run/proxy/"` and `.../swagger/index.html`.
- `POST /builds/{id}/verify` submits with `affinity=build.run_owner`; 409 unchanged when nothing runs.
- Generated service CORS: with the proxy the service is same-origin to the browser through the API, so `Cors__Origins__*` stays only for `EXIQO_ROLE=all`.

- [ ] **Step 1: Write failing tests**

```python
async def test_proxy_forwards_to_hosting_worker(client, fake_hosting_worker):
    r = await client.get(f"/builds/{fake_hosting_worker.build_id}/run/proxy/health")
    assert r.status_code == 200 and r.json() == {"status": "Healthy"}

async def test_internal_api_rejects_without_token(internal_client):
    assert (await internal_client.get("/internal/services/x")).status_code == 401

async def test_verify_job_has_affinity_to_service_owner(client, running_service_on("w-a")):   # Review Focus 5
    job_id = (await client.post(f"/builds/{bid}/verify")).json()["job_id"]
    assert (await get_job_row(job_id)).affinity == "w-a"

async def test_affinity_job_not_claimed_by_other_worker(app_no_worker):
    jid = await app_no_worker.state.runner.submit("noop-test", {}, affinity="w-a")
    await make_worker(app_no_worker, "w-b").start(); await asyncio.sleep(1)
    assert (await get(app_no_worker, jid)).status == "queued"

async def test_status_when_hosting_worker_gone(client, build_with_run_host("10.0.0.9:9100")):
    body = (await client.get(f"/builds/{bid}/run")).json()
    assert body["running"] is False and body["reason"] == "the worker hosting the service is gone"
```

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_hosting.py -v` — expect FAIL.
- [ ] **Step 3: Implement**; update `SwaggerFrame.tsx` to use the returned `swagger_url` unchanged (it already does) and drop any `localhost` assumptions in `generatedService.ts`.
- [ ] **Step 4: Run** `.venv/bin/pytest api/tests -q`, `make e2e` (Verify spec) — expect PASS.
- [ ] **Step 5: Commit** `feat(api): generated services hosted on workers behind an API proxy`.

---

### Task 6: Same-origin base path, health endpoints, Neo4j, branding

**Files:**
- Create: `api/app/routers/system.py`, `web/lib/brand.ts`, `web/public/brand/` (neutral ExiQO mark), migration `0005_seed_source_label.py`
- Modify: `api/app/main.py` (mount under `api_base_path`), `web/next.config.ts`, `web/scripts/root-env.ts`, `web/components/shell/{TopBar,Footer}.tsx`, `web/app/{icon.svg,signin/page.tsx,layout.tsx}`, `engine/knowledge/{memory.py,seed_patterns.yaml,prompts/ask.md}`, `engine/agents/prompts/reviewer_standards.md`, `api/app/services/knowledge.py`, `api/app/routers/projects.py`, tests that assert `rsi-seed`; delete `web/public/rsi-logo*.svg`; `docker-compose.yml` (add `neo4j:5` service with `NEO4J_AUTH` from `.env`); `pyproject.toml` (add `neo4j`)
- Test: `api/tests/test_system.py`, `engine/tests/test_knowledge.py`

**Interfaces:**
- When `api_base_path` is non-empty, `create_app` returns an outer `FastAPI` that mounts the inner app at that path; `/healthz` exists on both.
- `GET /healthz` → `{"status":"ok"}` with no dependency checks. `GET /readyz` → 200 `{"postgres":"ok","neo4j":"ok|disabled","secrets":"ok"}` or 503 naming the failing check (Neo4j `disabled` when `neo4j_uri` is empty; `secrets` checks every configured `*_FILE` is readable). `GET /system/status` (admin only in `oidc` mode) → `{version, role, auth_mode, deploy_credential_mode, image_digests, egress_allowlist, brand_name, support_contact, checks: <readyz body>}`.
- `GET /system/brand` (public) → `{brand_name, support_contact}`; `web/lib/brand.ts` exposes `useBrand()`.
- Web: when `NEXT_PUBLIC_API_URL` is unset at build time and `NEXT_PUBLIC_SAME_ORIGIN=1`, the API base is `/api`. Next telemetry disabled in `next.config.ts` docs and in the image env.
- Seed label: `SEED_SOURCE = "seed"`; migration updates `knowledge.memory_items.source` from `rsi-seed` to `seed`. The honesty note in `memory.py` keeps its meaning without the vendor name ("curated seed patterns shipped with the platform").

- [ ] **Step 1: Write failing tests**: `test_healthz_without_database` (app with a bad `DATABASE_URL` still answers 200); `test_readyz_reports_neo4j_down` (`neo4j_uri="bolt://127.0.0.1:1"` → 503 with `"neo4j"` failing); `test_base_path_mount` (`api_base_path="/api"` → `GET /api/projects` 200, `GET /projects` 404); `test_seed_label_neutral` (seeded items have `source == "seed"`); `test_no_vendor_strings_in_web` (a pytest that greps `web/app web/components web/lib web/public` for `R Systems|rsystems|rsi-logo|\bRSI\b` and asserts none).
- [ ] **Step 2: Run** them — expect FAIL.
- [ ] **Step 3: Implement.** TopBar shows the neutral mark and `brand_name`; the subtitle "TIBCO BusinessWorks to .NET" stays.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`, `cd web && npm run typecheck && npm run lint`, `make e2e` — expect PASS.
- [ ] **Step 5: Commit** `feat: same-origin API path, health endpoints, Neo4j readiness, neutral branding`.

---

### Task 7: Entra ID sign-in and server-side roles

**Files:**
- Create: `api/app/auth/{__init__,jwt,oidc,session,roles,router}.py`, `web/lib/auth.ts`
- Modify: `api/app/deps.py`, `api/app/services/gates.py` (actor from `Principal`), `api/app/main.py`, `pyproject.toml` (`pyjwt[crypto]`, `itsdangerous`), `web/lib/api.ts`, `web/lib/sse.ts`, `web/lib/state.tsx`, `web/lib/personas.ts`, `web/components/shell/UserMenu.tsx`, `web/app/signin/page.tsx`
- Test: `api/tests/test_auth.py`, `web/e2e/auth.spec.ts`

**Interfaces:**
- `Principal(BaseModel)`: `oid: str`, `upn: str`, `name: str`, `roles: frozenset[str]`. Role keys: `admin, viewer, sme, architect, engineer, qa, ops`.
- `GATE_OWNERS = {"G1":"sme","G2":"sme","G3":"architect","G4":"engineer","G5":"qa","G6":"ops"}` in `roles.py` (mirrors `web/lib/personas.ts`); `roles_from_groups(groups: list[str], role_groups: dict[str, list[str]]) -> frozenset[str]`.
- `validate_jwt(token: str, *, tenant_id: str, audience: str, jwks: JwksCache) -> dict` checks issuer `https://login.microsoftonline.com/{tenant_id}/v2.0`, audience, RS256 signature, `exp`/`nbf` with 60 s leeway; `JwksCache.key(kid)` refetches once on unknown `kid`.
- Routes (role `api`, mode `oidc`): `GET /auth/login` (PKCE S256, `state`, `nonce` in a short-lived signed cookie; redirect to Entra), `GET /auth/callback` (code exchange with httpx, validate ID token, set `exiqo_session` cookie: `HttpOnly; Secure; SameSite=Lax; Path=/`, signed with `session_secret`, 8 h max age, holding `Principal`), `POST /auth/logout`, `GET /auth/me` → `Principal` or 401.
- `require_user` → `Principal`. Mode `dev`: from `X-User` (persona id; roles from the persona's role key, plus `admin`). Mode `oidc`: from the session cookie, else `Authorization: Bearer` validated JWT (groups claim); `X-User` ignored. `X-Agent` still 403.
- Gate decisions in mode `oidc`: 403 `"G4 is decided by the engineer role"` unless `GATE_OWNERS[gate]` in `principal.roles`. Audit rows store `oid` and `upn`.
- Web: in `oidc` mode (`GET /auth/me` succeeds) the user menu shows the real name and roles and hides the persona switcher; persona permissions derive from roles; `api.ts` sends `credentials: "include"` and no `X-User`; any 401 from fetch or from the SSE helper redirects to `/api/auth/login?return=<current path>` (Review Focus 2). In `dev` mode behaviour is unchanged.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.parametrize("mutate,reason", [
    ({"iss": "https://login.microsoftonline.com/other/v2.0"}, "issuer"),
    ({"aud": "someone-else"}, "audience"),
    ({"exp": past}, "expired")])
def test_jwt_rejected(mutate, reason, signer): ...

def test_unknown_kid_refetches_jwks_once(signer, jwks_server): ...   # asserts 2 fetches, token accepted
def test_groups_map_to_roles(): assert roles_from_groups(["g-eng"], {"engineer": ["g-eng"]}) == {"engineer"}
async def test_oidc_mode_ignores_x_user(oidc_client): assert (await oidc_client.get("/auth/me", headers={"X-User": "ops.sam"})).status_code == 401
async def test_gate_owner_enforced(oidc_client_as(roles={"qa"})):
    r = await c.post(f"/processes/{pid}/gates/G4/approve"); assert r.status_code == 403 and "engineer" in r.json()["detail"]
async def test_callback_sets_secure_cookie(oidc_client, fake_entra): ...   # HttpOnly, Secure, SameSite=Lax
```

`web/e2e/auth.spec.ts`: with a stubbed `/auth/me` returning 401 mid-session, the next API call navigates to `/api/auth/login?return=...`.

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_auth.py -v` — expect FAIL.
- [ ] **Step 3: Implement.** Use the exact gate route the gates router already exposes in the 403 test.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`, `make e2e` — expect PASS (dev mode unchanged).
- [ ] **Step 5: Commit** `feat(auth): Entra ID OIDC sign-in, JWT validation, gate roles enforced server side`.

---

### Task 8: Deploy-step credentials: Workload Identity and Key Vault references

**Files:**
- Create: `api/app/services/deploy_creds.py`, migration `0006_azure_connection_auth.py` (`azure_connections.environment TEXT`, `auth_mode TEXT NOT NULL DEFAULT 'local'`, `client_id TEXT`, `key_vault_uri TEXT`, `secret_name TEXT`)
- Modify: `api/app/services/infra.py` (`plan_env`, `plan_job`), `api/app/routers/infra.py`, `api/app/jobs/handlers.py` (`plan`), `api/app/services/providers.py` (Azure OpenAI managed identity), `pyproject.toml` (`azure-identity`, `azure-keyvault-secrets`), `web/components/tabs/deploy/AzureConnectForm.tsx`, `web/lib/types.ts`
- Test: `api/tests/test_deploy_creds.py`

**Interfaces:**
- `GET /deploy/environments` → `settings.deploy_environments` without secrets.
- `PUT /projects/{id}/azure` body in modes `workload_identity`/`keyvault_sp`: `{environment: str}` only; the server copies tenant, subscription, client id and Key Vault reference from the declared environment and 422s an undeclared name. In mode `local` the existing body and Fernet path remain.
- `terraform_env(conn: AzureConnection, settings: Settings, *, secret_reader: Callable[[str, str], str]) -> dict[str, str]`:
  - `workload_identity`: `ARM_USE_OIDC=true`, `ARM_TENANT_ID`, `ARM_SUBSCRIPTION_ID`, `ARM_CLIENT_ID`, `ARM_OIDC_TOKEN_FILE_PATH=$AZURE_FEDERATED_TOKEN_FILE`; no `ARM_CLIENT_SECRET`.
  - `keyvault_sp`: same ids plus `ARM_CLIENT_SECRET=secret_reader(key_vault_uri, secret_name)`; called inside the plan handler at run time, never stored, never logged.
  - `local`: existing `plan_env` with the decrypted secret.
- `keyvault_secret(vault_uri: str, name: str) -> str` using `DefaultAzureCredential` (Workload Identity in the pod) and `SecretClient`; no caching.
- Azure OpenAI with `azure_openai_auth="managed_identity"`: the client gets `azure_ad_token_provider=get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")` and no key.
- Deploy form: environment select (from `/deploy/environments`), read-only tenant/subscription/auth mode for the chosen environment, and a line stating no secret is entered here; the old fields render only when `/system/brand` reports `deploy_credential_mode == "local"` (add that field to `/system/brand`).

- [ ] **Step 1: Write failing tests**

```python
def test_workload_identity_env_has_no_secret(conn_wi, settings):
    env = terraform_env(conn_wi, settings, secret_reader=fail_if_called)
    assert env["ARM_USE_OIDC"] == "true" and "ARM_CLIENT_SECRET" not in env

def test_keyvault_secret_read_each_plan(conn_kv, settings):   # Review Focus 3
    values = iter(["v1", "v2"]); reader = lambda uri, name: next(values)
    assert terraform_env(conn_kv, settings, secret_reader=reader)["ARM_CLIENT_SECRET"] == "v1"
    assert terraform_env(conn_kv, settings, secret_reader=reader)["ARM_CLIENT_SECRET"] == "v2"

async def test_plan_payload_and_events_hold_no_secret(client, kv_env_with_secret("s3cr3t")):
    job_id = (await client.post(f"/builds/{bid}/plan")).json()["job_id"]; await wait_terminal_job(job_id)
    assert "s3cr3t" not in json.dumps(await job_row_and_events(job_id))

async def test_undeclared_environment_rejected(client_wi_mode):
    assert (await client_wi_mode.put(f"/projects/{pid}/azure", json={"environment": "nope"})).status_code == 422
```

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_deploy_creds.py -v` — expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/pytest -q`, `make e2e` (Deploy spec, local mode) — expect PASS.
- [ ] **Step 5: Commit** `feat(deploy): Workload Identity and Key Vault references for the Deploy step`.

---

### Task 9: Package mirrors for generated builds

**Files:**
- Modify: `engine/dotnet_gen/templates/nuget.config.j2`, `engine/dotnet_gen/generator.py`, `api/app/services/build.py`, `api/app/config.py` (`nuget_source: str = ""`, `tf_cli_config_file: str = ""`)
- Test: `engine/tests/test_generator.py`, `api/tests/test_build_verify.py`

**Interfaces:**
- `nuget_source` set: the generated `nuget.config` has `<clear/>` and one source `gmf` with that URL; unset: today's output (golden unchanged).
- `tf_cli_config_file` set: Terraform/Terragrunt child processes get `TF_CLI_CONFIG_FILE` in the scrubbed environment (`engine/child_env.py` allowlist).

- [ ] **Step 1: Write failing tests**: `test_nuget_config_uses_mirror` (only the mirror URL, `<clear />` present); `test_child_env_passes_tf_cli_config`.
- [ ] **Step 2: Run** — expect FAIL. **Step 3: Implement.** **Step 4: Run** `.venv/bin/pytest -q` — expect PASS (golden files unchanged when unset).
- [ ] **Step 5: Commit** `feat(build): GMF package mirrors for generated solutions and Terraform`.

---

### Task 10: Container images

**Files:**
- Create: `deploy/docker/platform.Dockerfile`, `deploy/docker/web.Dockerfile`, `.dockerignore`, `deploy/docker/entrypoint.sh`, `deploy/docker/compose.pilot.yml`
- Modify: `web/next.config.ts` (`output: "standalone"`), `Makefile` (targets `images`, `image-lint`, `image-scan`, `pilot-up`), `scripts/` (nothing else)
- Test: `deploy/tests/test_images.sh` (run by `make image-test`)

**Interfaces:**
- `platform.Dockerfile` stages: `toolchain` (from `BASE_IMAGE_DOTNET` digest; adds Python 3.11, Terraform and Terragrunt at pinned versions verified by SHA256 build args), `deps` (`pip install --no-cache-dir .` into `/opt/venv`; `python -c` pre-download of the fastembed model to `/opt/models`, `FASTEMBED_CACHE_PATH=/opt/models`), `final` (no apt, `USER 10001`, `ENV EXIQO_ROLE=api NEXT_TELEMETRY_DISABLED=1 HF_HUB_OFFLINE=1 DOTNET_CLI_TELEMETRY_OPTOUT=1`, `ENTRYPOINT ["/entrypoint.sh"]`).
- `entrypoint.sh`: role `api` → `uvicorn api.app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'`; `worker` → `python -m api.app.worker`; `migrate` → `alembic -c api/alembic.ini upgrade head`; anything else exits 64.
- `web.Dockerfile`: build with `NEXT_PUBLIC_SAME_ORIGIN=1`, run `node server.js` on 3000 as UID 10001 from the standalone output.
- `compose.pilot.yml`: services `migrate`, `api`, `worker`, `web`, `neo4j`, `proxy` (Caddy on 443 routing `/api` → api and `/` → web); every secret a Docker secret file under `./secrets/` mounted at `/run/secrets/*` with `*_FILE` env; `read_only: true`, `tmpfs: /tmp`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`; no default passwords.

- [ ] **Step 1: Write the failing image test** (`deploy/tests/test_images.sh`): builds both images, then asserts `docker run --rm --entrypoint id exiqo-platform:test -u` prints `10001`; `docker run --read-only --tmpfs /tmp exiqo-platform:test` with role `api` and a stub `DATABASE_URL` answers `/healthz` 200; `docker run ... --entrypoint sh exiqo-platform:test -c 'command -v apt-get'` exits non-zero; `HF_HUB_OFFLINE=1` embedding load succeeds with `--network none`.
- [ ] **Step 2: Run** `make image-test` — expect FAIL. **Step 3: Implement.**
- [ ] **Step 4: Run** `make image-test`, `make image-lint` (hadolint, no errors), `make image-scan` (Trivy `--severity CRITICAL --exit-code 1`) — expect PASS.
- [ ] **Step 5: Commit** `feat(deploy): hardened platform and web images, pilot compose`.

---

### Task 11: Egress audit

**Files:**
- Create: `scripts/egress_audit.py`, `deploy/egress-allowlist.txt`
- Modify: `Makefile` (`egress-audit`)
- Test: `scripts/tests/test_egress_audit.py`

**Interfaces:**
- `egress_audit.py <dir-or-image>... --allowlist deploy/egress-allowlist.txt` extracts `https?://host` and bare FQDNs in string literals from `.py`, `.js`, `.json`, `.html`, `.md` files (for an image: `docker create` + `docker export` into a temp dir), ignores RFC 2606 example domains, `localhost`, private IP ranges and XML namespace URIs (`http://www.w3.org/`, `http://schemas.*`, `http://www.tibco.com/schemas`, `xmlns` attribute values), prints `file:line host` per violation, exits 1 on any.
- Allowlist entries: `login.microsoftonline.com`, `*.vault.azure.net`, `*.openai.azure.com`, `management.azure.com`, `*.postgres.database.azure.com`, and the documented mirror placeholders as patterns.

- [ ] **Step 1: Write failing tests**: allowlisted host passes; `https://huggingface.co/x` in a `.js` file fails with its `file:line`; `xmlns="http://www.tibco.com/..."` ignored; wildcard `*.vault.azure.net` matches `gmf.vault.azure.net`.
- [ ] **Step 2: Run** `.venv/bin/pytest scripts/tests/test_egress_audit.py -v` — expect FAIL. **Step 3: Implement.**
- [ ] **Step 4: Run** tests, then `make egress-audit` on both images; fix or allowlist each finding with a comment line saying why (Claude endpoint is not allowlisted; `claude-agent-sdk` hosts appear only when the Claude provider is enabled, so the audit's image run uses `--allow-optional claude` which reads `# optional: claude` sections) — expect PASS.
- [ ] **Step 5: Commit** `feat(deploy): egress audit for images and bundles`.

---

### Task 12: Helm chart

**Files:**
- Create: `deploy/helm/exiqo/{Chart.yaml,values.yaml,values.schema.json,values.gmf.example.yaml,ci/values.kind.yaml}`, `templates/{_helpers.tpl,api-deployment.yaml,api-service.yaml,web-deployment.yaml,web-service.yaml,worker-deployment.yaml,worker-service.yaml,worker-hpa.yaml,ingress.yaml,serviceaccounts.yaml,secretproviderclass.yaml,networkpolicies.yaml,pdb.yaml,migrate-job.yaml,neo4j-backup-cronjob.yaml,configmap.yaml,data-pvc.yaml,NOTES.txt}`
- Modify: `Makefile` (`chart-check`)
- Test: `deploy/helm/tests/` (helm-unittest suites)

**Interfaces:**
- `Chart.yaml` dependency: `neo4j` (official chart, pinned version) with `condition: neo4j.enabled`.
- Values keys (schema-validated): `image.{registry,platform.{repository,digest},web.{repository,digest}}` (digest required, tag forbidden), `auth.{mode,tenantId,clientId,roleGroups}`, `deploy.{credentialMode,environments[]}`, `keyVault.{name,tenantId,secrets{}}` (logical name → Key Vault object name), `identity.{platformClientId,deployIdentities{env: clientId}}`, `ingress.{className,host,tlsSecretName,annotations}`, `api.{replicas≥2,resources}`, `web.{replicas≥2}`, `worker.{replicas,maxReplicas,slots,resources}`, `data.{storageClass: azurefile-csi-premium,size}`, `neo4j.{enabled,uri}`, `keyVault.mode` (`csi` only; `kubernetesSecret` allowed only under `x-ci`, used by Task 16), `postgres.{host}`, `egress.{allowCidrs[],allowNamespaces[]}`, `providers.claude.enabled=false`, `brand.{name,supportContact}`, `mirrors.{nuget,npm,terraformConfig}`.
- Schema rejects: `auth.mode: dev`, `deploy.credentialMode: local`, `image.*.tag`, `api.replicas < 2`. `ci/values.kind.yaml` sets `x-ci: true`, and the schema's `if x-ci then` branch allows `dev`.
- Pod security on every workload: as in Global Constraints; `automountServiceAccountToken: false` except api/worker (Workload Identity label `azure.workload.identity/use: "true"`).
- NetworkPolicies: default deny; web ← ingress namespace; api ← ingress namespace and web; worker:9100 ← api only; neo4j:7687 ← api, worker, backup; egress per `egress.*` plus DNS.
- Migration: `migrate-job.yaml` hook `pre-install,pre-upgrade`, `hook-delete-policy: before-hook-creation`, role `migrate`.
- Ingress annotations (NGINX default; AGIC alternative documented): `proxy-body-size: 2100m`, `proxy-read-timeout: "3600"`, `proxy-buffering: "off"` on `/api`, path `/api` → api (no rewrite, API mounted at `/api`), `/` → web.
- Worker: `terminationGracePeriodSeconds: 600`, env `POD_NAME`/`WORKER_HOST` from the downward API (`metadata.name`, `status.podIP`).

- [ ] **Step 1: Write failing helm-unittest suites**: `security_test.yaml` (every container has `runAsUser: 10001`, `readOnlyRootFilesystem: true`, `capabilities.drop: [ALL]`); `schema_test.yaml` (`auth.mode=dev` fails `helm template` with the GMF example; `ci/values.kind.yaml` passes); `ingress_test.yaml` (Review Focus 4: the three annotations above rendered on the `/api` ingress); `netpol_test.yaml` (worker 9100 ingress only from api pod labels); `secrets_test.yaml` (no rendered manifest contains `value:` for any env var whose name ends in `_SECRET`, `_PASSWORD`, `_KEY`, `_URL` holding credentials — those use `*_FILE`).
- [ ] **Step 2: Run** `make chart-check` (`helm dependency build`, `helm lint`, `helm unittest`, `helm template -f values.gmf.example.yaml | kubeconform -strict -summary`) — expect FAIL.
- [ ] **Step 3: Implement** the chart.
- [ ] **Step 4: Run** `make chart-check` — expect PASS.
- [ ] **Step 5: Commit** `feat(deploy): Helm chart for AKS`.

---

### Task 13: Azure prerequisites Terraform and release pipelines

**Files:**
- Create: `deploy/azure/{main.tf,variables.tf,outputs.tf,versions.tf,README.md}`, `deploy/pipelines/azure-pipelines.yml`, `deploy/pipelines/github-release.yml`
- Modify: `Makefile` (`azure-validate`)
- Test: `terraform -chdir=deploy/azure validate` and `tflint`

**Interfaces:**
- Terraform (azurerm, pinned) creates when enabled per variable: ACR (Premium, admin disabled), Key Vault (RBAC mode, purge protection), user-assigned identities `exiqo-platform` and one per `deploy_environments` entry with federated credentials for `system:serviceaccount:<ns>:exiqo-platform` / `exiqo-worker`, Postgres Flexible Server 16 with `azure.extensions = VECTOR` and private access, Storage account + Premium file share for `DATA_DIR`, Blob container `neo4j-backups`. Outputs feed `values.gmf.example.yaml` keys one-to-one (output names equal the values keys).
- Pipelines: build both images with `BASE_IMAGE_*` from pipeline variables, hadolint, `make egress-audit`, Trivy (fail on CRITICAL), syft SBOM attached as an OCI artifact, cosign sign with a key held in GMF's Key Vault, push by digest, `helm upgrade --install --atomic --timeout 15m` with the digests written into values. No step pulls from a registry other than the configured mirror.

- [ ] **Step 1: Run** `make azure-validate` — expect FAIL (no files).
- [ ] **Step 2: Implement** Terraform and pipelines.
- [ ] **Step 3: Run** `make azure-validate` (`terraform init -backend=false && terraform validate && tflint`) — expect PASS; run `actionlint deploy/pipelines/github-release.yml` — expect PASS.
- [ ] **Step 4: Commit** `feat(deploy): Azure prerequisites and release pipelines`.

---

### Task 14: Install guide, Deploy-step secrets guide, runbook

**Files:**
- Create: `docs/deploy/GMF-INSTALL.md`, `docs/deploy/DEPLOY-STEP-SECRETS.md`, `docs/deploy/RUNBOOK.md`
- Modify: `README.md` (link to `docs/deploy/`), `scripts/demo_check.py` (`--base-url`, and `--token` for a bearer token in OIDC installs)
- Test: `scripts/tests/test_docs_consistency.py`

**Interfaces:**
- `GMF-INSTALL.md` has exactly the eleven sections in spec section 3, in that order, as `##` headings. The Key Vault table lists every `*_FILE` secret from Task 1 with owner and reader. Every values key named in the guide exists in `values.schema.json`; every Make target named exists.
- `DEPLOY-STEP-SECRETS.md`: per-environment identity creation (`az identity create`, federated credential, role assignment examples `Contributor` scoped to a resource group, plus the narrower custom-role option), declaring it in `deploy.environments`, the Key Vault SP alternative, rotation.
- `RUNBOOK.md`: scaling workers, draining, a stuck job, `worker lost` meaning, backups and restore drills, secret rotation, upgrades and rollback, reading `/readyz` and `/system/status`.

- [ ] **Step 1: Write failing test** `test_docs_consistency.py`: parses the three docs, asserts the section list, that each `` `values.key.path` `` exists in the schema, each `make <target>` exists in the Makefile, and no line contains `R Systems` or `rsystems`.
- [ ] **Step 2: Run** — expect FAIL. **Step 3: Write the docs**; add `--base-url`/`--token` to `demo_check.py`.
- [ ] **Step 4: Run** the test — expect PASS.
- [ ] **Step 5: Commit** `docs(deploy): GMF install guide, Deploy-step secrets, runbook`.

---

### Task 15: Help & Support screen

**Files:**
- Create: `web/app/help/page.tsx`, `web/app/help/[slug]/page.tsx`, `web/components/help/{HelpNav,GuideView,AboutInstall}.tsx`, `web/scripts/copy-guides.mjs`, `web/e2e/help.spec.ts`
- Modify: `web/package.json` (`prebuild`/`predev` run `copy-guides.mjs`; add `react-markdown` and `remark-gfm` at pinned versions), `web/components/shell/TopBar.tsx` (headphone link)
- Test: `web/e2e/help.spec.ts`

**Interfaces:**
- `copy-guides.mjs` copies `docs/deploy/*.md` to `web/content/help/` (git-ignored) with slugs `install`, `deploy-step-secrets`, `runbook`; `troubleshooting` is the `## Troubleshooting` section of the install guide extracted to its own page.
- TopBar: `{ href: "/help", label: "Help & Support", icon: Headphones }` (lucide `Headphones`) added to `LINKS`, last.
- `/help` redirects to `/help/install`; left nav: Install on GMF, Configure Deploy-step secrets, Runbook, Troubleshooting, About this install. Guides render with `react-markdown` + `remark-gfm` (tables, code blocks with copy button), headings get anchor links, nothing loads from the network.
- `AboutInstall` reads `/system/status`; shown only when the user has `admin`; otherwise the nav item is hidden. It shows version, role, auth and credential modes, image digests, egress allowlist, and each `/readyz` check as a status pill. Support contact from `useBrand()` at the foot of every help page; hidden when empty.

- [ ] **Step 1: Write failing Playwright tests**: headphone link visible and navigates to `/help/install`; the install guide shows its eleven headings; a table renders as `<table>`; About hidden for a non-admin dev persona, shown for admin with the `postgres` pill reading `ok`; no request leaves the web origin while browsing help (`page.on("request")` asserts every URL starts with the base URL).
- [ ] **Step 2: Run** `cd web && npx playwright test e2e/help.spec.ts` — expect FAIL. **Step 3: Implement.**
- [ ] **Step 4: Run** the spec, `npm run typecheck`, `npm run lint` — expect PASS.
- [ ] **Step 5: Commit** `feat(web): Help & Support with the install guide and About this install`.

---

### Task 16: In-cluster end-to-end on kind

**Files:**
- Create: `deploy/kind/{cluster.yaml,postgres.yaml,setup.sh}`, `.github/workflows/kind-e2e.yml`
- Modify: `Makefile` (`kind-e2e`), `deploy/helm/exiqo/ci/values.kind.yaml`

**Interfaces:**
- `make kind-e2e`: creates a kind cluster, installs ingress-nginx and a `pgvector/pgvector:pg16` Postgres in the cluster, creates the secrets as plain Kubernetes Secrets mounted as files (CSI driver replaced via `keyVault.mode: kubernetesSecret` only allowed under `x-ci`), loads both images, `helm upgrade --install --atomic -f ci/values.kind.yaml` with `PROVIDER=fake`, `EMBEDDINGS_BACKEND=hash`, `BUILD_TOOLCHAIN=false`, 2 api and 2 worker replicas, then runs `scripts/demo_check.py --base-url http://localhost:<port>`.
- Then deletes one worker pod during a running build and asserts the job ends `done` (requeued) or `failed` with `worker lost`, never stays `running` past 120 s.

- [ ] **Step 1: Run** `make kind-e2e` — expect FAIL (no target).
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Run** `make kind-e2e` — expect PASS: demo check green, worker-kill assertion green.
- [ ] **Step 4: Commit** `test(deploy): kind end-to-end of the chart`.
