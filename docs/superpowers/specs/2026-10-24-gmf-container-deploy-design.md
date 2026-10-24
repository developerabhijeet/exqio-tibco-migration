# ExiQO Migrate on GMF infrastructure: containers, AKS install, Help & Support

Date: 2026-10-24. Status: approved design, sub-project 1 of the roadmap at the end of this file.

## Purpose

GM Financial installs and runs ExiQO Migrate inside its own Azure tenant. Once installed, the
platform has no link back to R Systems: no telemetry, no vendor endpoints, no vendor branding.
GMF's operators configure every secret the platform needs, including the credentials the Deploy
step uses to deploy migrated TIBCO services into GMF's Azure subscriptions. The packaging must
read as production enterprise DevOps, not a demo setup.

Success criteria:

1. `helm install` of the chart on AKS, with prerequisites from GMF (or from the optional
   Terraform in `deploy/azure/`), yields a working seven-step platform behind GMF's ingress and
   Entra ID sign-in.
2. No secret value appears in a pod spec, Helm values file, image, log or the database (except
   the local-development Fernet path, which the chart cannot enable).
3. The Deploy step authenticates to GMF's Azure with Workload Identity by default; no client
   secret is stored anywhere.
4. Egress from every pod is limited to an allowlist of GMF-controlled endpoints, and a CI check
   proves no image or bundle references another outbound host.
5. API replicas are stateless and can scale; workers scale independently; a rollout does not
   fail a running job whose worker is still alive.
6. A GMF engineer can install, operate, back up, rotate secrets and troubleshoot using only the
   guide served under Help & Support.

Decisions taken during design (by the user): AKS via Helm; Key Vault plus Workload Identity for
secrets; egress to GMF-tenant services only (Claude optional and off by default); Entra ID OIDC
sign-in; API and worker split (approach A); Neo4j deployed now as infrastructure, its memory
model in sub-project 2.

## 1. Runtime architecture

```
                 GMF ingress (App Gateway / NGINX, TLS, GMF cert, Entra OIDC)
                        │
        ┌───────────────┴────────────────┐
     web (Next.js standalone, 2+)     api (FastAPI, 2+, role=api)
                                         │  SSE via LISTEN/NOTIFY; /verify/{run}/proxy/* → worker
                 ┌───────────────────────┼────────────────────────┐
     Azure Database for PostgreSQL   worker (role=worker, N)    Neo4j StatefulSet (in-cluster)
     jobs, gates, audit, runs,       dotnet / terraform /       Bolt :7687 (TLS), PVC premium SSD,
     settings, pgvector (until SP2)  generated service          password from Key Vault,
                                         │                      nightly neo4j-admin dump → GMF Blob
                                  Azure Files RWX PVC → DATA_DIR
```

### Images

- `exiqo-platform`: engine, API and worker roles, with the toolchains the steps run (.NET 10
  SDK, Terraform or OpenTofu, Terragrunt). The process role comes from `EXIQO_ROLE`
  (`api`, `worker`, `all`; `all` is the local-development default).
- `exiqo-web`: Next.js `output: "standalone"` served by `node server.js`.
- Multi-stage builds on base images from GMF's mirror, passed as `BASE_IMAGE_*` build args and
  pinned by digest. UID 10001, read-only root filesystem with writable `/tmp` and data mounts
  only, no package manager in the final stage.
- The fastembed model is downloaded at build time into the image, so no pod calls Hugging Face.
- Each image has an SBOM (syft) and a cosign signature, pushed to GMF's ACR.
- Neo4j uses the official image mirrored into GMF's ACR.

### Job runner (refactor of `api/app/jobs/runner.py`)

The current runner is single-process by design: jobs are in-memory closures, events reach SSE
subscribers in-process, and `recover()` fails every queued or running job at startup. It changes
as follows.

- **Named handlers.** Each job kind registers a handler (`@job("build")`, `@job("verify")`, ...)
  that takes the stored payload. `submit` stores a `queued` row; it no longer carries a coroutine.
- **Claiming.** Workers claim with `SELECT ... FOR UPDATE SKIP LOCKED` ordered by `created_at`,
  set `status=running`, `owner=<pod name>`, `heartbeat_at=now()`, and refresh the heartbeat every
  10 s. A job is bound to `JOB_WORKERS` concurrent slots per worker process.
- **Recovery.** On startup and every 30 s, any `running` job whose heartbeat is older than
  `JOB_STALE_SECONDS` (default 90) is marked failed with the reason "worker lost". Live jobs on
  other workers are untouched.
- **Cancellation.** A `cancel_requested` column; the worker checks it between stages and at each
  heartbeat, then runs the existing cancellation cleanup.
- **Events.** Written to `job_events` as today, then `NOTIFY job_events, '<job_id>:<seq>'`. Each
  API replica holds one `LISTEN` connection and fans out to its SSE subscribers; the existing
  database poll remains the fallback.
- **Shutdown.** On `SIGTERM` a worker stops claiming, lets running jobs finish within
  `terminationGracePeriodSeconds` (default 600), and requeues what it could not finish (status
  back to `queued`, attempt counter incremented, at most `JOB_MAX_ATTEMPTS`, default 2).
- **Local development.** `EXIQO_ROLE=all` runs the API and in-process workers in one process, so
  `make api`, `make test` and `make e2e` keep working unchanged.
- Alembic migration adds `owner`, `heartbeat_at`, `cancel_requested`, `attempts` to `jobs`.

### Shared data

`DATA_DIR` (uploads, generated builds) is an Azure Files RWX PersistentVolumeClaim mounted by
api and worker pods. Workers write builds; API replicas read them for the code view.

### Verify through the API

Verify starts the generated service inside the worker pod on a loopback port. Each worker runs a
small internal listener (port 9100, cluster-internal only) that forwards to the services it
owns. The run row records the worker's pod IP and the port. The browser calls
`/verify/{run_id}/proxy/{path}` on the API, which forwards to that worker after an authorization
check. Worker pods are never exposed through the ingress. NetworkPolicy allows only api pods to
reach port 9100.

### Generated tests

Generated .NET tests run in the worker with `TEST_DATABASE_URL` pointing at a dedicated test
database on GMF's Postgres server (name ending in `_test`). There is no Docker socket and no
privileged pod; the Testcontainers path stays for local development only.

### Neo4j (infrastructure only in this sub-project)

- Official Neo4j Helm chart as a subchart, `neo4j.enabled` (default true), or an external
  endpoint via `neo4j.uri` when GMF hosts Neo4j itself. Neo4j Aura is not used (egress rule).
- Bolt over TLS, password from Key Vault, Premium SSD PVC, a backup CronJob running
  `neo4j-admin database dump` to a GMF Blob container via Workload Identity.
- Community edition by default (single instance); the guide states that HA clustering needs a
  Neo4j Enterprise licence held by GMF.
- `/readiness` reports Neo4j connectivity; `docker-compose.yml` gains a `neo4j` service for
  local development.
- No code writes to Neo4j until sub-project 2.

## 2. Security, secrets and identity

### Platform secrets

Stored in GMF's Key Vault and mounted by the Secrets Store CSI driver
(`SecretProviderClass`) as files under `/mnt/secrets`. `api/app/config.py` reads each secret
from `<NAME>_FILE` when set, falling back to `<NAME>`, so no value is placed in a pod spec or
values file. The pods authenticate to Key Vault with Workload Identity (a user-assigned managed
identity, `exiqo-platform`).

| Secret | Read by |
| --- | --- |
| `DATABASE_URL` | api, worker, migration Job |
| `TEST_DATABASE_URL` | worker |
| `APP_SECRET` | api, worker (local-mode credentials and Azure OpenAI key encryption) |
| `NEO4J_PASSWORD` | api, worker, Neo4j, backup CronJob |
| `AZURE_OPENAI_API_KEY` (omit with managed identity to Azure OpenAI) | api, worker |
| `CLAUDE_CODE_OAUTH_TOKEN` (optional, off by default) | api, worker |
| `OIDC_CLIENT_SECRET` (only for confidential client) | web |
| `SESSION_SECRET` | web |

Azure OpenAI also supports Entra authentication with the platform's managed identity
(`AZURE_OPENAI_AUTH=managed_identity`), in which case no key exists.

### Deploy-step credentials for migrated services

- **Default: Workload Identity.** The worker's Kubernetes service account is federated to one
  managed identity per target environment (for example `exiqo-deploy-dev`,
  `exiqo-deploy-prod`), which GMF grants RBAC on its subscriptions. Terraform runs with
  `ARM_USE_OIDC=true`, `ARM_CLIENT_ID` of that identity and the projected token file; there is
  no client secret.
- **Service principal via Key Vault reference.** For environments where federation is not
  possible, the admin enters a Key Vault secret name; the worker reads it at plan time with its
  own identity, passes it to Terraform in the scrubbed child environment, and never stores it.
- **Deploy tab form** (`web/components/tabs/deploy/AzureConnectForm.tsx`): environment, tenant,
  subscription, auth mode (*Workload identity* default, *Service principal (Key Vault
  reference)*), client ID of the identity, and the Key Vault secret name for the second mode.
  The environments and their identities are declared in Helm values; the form offers only
  declared environments.
- **Local development.** The existing Fernet-encrypted secret path remains behind
  `DEPLOY_CREDENTIAL_MODE=local`. The chart's schema rejects `local`.

### Sign-in

- Entra ID OIDC, authorization code with PKCE, against GMF's own app registration. The web
  holds an encrypted session cookie; it calls the API with the access token.
- The API validates JWTs: issuer, audience, signature against the JWKS of GMF's tenant (cached,
  refreshed on unknown `kid`), expiry.
- Roles (admin, viewer, and the G1 to G6 approver roles) map from Entra group object IDs listed
  in Helm values (`auth.roleGroups`). Gate decisions and audit entries record the Entra `oid`
  and UPN.
- `AUTH_MODE=dev` keeps the simulated account picker for local development; the chart's schema
  rejects it, so it cannot reach a GMF install.

### Network

- Default-deny NetworkPolicies for ingress and egress in the namespace.
- Allowed egress: Postgres, Neo4j, Key Vault, Entra ID (`login.microsoftonline.com` and the
  tenant's JWKS), GMF's Azure OpenAI endpoint, Azure Resource Manager, and GMF's package mirrors.
  The mirrors are configured, not hard-coded: `NUGET_SOURCE` (written into generated
  `nuget.config`), `NPM_REGISTRY`, and a Terraform `provider_installation` network mirror via
  `TF_CLI_CONFIG_FILE`.
- FQDN allowlisting is enforced by GMF's Azure Firewall or egress gateway; the chart's
  NetworkPolicies restrict by namespace and port, and the guide lists every FQDN to allow.
- Claude is off unless `providers.claude.enabled=true` and its endpoint is added to the
  allowlist.

### No link back to R Systems

- Telemetry off: `NEXT_TELEMETRY_DISABLED=1`, Langfuse disabled unless pointed at a host GMF
  runs, no update checks.
- No external fonts, CDNs or analytics; web assets are self-hosted in the image.
- Product name and support contact are configurable (`BRAND_NAME`, default "ExiQO";
  `SUPPORT_CONTACT`, default GMF's internal platform team as set in values). No R Systems URL,
  email or logo appears in the installed UI. The `rsi-seed` source label on seed patterns is
  renamed to a neutral `seed` (data migration included).
- `make egress-audit` (run in CI on both images and the web bundle) extracts hostnames and URLs
  and fails on any not in `deploy/egress-allowlist.txt`.

### Pod hardening

`runAsNonRoot`, `readOnlyRootFilesystem`, `allowPrivilegeEscalation: false`, drop ALL
capabilities, `seccompProfile: RuntimeDefault`, `automountServiceAccountToken` only where
Workload Identity needs it. Resource requests and limits on every container; PodDisruptionBudgets
for api, web and worker; startup, liveness and readiness probes (`/healthz` for liveness,
`/readiness` for readiness, with dependency checks on readiness only).

## 3. Delivery, Help & Support, testing

### Repository layout

| Path | Contents |
| --- | --- |
| `deploy/docker/` | `platform.Dockerfile`, `web.Dockerfile`, `.dockerignore`, `compose.pilot.yml` (hardened single-VM pilot) |
| `deploy/helm/exiqo/` | the chart: api, web and worker Deployments, Services, Ingress, Neo4j subchart, NetworkPolicies, `SecretProviderClass`, Workload Identity service accounts, PDBs, worker HPA, Alembic migration as a `pre-install,pre-upgrade` hook Job, Neo4j backup CronJob, `values.yaml`, `values.schema.json`, `values.gmf.example.yaml` |
| `deploy/azure/` | optional Terraform for prerequisites: ACR, Key Vault, managed identities and federated credentials, Postgres Flexible Server with pgvector, Azure Files share, Blob container for backups |
| `deploy/pipelines/` | `azure-pipelines.yml` and `.github/workflows/release.yml` templates: build, hadolint, Trivy scan, syft SBOM, cosign sign, push to ACR, `helm upgrade --install --atomic` |
| `deploy/egress-allowlist.txt` | hosts the egress audit accepts |
| `docs/deploy/` | the install guide and runbook served by Help & Support |

`docker-compose.yml` stays the local-development file (with Neo4j added); it is not the pilot
file and keeps development defaults. `compose.pilot.yml` has no default passwords: every secret
is a Docker secret file the operator creates.

### Install guide (`docs/deploy/GMF-INSTALL.md`)

1. Prerequisites checklist (AKS with OIDC issuer and Workload Identity enabled, Secrets Store CSI
   driver, ingress controller, ACR, Key Vault, Postgres Flexible Server with pgvector, Azure
   Files, DNS and TLS certificate).
2. Mirror images and toolchains into GMF registries (image list with digests, NuGet, npm and
   Terraform provider mirrors).
3. Entra app registration (redirect URIs, scopes) and the groups for each role.
4. Key Vault secrets: the table from section 2, with owner and rotation guidance.
5. Deploy-step identities per target environment, with example role assignments.
6. `values.yaml` walk-through using `values.gmf.example.yaml`.
7. Install and upgrade (`helm upgrade --install --atomic`, migration hook, rollback).
8. Smoke test: `scripts/demo_check.py --base-url https://<host>` against the install.
9. Backup and restore for Postgres (Flexible Server PITR) and Neo4j (dump and load).
10. Secret rotation (Key Vault versions, CSI rotation polling, pod restart where needed).
11. Troubleshooting (readiness output, job stuck, egress denied, token validation failures).

Companion: `docs/deploy/DEPLOY-STEP-SECRETS.md` (configuring Deploy-step environments and
identities) and `docs/deploy/RUNBOOK.md` (day-two operations).

### Help & Support screen

- A headphone icon in `web/components/shell/TopBar.tsx` opens `/help`.
- Left navigation: Install on GMF, Configure Deploy-step secrets, Runbook, Troubleshooting,
  About this install.
- The guides in `docs/deploy/` are copied into the web build at build time and rendered from
  Markdown, so they need no network.
- "About this install" (admin role only) shows version, image digests, auth mode, credential
  mode, egress allowlist, and Postgres, Neo4j, Key Vault mount and Entra status from
  `/readiness`.
- Support contact from `SUPPORT_CONTACT`.

### Testing

- Runner: claim with two concurrent workers (no double claim), heartbeat, stale recovery leaves
  live jobs alone, cancel, SIGTERM requeue with attempt limit, NOTIFY fan-out to SSE on two API
  instances.
- Config: `*_FILE` precedence, missing-file errors.
- Auth: JWT validation (bad issuer, audience, signature, expiry, unknown `kid` refresh), group to
  role mapping, `AUTH_MODE=dev` rejected outside development.
- Deploy credentials: Workload Identity env built for Terraform; Key Vault reference resolved
  and never persisted; `local` mode rejected by the chart schema.
- Verify proxy: forwards to the owning worker, refuses unauthorized callers and unknown runs.
- Chart: `helm lint`, `helm template` with `values.gmf.example.yaml` validated by
  `values.schema.json` and kubeconform; schema rejects `AUTH_MODE=dev` and `local` credentials.
- Images: hadolint, Trivy with no critical findings, `make egress-audit`, container runs as
  UID 10001 with a read-only root filesystem.
- End to end: CI installs the chart on kind (in-cluster Postgres and Neo4j, `AUTH_MODE=dev`
  allowed by a CI-only override values file that the GMF schema path does not accept,
  `PROVIDER=fake`, `EMBEDDINGS_BACKEND=hash`) and runs the seven-step demo check.
- Playwright: `/help` navigation, guide rendering, About panel hidden for non-admins.

### Out of scope here

Neo4j memory model and explorer (sub-project 2), and everything else in the roadmap below.

## Roadmap (all sub-projects functional, not mocked)

Each sub-project gets its own design, spec, plan and implementation cycle.

1. **Containers, AKS install, Help & Support** (this spec).
2. **Neo4j knowledge graph memory.** Old code (TIBCO model) and new code (generated services)
   as a graph with vector indexes, lineage between them, decisions, defects and re-entry history,
   evolving with every run; migration from the Postgres `knowledge.*` tables; a separate
   `/memory` explorer screen (graph canvas, search, timeline of how memory changed).
3. **ExiQoian, the project chat assistant.** Available to every persona (role-aware answers).
   Answers only about the current project: its TIBCO workflows, the modernized application,
   phase context, knowledge and memory. Off-topic questions (arithmetic, recipes, general chat)
   are declined with a short fixed reply, and prompt-injection attempts in the question or in
   retrieved content are treated as data and refused. Every answer carries citations to the
   phase artifacts it used. Builds on `engine/knowledge/ask.py` and reads from sub-project 2's
   graph memory.
4. **Inline citations and enterprise UI refresh.** Citations shown inline at the claims they
   support in each phase (replacing the separate evidence box), and a roomier, smoother
   SaaS-style layout across the stepper and secondary screens.
5. **Target-stack selection.** .NET, Java, Go or Python chosen at upload and as a default in
   Settings; prompts and agents follow the chosen stack; generators for Java, Go and Python with
   tests, infrastructure and parity at the level of the existing .NET generator.
6. **Iteration loop.** Re-enter Design with error logs and updated requirements; upload a
   modified zip of a downloaded build, or connect a GitHub repository, and resume from that
   point; workflow memory persisted across iterations.
7. **Connectors.** A Settings tab to connect GitHub, Atlassian (Confluence/Jira) and Figma to
   publish architecture documents, issues and UI designs.
