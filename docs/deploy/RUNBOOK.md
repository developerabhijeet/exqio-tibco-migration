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

## ExiQoian

ExiQoian is the project assistant in the slide-over opened from the top bar ("ExiQoian", or Ctrl+J
and Cmd+J). It answers only about the current project (its TIBCO workflows, the modernized
application, and what each phase found and decided) from the project's Neo4j memory, and cites the
phase artifacts inline. It has no tools and takes no actions, so it cannot approve a gate or start
a step. It needs no setting of its own: it runs wherever memory (`NEO4J_URI`) is configured, with
the model provider if there is one and without it (a no-model answer built from the top memory
hits, labelled "From memory, no model" in the panel).

**Who can use it.** Every persona, viewers included. In `AUTH_MODE=oidc` any signed-in user who
holds at least one role may chat; the panel header shows a persona select with the roles the user
holds (the first held is the default), and a request for a role the user does not hold is 403. The
persona changes only the order of the sources and the tone of the answer, never what may be
retrieved. Every other viewer write stays refused. The routes are under
`/api/exiqoian/<project id>/` (`profile`, `conversations`, `conversations/<id>/messages`, the last
a server-sent event stream).

**What it declines.** The decline text is fixed and written by the platform, never by a model, and
no model is called for a decline made by the rules.

| Verdict | When | What the user sees |
|---|---|---|
| `off_topic` | arithmetic, recipes, general knowledge, chit-chat, a question with too little to go on | "I'm ExiQoian, and I only answer questions about this project ..." |
| `injection` | text that tries to change the assistant's behaviour: override or role-change phrases, requests for its instructions, forged `<source>` or `system:` markers, encoded payloads, in several languages and obfuscations; also a model reply that leaks the per-request canary | "I can't follow instructions that try to change how I work ..." |
| `not_covered` | nothing in the project's memory clears the retrieval floors, or the model's answer cited nothing | "I couldn't find anything in this project's memory that answers that, so I won't guess ..." |
| `intro` | a greeting or "what can you do" | the introduction and three suggested questions |
| `unavailable` | memory is down or not configured | "Project memory is unavailable right now ..." or "Project memory is not configured for this install ..." |

An injection mixed into an on-topic question refuses the whole message; the user resends the
question alone. A project process that happens to be called like an off-topic word (a process named
`CookDealerRates`) lets that word through the off-topic filter: the project's own names count as
on-topic. Retrieved memory text that carries an injection is dropped before the model sees it.
Questions over 2000 characters are refused with 422.

**Conversation privacy.** A conversation belongs to the user who started it and is shown only to
them; another user's conversation id answers 404, administrators included. The tables are
`chat_conversations` and `chat_messages`; the text is stored masked. A user deletes their own
conversation from the panel's conversation menu, and deleting the project removes all of its
conversations. There is no automatic retention purge: the install's Postgres backup policy governs
how long chat text is kept. If your policy needs one, schedule a delete such as:

```sql
delete from chat_conversations where updated_at < now() - interval '180 days';
```

(messages go with their conversation).

**The audit row.** Every question writes one `audit_log` row with `action` `exiqoian asked`,
whatever the verdict. `detail` carries `conversation_id`, `message_id`, `verdict`, `rule` (the guard
rule that decided, if any), `persona`, `used_model`, `provider`, `sources`, `cited`,
`dropped_sources` and `question` (masked, cut to 500 characters). An `injection` verdict also logs
a warning on `api.exiqoian` with the rule name, never the text.

```sql
select ts, actor, project_id, detail->>'verdict' as verdict, detail->>'rule' as rule,
       detail->>'persona' as persona, detail->>'question' as question
from audit_log where action = 'exiqoian asked' order by ts desc limit 50;

-- who is being refused as an injection, and by which rule
select actor, detail->>'rule' as rule, count(*)
from audit_log where action = 'exiqoian asked' and detail->>'verdict' = 'injection'
  and ts > now() - interval '7 days' group by 1, 2 order by 3 desc;

-- answers the model wrote but that cited nothing
select ts, actor, detail->>'question' from audit_log
where action = 'exiqoian asked' and detail->>'verdict' = 'not_covered' and detail->>'used_model' = 'true';
```

A burst of `injection` rows from one actor is the thing to look at; a `rule` of `output_leak`
means the model's reply, not the question, was refused.

**Rate limit.** 12 questions per user per rolling 60 seconds, counted from the stored messages, so
it holds across api replicas. The 13th answers 429 "slow down: ExiQoian takes 12 questions a
minute"; the panel shows that text. There is no setting for it.

**When memory is down.** ExiQoian reads memory only through the same retriever as the explorer, so
it is down when Neo4j is. The question is still stored and audited (verdict `unavailable`, rule
`memory_unavailable`, or `not_configured` when `NEO4J_URI` is empty), the stream sends an `error`
event and the panel shows the fixed "unavailable" or "not configured" reply. Nothing is queued:
the user asks again when `/readyz` shows `neo4j: ok` again (see "Reading /readyz and
/system/status", and "Memory (Neo4j)" for a restore). The panel's profile
(`GET /api/exiqoian/<project id>/profile`) reports `memory: {configured, available}` and the model
provider name (`fake`, `none` or the real one), which is the quickest check from a browser session.
If the model provider fails, ExiQoian falls back to the no-model answer instead of failing.

**Egress.** The panel uses only the platform's own API. It adds no outbound host, so
`make egress-audit` and the install's allowlist are unchanged.

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

## Iterations

An iteration is one numbered pass of the loop that re-enters Design with error logs and new
requirements, resumes from an edited copy of a downloaded build, or starts from a GitHub branch.
The counter is per project; iteration 1 always exists. The architect opens one from the Build,
Verify, Deploy or Design tab ("Re-enter design"), an engineer uploads an edited zip on the Build
tab ("Upload an edited copy"), and the Repository panel on the Upload and Build tabs connects a
branch (it shows only when the GitHub connector is on, see "Connectors"). Opening an iteration
resets G3 to G6 of the process with an audit line naming the iteration. Roles: architect or admin
open and close (an SME may open with requirements only); engineer or admin upload edits and
connect or push repositories (an SME may connect an estate repository); viewers read.

The tables are `iterations` (status `open`, `designing`, `building`, `verifying`, `deploying`,
`closed`, `abandoned`; `kind` `initial`, `reentry`, `edits`, `repo`), `iteration_inputs`, `edit_sets`
and `project_repos`. Audit lines: `iteration opened`, `iteration closed`, `iteration abandoned`,
`edit set uploaded`, `edit set resumed`, `repo connected`, `repo pulled`.

```sql
select n, kind, status, resume_phase, opened_by, opened_at, closed_at, note
from iterations where project_id = '<project id>' order by n;

select ts, actor, action, detail from audit_log
where action in ('iteration opened', 'iteration closed', 'iteration abandoned',
                 'edit set uploaded', 'edit set resumed', 'repo connected', 'repo pulled')
order by ts desc limit 50;
```

**An iteration is stuck.** The status follows the jobs (a design job sets `designing`, build
`building`, verify `verifying`, plan `deploying`; G6 approved for every process of the iteration
sets `closed`), so a status that does not move means a job that did not run or did not finish.

1. Read the iteration: `GET /api/iterations/<id>` returns its inputs and a timeline of the design
   records, builds and gate decisions that belong to it. The Iterations strip under the stepper
   shows the same.
2. Look at its jobs with the queries in "A stuck job" (`kind` design, build, verify or plan for the
   process). A `running` job with a stale heartbeat is "worker lost": run the step again. A
   `queued` job waits for a slot or a pinned worker.
3. Opening another iteration for the same process is refused with 409 while its design or build job
   is queued or running: cancel the job (or wait), then open it. If the design job could not even
   be queued, the platform abandons the new iteration itself with the note "the design job could not
   be queued: the iteration was abandoned, open it again" (the gate reset stands); open it again.
4. To give up on an iteration, an architect or admin closes it (`POST /api/iterations/<id>/close`)
   or abandons it (`.../abandon`) with a note. Both answer 409 if it already ended. Opening a new
   iteration for a process that is in an open one abandons the old one ("superseded by iteration
   n"). Iterations are never deleted and their inputs stay readable.
5. Never edit the `iterations` table by hand.

**An edit set conflicts.** An uploaded edited zip is compared with the baseline build the server
holds (the manifest `EXIQO_BASELINE.json` in the zip only names the build, so editing it changes
nothing), and `edit_sets.files` lists exactly the modified, added and deleted files. Nothing is
built until the engineer presses "Resume from <phase>" on the preview. A refused upload says why:
a path outside the archive folder, a symlink, a duplicate or case-colliding name, more than 20 000
entries, a size over `upload_max_bytes` (413), a compression ratio that looks like a bomb, a build
that is still queued or running, or a manifest naming a build of another project.

A conflict is a file the engineer changed and the regenerated code changed on the same lines. The
hand edit always wins and is kept as uploaded; the Build tab's "hand edits" stage lists every path
as `kept`, `merged`, `conflict`, `added`, `deleted` or `restored`, and the merge output with both
versions is in the build folder at `.exiqo/conflicts/<path>.merge`. To resolve one, take the
generated improvement by hand, download the new build, and upload it again as the next edit set.
Files marked with the "hand edit" badge are protected: the code builder, test engineer and both repair
loops leave them alone, and the review panel still reads them.

| Resume message | Meaning and fix |
|---|---|
| "the edit set was already resumed" (409) | The build for it was queued. Open the build. |
| "the design must be approved again (G3) ..." (409) | The approved design moved since the baseline build (someone re-designed). The edits are kept; the architect approves the design, then the engineer presses Resume again. |
| "upload them again" (409) | The approved design hash no longer equals the baseline build's. Download the latest build, re-apply the edits, upload again. |

An infrastructure-only edit set (only `infra/`, `Dockerfile` or pipelines changed) builds from the
`infra validate` stage and records the earlier stages as skipped; its code is the baseline's, so
check the build's test results are the baseline's before approving G4.

**A clone times out.** Connecting a repository or pulling a branch queues a `source.fetch` job on
a worker: a shallow, single-branch `git clone` through the GitHub connector with a 300 second limit
and the upload size limit applied to the checkout. When it fails, the job's error (Jobs panel, or
`select error from jobs where kind = 'source.fetch' order by created_at desc limit 5`) names the
reason, with secrets removed.

| Symptom | Cause and fix |
|---|---|
| 409 "the GitHub connector is not enabled" | Off in the install or in Settings. Enable it as under "Connectors". Nothing was sent. |
| 502 on connect or pull | The connector could not read the repository or branch (`head`): wrong `owner/name` or branch, or the token lacks `contents:read`. The message is the connector's; use the "Connectors" troubleshooting table. |
| The job fails with a timeout | Egress to the GitHub host from the **worker** pod: check `kubectl -n exiqo get networkpolicy exiqo-connectors-egress -o yaml` lists the host's `cidrs`, the Azure Firewall FQDN rule and private DNS. Cloning runs in the worker, not the api, so test from `deploy/exiqo-worker`. |
| Fails with a size message | The checkout is larger than `upload_max_bytes`. Point at a branch that holds the estate or the exported build only, not a monorepo. |
| 422 on connect | The repository name must be `owner/name` (letters, digits, `_`, `.`, `-`) and the branch a valid branch name. |
| A pull answers `{"changed": false}` | The branch head equals the last fetched commit. Nothing runs. Push the reviewer's commit first. |

The clone runs with a scrubbed environment, no prompts, no submodules, no Git LFS download and no
hooks, and only over https; generated code is never run by it. To retry after a timeout, press
"Pull" (or `POST /api/repos/<id>/pull`); a repository that holds a TIBCO estate creates an
upload and a scan, and one that holds an exported build creates an edit set against that build.
Pushing an iteration's build back (`POST /api/repos/<id>/push`) goes to the branch
`exiqo/<process slug>/iteration-<n>` and opens a pull request when asked.

**Memory and the timeline.** Every memory revision carries its iteration number. The explorer's
timeline groups revisions by iteration and `GET /api/memory/<project id>/revisions?iteration=<n>`
filters them. Requirements and uploaded-log excerpts are searchable memory with citations; after a
Neo4j restore follow "Memory (Neo4j)" and check that the iteration's revision is back in the
timeline.

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
3. For a release that changes the web app, the visual baselines pass on the release commit:
   `make e2e-visual` (main screens, both themes, 1440 and 390 px). A deliberate change of look is
   re-baselined with `make e2e-visual UPDATE=1` and the new screenshots reviewed in the diff.
4. Note the time (UTC) as the Postgres recovery point, and make sure no long build is running
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
