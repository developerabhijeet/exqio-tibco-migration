# GMF containers and AKS install: follow-ups

Open items from the implementation of docs/superpowers/plans/2026-10-24-gmf-container-deploy.md. All were triaged FOLLOW-UP by the final whole-branch review (none blocks merge).

## Needs a GMF dry run (not exercised against real Azure)

- cosign signing with the Key Vault key over a federated token; cosign verify without the transparency log may still fetch public TUF metadata
- helm with an Entra bearer token; the ADO idToken flow; AzureCLI@2.282.2 availability in GMF's org
- `make kind-e2e` re-run after the per-role worker secrets change (a2045cd range); the GitHub kind-e2e workflow has not run on GitHub yet
- Procedures flagged untested in docs/deploy: public-client registration, Bolt TLS, Neo4j restore/password recovery, bearer token via az account get-access-token

## Deferred minor findings

- T1: secrets.py — directory/unreadable/undecodable *_FILE raises misleading or raw errors; wrap as ValueError naming the var
- T1: empty mounted secret file silently overrides env value
- T1: README env table rows not padded; no cross-field validation (heartbeat<stale, api_base_path shape); trivial __init__ in FileSecretsSource
- T2: locks.py unlock `except BaseException` swallows a CancelledError delivered during shielded unlock
- T2: each lock waiter pins a pooled connection (pool sizing for many waiters)
- T2: plan payload `environment` unused by _run_plan (PLAN_ENV const) — Task 8 owns environments
- T2: plan handler catches Exception not CancelledError — plan row can stay running on cancel during _plan_inputs
- T2: _plan_inputs misleading "no Azure connection" for missing build/process
- T2: test_jobs.py registers "demo" globally without unregister; test_build_verify passes asyncio.Lock class (no exclusion) uncommented; runner.services None default -> AttributeError
- T2: test_cancelled_waiter_leaves_no_lock uses fixed sleeps (possible CI flake)
- T3: reaper max(seq)+1 can collide with a zombie emit and roll back the whole reap batch (self-heals in 30 s); use SAVEPOINT per job
- T3: drain requeues a job with cancel_requested=true (should fail "cancelled")
- T3: no cancel check between stages (spec wording); cancel latency up to heartbeat_s
- T3: role all shutdown now requeues (re-runs a job once after make api reload) — document or max_attempts=1 for role all
- T3: local-<pid> worker name collides across containers in role all (local-1)
- T3: drain waits unbounded on a handler whose cleanup hangs
- T4: heartbeat psycopg connection has no statement_timeout/TCP keepalive — a silently dead path blocks heartbeats for minutes (jobs reaped while still running)
- T4: idle heartbeat connection may be killed by idle timeout -> spurious ERROR traceback and a skipped beat
- T4: capture_signals override relies on uvicorn internal, uvicorn unpinned — pin uvicorn>=0.29
- T4: no force-quit on second SIGINT locally; test_roles imports support_handlers at module level (registry side effect); long line test_job_worker.py:698
- T5: pod IP reuse — new worker answers running:false for a dead owner's build; row keeps dead run_owner with reason None
- T5: stream cleanup gap if client disconnects before body generator runs — add BackgroundTask(upstream.aclose)
- T5: proxy buffers request bodies (await request.body()) twice in memory
- T5: role api starts with empty INTERNAL_TOKEN (should fail fast like worker); worker 401 passes straight to browser
- T5: service start failure log tail now persisted in jobs.error — ensure generated service never logs DSN/keys at startup
- T5: DELETE /run can wait up to 270 s for a pending start; _wait_job polls at 200 ms
- T5: _call reads whole body; RemoteProtocolError mid-body still counts as gone; `..;` segments pass (no traversal in httpx/Starlette/Kestrel)
- T6: /readyz engine.connect() outside wait_for (can stall past 3 s); web base-url test only string-checks; vendor-string test covers 4 dirs via grep; /system/status runs checks each call
- T7: JwksCache failed first load not rate-limited (every bearer request refetches while Entra unreachable)
- T7: issuer built from oidc_tenant_id — a domain-name tenant breaks every sign-in; validate GUID at startup or take issuer from discovery → also carry to Task 12 schema (auth.tenantId format uuid)
- T7: exchange r.json() ValueError → 500; exiqo_login not cleared on failed callback
- T7: cookies lack __Host- prefix (sibling subdomain cookie tossing → login CSRF/session fixation)
- T7: CSRF ignores Referer when Origin absent; cors_origins default localhost:3000 trusted in oidc unless chart overrides → carry to Task 12 (set CORS_ORIGINS to the install host)
- T7: X-Agent not refused on DELETE /history/model-calls in oidc; no alg:none test; no overage-with-bearer test
- T7: inner app /api/docs and /api/openapi.json public in oidc
- T7: web probe network error falls back to dev mode UI in an oidc install; e2e return assertion expects "/" after /?step=upload
- T7: generated service HTML/JS served via proxy on platform origin can call /api/* with the session cookie and pass CSRF — document as known trust boundary (Task 14)
- T7: admin-only user can write via API but the web shows them read-only (admin not mapped to a persona)
- T8: stored connection keeps copies of client_id/key_vault_uri/secret_name (resolved by fix 1 if non-local resolves from settings)
- T8: redact replaces exact string only (misses JSON/URL-encoded forms; very short secrets mangle text); plan_job re-raises unredacted exception into jobs.error
- T8: _ENV_NAME.match with $ accepts trailing \n (use fullmatch); DeployEnvironment.name has no pattern at config load
- T8: plan endpoint still calls decrypt_secret for non-local connections; sync token provider blocks loop on refresh; web DEFAULT_BRAND local may flash secret fields
- T9: no XML-escape test for mirror URL; tf config test checks child_env only, not build.py wiring
- T9: no keyvault_sp test for TF_CLI_CONFIG_FILE merge (path is mode-agnostic)
- T10: mirror URLs with embedded credentials would persist in image history — document "no credentials in URLs" (Task 14) or support --mount=type=secret
- T10: apt-get upgrade makes package set float despite digest pin — fix comment
- T10: web .next/cache symlink to /tmp/next-cache dangles on fresh tmpfs — exercise /_next/image or set images.unoptimized
- T10: migrate container receives all secrets (needs database_url only)
- T10: Neo4j password not rotatable via secret file after first start — document (Task 14 runbook)
- T10: Caddy no CSP; PILOT_BIND 0.0.0.0 note; api/worker egress unenforced in compose; secret file perms 0444 safe only in 0700 dir
- T10: generated pipelines still pin Terraform 1.13.0 / Terragrunt v1.1.6 (generator), platform image uses 1.17.0 / v1.2.1; one test_infra test assumes no dotnet in /usr/bin (fails only inside SDK image); .trivyignore CVE-2026-31789 expires 2027-01-22
- T11: bare-FQDN extraction limited to 12 TLDs, misses concatenation/embedded/unquoted/py3.12 f-string bare names — document limits
- T11: allowlist global not per-target (Monaco CDN/doc hosts also pass in engine/api) — consider # scope: web
- T11: http://schemas. prefix skip unanchored; JS stripper desync risk; css stripped as JS; docker cp stderr swallowed; image-vs-dir detection by Path.exists; dotnet nuget list ignores timeout_s and parses English [Enabled]; no tests for extract_image/main
- T11: PascalCase-only dotted host (Api.Vendor.Io) skipped by design; generator.py:2725 long docstring line
- T12: x-ci settable by --set disables all production refusals (plan-mandated) — consider failing when x-ci with keyVault.mode csi
- T12: Makefile negative-schema loop counts any failure as pass (grep stderr for expected key); kubeconform CRD catalog unpinned (pin commit)
- T12: egress.allowNamespaces has no ports; no LANGFUSE_PUBLIC_KEY value; schema doesn't require neo4jPassword when neo4j.enabled, customImage for backup, tlsSecretName outside x-ci
- T12: helm default --timeout 5m < migrate activeDeadlineSeconds 900 → document --timeout 20m (Task 13/14); pre-existing namespace deny baseline blocks migrate hook on first install → document (Task 14)
- T12: Neo4j SA federated to platform identity (reads all vault secrets) — least-privilege dedicated identity; secrets_test doesn't cover backup CronJob / subchart
- T12: StorageClass is cluster-wide (second install must set create:false, documented); subchart contract via script (helm-unittest can't parse subchart StatefulSet)
- T13: public_network_access_enabled=true opens storage/ACR to all networks (no Deny network_rules + allowlist); no precondition that public-off requires private_endpoint_subnet_id
- T13: tftest misses KV ACL Deny, ACR anonymous pull off, Postgres public off, backup shared key off, TLS1_2/https assertions
- T13: exiqo-platform federated to neo4j SAs with vault-wide Secrets User (neo4j pod can read all secrets) — separate neo4j identity with secret-scoped role
- T13: StorageClass parameters immutable (adding storageAccount to existing install fails upgrade); helm_values doesn't emit skuName (ZRS vs chart Premium_LRS default)
- T13: token file umask window / not removed on failure (trap); default ADO pool Microsoft-hosted can't reach private resources; unquoted $HELM_VALUES_FILES; ACR export_policy_enabled=false; SMB 3.1.1 + AES-256-GCM
- T13: custom target-subscription role example lacks */register/action (azurerm provider registration)
- T14: unverified az ad app update --set api.requestedAccessTokenVersion=2 (az rest PATCH fallback); CSI-synced Secret refresh on pod restart with rotation off; cypher-shell -d system
- T14: no command for enabling azure-keyvault-secrets-provider add-on / --enable-secret-rotation; walkthrough keyVault.secrets omits testDatabaseUrl; runbook Postgres restore should repeat DATABASE_URL/postgres.host change
- T14: BYO federated-credential loop omits backup SA (table notes it conditional); "Without --token" sentence wording after example change
- T15: CopyButton no fallback when navigator.clipboard undefined (plain-HTTP); guideHref matches bare/./ filenames only; extracted troubleshooting #anchors dangle; About shows admin-only message on network/5xx errors; tone names gate/det for ok/disabled; no img component guard; e2e hard-codes eleven sections
- T16: worker_kill polls via kubectl exec psql (slow; fast builds may outrun kill → retries); workflow pip pins only top-level httpx/lxml; on-PATH helm/kind versions unchecked (pinned only in CI); kill check proves "worker lost", not requeue; Verify only refusal path with toolchain off; GitHub workflow not yet run on GitHub
