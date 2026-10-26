# ExiQO Migrate runbook

Day-two operations for an install made with `GMF-INSTALL.md`. Commands assume the namespace
`exiqo` and the release name `exiqo`. Components: `exiqo-api` and `exiqo-web` (2 replicas each),
`exiqo-worker` (autoscaled), the StatefulSet `exiqo-neo4j`, and Postgres and Azure Files outside
the cluster.

## Reading /readyz and /system/status

Three endpoints describe health, and they answer different questions.

| Endpoint | Who may call it | Question |
|---|---|---|
| `/healthz` | anyone (probes) | Is the process alive? No dependency is touched, so a slow database never gets a pod killed. |
| `/readyz` | anyone (probes) | Can this pod serve traffic? Checks Postgres, that every configured secret file is readable and Neo4j. |
| `/system/status` | admin role | What is installed and how is it doing? The About panel's data. |

Through the ingress they are under `/api`: `https://<host>/api/healthz`, `/api/readyz` and
`/api/system/status` (the last needs a signed-in admin: a session cookie, or
`curl -H "Authorization: Bearer $EXIQO_TOKEN"`).

`/readyz` answers 200, or 503 when a gating check fails:

```json
{"postgres": "ok", "neo4j": "ok", "secrets": "ok"}
```

| Check | Values | Gates readiness? |
|---|---|---|
| `postgres` | `ok`, `error: <exception class>` | yes |
| `secrets` | `ok`, `error: unreadable <names>` | yes |
| `neo4j` | `ok`, `degraded: <class> (down <s> s)`, `down: <exception class>`, `disabled` (no `NEO4J_URI`) | yes, unless `NEO4J_GATES_READINESS` is false; `degraded` (down less than `NEO4J_READY_GRACE_SECONDS`, 900) still counts as ready |

A 503 removes that pod from the Service; if every api pod is unready the ingress answers 503. The
message never carries a value or a URL, only the check and the exception class. A `neo4j: degraded`
is expected for about a minute during the nightly backup and is not an incident by itself; only
`down` (past the grace) takes the pod out of service. A pod that starts during a Neo4j outage is
ready for up to `NEO4J_READY_GRACE_SECONDS`, after which all api pods go unready together; a backup
that runs longer than the grace therefore flips readiness.

`/system/status` adds: `version`, `role`, `auth_mode`, `deploy_credential_mode`, `image_digests`
(what is running, by digest), `egress_allowlist`, `brand_name`, `support_contact` and the same
`checks`. Compare `image_digests` with what the release pipeline printed after an upgrade.

Workers expose `/internal/healthz` on port 9100, cluster-internal, for the kubelet only. Their
state is in the logs (`kubectl -n exiqo logs deployment/exiqo-worker`) and in the `jobs` table.

## Scaling workers

Workers run the long jobs (scan, analyze, design, build, test, verify, plan). A worker pod runs
`worker.slots` jobs at once (`JOB_WORKERS`, default 2), each pod requests 2 CPU and 4 GiB, and the
HorizontalPodAutoscaler `exiqo-worker` scales between `worker.replicas` (the minimum, default 2) and
`worker.maxReplicas` (default 6) on CPU (`worker.targetCPUUtilization`, 70 percent). Scale-down is
deliberately slow: a 15 minute stabilization window, then one pod per 10 minutes, because a removed
pod drains its running jobs.

- To change capacity, change the values and run `helm upgrade` (`GMF-INSTALL.md`, section 7). The
  keys: `worker.replicas`, `worker.maxReplicas`, `worker.slots`, `worker.resources`.
- `kubectl scale deployment/exiqo-worker` does not stick while the autoscaler exists; it resets the
  count to what the autoscaler wants.
- More `worker.slots` per pod shares the pod's CPU, memory and its ephemeral storage
  (`worker.tmpSize`) between jobs; add replicas first.
- Jobs wait as `queued` rows when every slot is busy. A growing queue with idle CPU means jobs are
  pinned to a worker (see "A stuck job") or the workers are not claiming.
- Queue depth and running jobs:

  ```sql
  select status, count(*) from jobs where status in ('queued', 'running') group by status;
  ```

- The api (`api.replicas`) and web (`web.replicas`) are fixed counts, never below 2.

## Draining

There is no drain flag: stopping a worker pod drains it. A pod stopped for any reason (a rollout, a
scale-down, `kubectl delete pod`, a node drain) receives SIGTERM and then:

1. stops claiming new jobs,
2. lets its running jobs finish for up to `JOB_DRAIN_SECONDS` (570 seconds) inside the pod's 600
   second termination grace period,
3. requeues what is still running if the job has attempts left (`JOB_MAX_ATTEMPTS`, 2, so a job
   interrupted by a shutdown is requeued once), or fails it with "worker stopped".

So to take a worker out of service: `kubectl -n exiqo delete pod <worker-pod>` (the Deployment
starts a replacement) and wait for it to terminate. To drain a node, `kubectl drain` respects the
PodDisruptionBudgets (one worker, api or web pod unavailable at a time). Plan node maintenance
outside long builds (a build can run about 25 minutes).

Consequences to know:

- A Verify run hosts the generated service on one worker (the Verify job is pinned to it). When that
  worker stops, the service stops; start it again from the Verify step. A queued job pinned to a
  worker that is gone fails with "worker lost" (below).
- The pod's `/tmp` and the NuGet cache are emptied; they refill from the mirrors.

## A stuck job

First find out whether it is stuck or just long. Builds, tests and Verify legitimately run for
many minutes; what matters is the heartbeat.

```sql
select id, kind, status, stage, owner, attempts, cancel_requested, heartbeat_at,
       now() - heartbeat_at as silent, error
from jobs where status in ('queued', 'running') order by created_at;
```

| What you see | Meaning | Action |
|---|---|---|
| `running`, `silent` under about 30 seconds | alive; the worker heartbeats every 10 seconds | wait; read the live log (`GET /api/jobs/<id>/events`, or the step's log in the app) |
| `running`, `silent` over 90 seconds | the worker is gone or cut off from Postgres; any worker's reaper fails it within about 30 seconds ("worker lost") | check the worker pod (below); re-run the step |
| `queued`, no `owner`, workers idle | not claimed: no free slot, or an `affinity` naming a worker that is not running | check `affinity`; a pinned job whose worker shows no heartbeat for 90 seconds is failed as "worker lost" |
| `queued`, all workers busy | waiting for a slot | add capacity or wait |
| `running` but the user no longer wants it | | cancel it |

Cancel with `POST /api/jobs/<id>/cancel` (a signed-in user; the app's cancel button does this): a
queued job fails at once with "cancelled"; a running one is flagged and its worker cancels it at
the next heartbeat (within 10 seconds).

A job that stays `running` with a fresh heartbeat for far longer than its step normally takes is
doing real work that is not finishing: read its events and the worker log, and check the pod's CPU
and ephemeral storage (`kubectl -n exiqo top pod`, `kubectl -n exiqo describe pod`). Never edit
the `jobs` table by hand to "unstick" a job; cancel it, or let the reaper fail it.

## worker lost

"worker lost" is the error stored on a job whose worker stopped proving it was alive: the
heartbeat (`heartbeat_at`, refreshed every `JOB_HEARTBEAT_SECONDS`, 10) is older than
`JOB_STALE_SECONDS` (90). Every worker reaps at start and every 30 seconds. The heartbeat runs on
its own thread with its own Postgres connection, so a busy job cannot make a healthy worker look
lost.

Common causes, most likely first:

1. **Out of memory.** The container was OOM-killed. `kubectl -n exiqo describe pod <worker>` shows
   `OOMKilled` under the last state; raise `worker.resources` (limits default to 8 GiB) or lower
   `worker.slots`.
2. **Evicted for ephemeral storage** (the `/tmp` emptyDir and the NuGet cache count against it):
   raise `worker.tmpSize` and the ephemeral-storage requests and limits.
3. **A node was lost or drained abruptly**, so there was no graceful drain.
4. **Postgres unreachable** from the worker for over 90 seconds (failover, network, firewall).

Unlike a graceful shutdown, a lost worker's job is **not** requeued: it is failed and the user runs
the step again. A job requeued by a drain keeps its event sequence and continues it.

## Backups and restore drills

What is backed up where is in `GMF-INSTALL.md`, section 9: Postgres (Flexible Server
point-in-time restore), the `exiqo-data` share (Azure Backup snapshots) and Neo4j (the CronJob,
on by default: it holds the project memory). A backup is untested until restored, so run a drill each
quarter and after any change to the backup configuration. Record the elapsed time of each step.

**Postgres drill.**

```sh
az postgres flexible-server restore -g <rg> -n exiqo-drill-<date> \
  --source-server <server> --restore-time <UTC time from the last day>
```

Connect to the restored server and check that the schema and the data are plausible:

```sql
select version_num from alembic_version;
select count(*) from jobs;
```

Confirm the version matches the running release, then delete the drill server. For a real
recovery, follow `GMF-INSTALL.md` section 9: point `exiqo-database-url` at the restored server,
`helm upgrade`, restart api and worker.

**Share drill.** Restore the latest snapshot to a scratch share, mount it in a scratch pod, and
open a build directory under `builds/` and an upload under `uploads/`.

**Neo4j drill (when the backup is on).** Download the newest dump from the container, then load it
into a scratch Neo4j:

```sh
docker run --rm -v "$PWD/backup":/backup -v exiqo-drill:/data <neo4j image from neo4j.image.customImage> \
  neo4j-admin database load neo4j --from-path=/backup --overwrite-destination=true
```

Start that Neo4j and check that it opens. A real recovery uses the one-off pod procedure in
`GMF-INSTALL.md`, section 9. These steps follow the Neo4j 5 manuals and have not been exercised
in this repository, so a first drill is also the test of the procedure.

## Memory (Neo4j)

Neo4j holds each project's memory as a bitemporal graph; Postgres keeps the event log
(`memory_events`) and the job queue. Ingest runs as `memory.ingest` jobs after each phase and gate.
The commands below run in an api or worker pod with `NEO4J_URI` set (exit 2 without it):

```sh
kubectl -n exiqo exec deploy/exiqo-api -- python -m api.app.memory_admin <command>
```

| Command | Use |
|---|---|
| `reingest --project <id>` | queue one ingest per scope (estate, each process's analysis, design, build/verify, deploy and gates, every decision, seeds). Idempotent: unchanged content is a no-op. Use after a Neo4j restore, or after an ingest failed. |
| `reembed [--project <id>] [--wait]` | recompute vectors with the active embeddings backend. Without `--project` it covers every project and records the backend at the end; until then ingests fail with the backend-mismatch error, so re-ingest afterwards. With `--project` it only refreshes that project's vectors under the backend already recorded. Run the all-project form after changing `EMBEDDINGS_BACKEND`. |
| `migrate [--wait] [--timeout 1800]` | run the one-time migration by hand (below). |

Exit statuses: 0 ok, 1 the job failed, 2 usage (no `NEO4J_URI`, unknown project), 3 the `--wait`
timed out (`--timeout`, default 1800 s).

**Reading ingest results.** Each ingest is a job (`memory.ingest`); its result has the revision
number and counts. "memory unavailable after 10 attempts: <reason>" means Neo4j was unreachable
through every retry: fix Neo4j, then `reingest`. The explorer's History shows revisions and what
changed per key. Projects deleted from Postgres keep their old vectors in Neo4j; the API cannot
reach them.

**Migration from the Postgres knowledge store.** On start with `NEO4J_URI` set, when schema
`knowledge` exists and `knowledge.meta` has no `neo4j_migrated_at`, the API queues `memory.migrate`
(once, however many replicas start). Ingests wait (5 s, attempt not counted) while it is queued or
running. It copies memory items into `memory_events`, re-derives the graph through the live
projectors, reuses old vectors when the embeddings backend is unchanged, checks that every item
and chunk text is in Neo4j, and only then renames `knowledge` to `knowledge_retired` and stamps
`neo4j_migrated_at`. Run by hand: `python -m api.app.memory_admin migrate --wait`.

*A miss that persists.* The job fails with `migration incomplete for <project>: [<old ids>]` and
`knowledge` stays.

1. Look at the named rows: `SELECT id, kind, text FROM knowledge.memory_items WHERE id = ANY(...)`,
   or the same on `knowledge.chunks`.
2. Usual causes: Neo4j was unavailable, or a text holds raw TIBCO XML (refused on write). A chunk
   whose process was deleted is still copied as an orphan.
3. Fix the cause (for a row that should not be migrated, delete it from `knowledge.*` after taking
   a backup).
4. Run `memory_admin migrate --wait` again. Migrated projects are no-ops (same revision); only the
   incomplete ones are redone, and the schema is renamed only when every project verifies.

**Rollback to the previous release.** The old data is in `knowledge_retired`. In the Postgres
database:

```sql
ALTER SCHEMA knowledge_retired RENAME TO knowledge;
```

then `helm rollback`. Writes made in Neo4j after the migration are not copied back. Before
upgrading again, either delete the `neo4j_migrated_at` row from `knowledge.meta` so the API queues
the migration, or run `memory_admin migrate`.

## Connectors

Optional GitHub, Confluence, Jira and Figma integrations (`GMF-INSTALL.md`, section 6, "Connectors").
Two layers decide whether one is on: `connectors.<kind>.enabled` in the install, and the admin's
Settings, Connectors tab; `GET /api/connectors` shows the result.

**Enabling one.** Create the service account and token with the permissions in the install guide,
write the Key Vault secret, set `connectors.<kind>` (`enabled`, URLs, `allowedHosts`, `cidrs`),
`helm upgrade`, then in Settings, Connectors set the Confluence space or Jira project and press
"Test connection" (the test needs the `admin` role). Enable them one at a time; each test is audited.

**Rotating a token.** Write a new Key Vault version, wait for the CSI refresh (or restart api and
worker), press "Test connection", then revoke the old token. A running push or publish keeps the
token it started with.

**Private CA.** "certificate verify failed" in a test means the server's certificate chain is not
trusted by the image: provide `connectors.caBundle` (PEM text) or `keyVault.secrets.connectorsCaBundle`
with the root and intermediates, restart api and worker, test again. Do not look for a way to turn
verification off; there is none.

**Troubleshooting.** The test and every failed job show the remote status, with secrets removed.

| Message or symptom | Cause and fix |
|---|---|
| 401 or "bad credentials" | The token is wrong, expired or revoked, or (Atlassian cloud) `ATLASSIAN_USER` is not the email of the token's account. Rotate as above. |
| 403 | The token lacks a permission: GitHub `contents:write`, `pull_requests:write`, `metadata:read`; Confluence View space, Add page, Edit page and Add attachment on the space; Jira Browse Projects, Create Issues and Edit Issues in the project; Figma `file_content:read` (files, frames, images) and `current_user:read` (the connection test). GitHub branch protection or a required SSO authorization of the token (enterprise "Authorize SSO") also gives 403. |
| "host ... is not in this connector's allowed hosts" or "not allowed" | The URL or a redirect names a host outside `connectors.<kind>.allowedHosts` (or Settings holds a URL for another host). Add the host to the install values; the client never follows a redirect off the list or to plain http. |
| Redirect to an SSO or login page | The service wants browser sign-in for that URL: the base URL points at the web front door instead of the API, or the token type is not accepted there. Use the API base URL (GitHub `apiUrl`, Confluence and Jira context path). |
| Timeouts, "connection refused" | Egress. `kubectl -n exiqo get networkpolicy exiqo-connectors-egress -o yaml` should list the connector's `cidrs` on 443; check the Azure Firewall FQDN rule and private DNS for the name, and that the CIDR covers the address the name resolves to from inside the cluster (`kubectl -n exiqo exec deploy/exiqo-api -- python -c "import socket;print(socket.getaddrinfo('<host>',443))"`). The chart refuses to render an enabled connector without its own `cidrs` (and Figma `imageCidrs` when `imageHosts` is set); `egress.allowCidrs` does not substitute. |
| Confluence publish answers 409 ("Confluence rejected the update twice") | Someone saved the page in Confluence while the platform was updating it: the platform re-reads the current version and retries once, and two conflicts in a row fail the job. Wait until the editing stops and publish again. |
| Jira shows a 404 on a link, issue marked stale | The issue was deleted or moved out of the project. The link is marked stale; in the Jira issues dialog tick "Create again the issues that were deleted in Jira" (shown when a selected item has a stale link) and create them again. The API equivalent is `POST /api/processes/<id>/jira/issues` with `"create_again": true`. |
| "the ... connector is not enabled" (409) | Off in the install or in Settings. Nothing was sent. |
| "the ... connector has no credential: mount ..." (409) | The secret file is missing or empty: check the Key Vault object and `kubectl -n exiqo describe pod`. |

**Audit queries.** Every test, push, publish, issue creation and refusal writes a line to
`audit_log` with `action` of the form `connector <kind> <operation>`; `detail` carries ids, URLs,
counts and the outcome (`ok`, `failed`, `refused`), never a token or a request body. Connector
settings changes are `connector settings changed` (field names only). For example:

```sql
select ts, actor, action, process_id, detail->>'outcome' as outcome, detail->>'target' as target
from audit_log where action like 'connector %' order by ts desc limit 50;

select ts, actor, action, detail from audit_log
where action like 'connector %' and detail->>'outcome' <> 'ok' and ts > now() - interval '7 days';
```

What was published where is in `connector_links` (kind, object type, remote id and URL, `stale`).

## Secret rotation

The procedure per secret is in `GMF-INSTALL.md`, section 10; the Deploy-step credentials are in
`DEPLOY-STEP-SECRETS.md`. The routine:

1. Write a new version of the Key Vault secret (same name).
2. Restart: `kubectl -n exiqo rollout restart deployment/exiqo-api deployment/exiqo-worker`. The
   apps read secrets only at start; workers drain first.
3. Verify with `/readyz` (`secrets: ok`) and a sign-in.

Connector tokens (`GITHUB_TOKEN`, `ATLASSIAN_TOKEN`, `FIGMA_TOKEN`) and the private CA bundle are
covered above, under "Connectors".

Keep a calendar of expiries: the Entra client secret (`OIDC_CLIENT_SECRET`), the TLS certificate
(`ingress.tlsSecretName`), and any Key Vault service principal secrets. The Neo4j password lives in
two Key Vault objects that must rotate together, plus an `ALTER USER`; follow the section 10
procedure exactly, because Neo4j applies the Key Vault value only on the first start of its data
volume.

## Upgrades and rollback

Before:

1. Read the release notes for migrations and for changes to the values schema; compare your
   `values.gmf.yaml` with the new `values.gmf.example.yaml`.
2. Confirm the release was built, scanned and signed by the pipeline, and note the digests.
3. Note the time (UTC) as the Postgres recovery point, and make sure no long build is running
   (`select kind, stage from jobs where status = 'running'`). Running jobs are drained, but a
   build is cheaper finished.

Upgrade (pipeline, or by hand as in `GMF-INSTALL.md`, section 7):

```sh
helm upgrade --install exiqo deploy/helm/exiqo -n exiqo -f values.gmf.yaml -f values.azure.json \
  --set-string image.platform.digest=sha256:<new> --set-string image.web.digest=sha256:<new> \
  --atomic --timeout 20m
```

The migration Job runs first (up to 15 minutes): `kubectl -n exiqo logs job/exiqo-migrate`. Then
`kubectl -n exiqo rollout status deployment/exiqo-api deployment/exiqo-web deployment/exiqo-worker`.
Rollouts keep full capacity during the change (no pod is removed before its replacement is ready).
Afterwards: `/readyz`, the About panel's `image_digests`, and for a major release the acceptance
run (`GMF-INSTALL.md`, section 8).

Roll back:

```sh
helm history exiqo -n exiqo
helm rollback exiqo <revision> -n exiqo --wait
```

`--atomic` already rolls back a failed upgrade. A rollback does **not** reverse database
migrations: it restores the old images and objects. If the new migration only added things, the old
release runs on the new schema; if it changed or dropped something the old release needs, restore
Postgres to the recovery point from before the upgrade (section 9 of the install guide) and then
roll back.

Other upgrades:

- **Neo4j image or chart.** Change the digest only to a version the release notes allow; a major
  version change needs a dump and load, not a new image on the old volume.
- **AKS.** Node upgrades drain pods through the PodDisruptionBudgets; see "Draining".
- **StorageClass.** Its parameters are immutable. To change them, create a new class under a new
  name and migrate the share deliberately.

## Local development notes

These apply on a developer machine, where the same job runner is used: `make api` (the default
`EXIQO_ROLE=all` runs the worker inside the API process) or `make worker` beside an API started
with `EXIQO_ROLE=api`:

- Stopping the process while a job runs requeues that job once (`JOB_MAX_ATTEMPTS=2`); on the
  second interruption it fails with "worker stopped".
- A job whose heartbeat goes stale for `JOB_STALE_SECONDS` (90) fails with "worker lost", exactly
  as on the cluster: for example after a laptop sleeps mid-job.
- `AUTH_MODE=dev` (the default locally) is refused by the chart's schema, so it cannot reach an
  install.
