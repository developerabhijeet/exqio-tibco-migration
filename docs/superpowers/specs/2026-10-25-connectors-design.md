# ExiQO Migrate connectors: GitHub, Atlassian (Confluence, Jira) and Figma

Date: 2026-10-25. Status: approved design, sub-project 7 of the roadmap in
`docs/superpowers/specs/2026-10-24-gmf-container-deploy-design.md`. The user asked for every
remaining choice to be made without further stops; each such choice is recorded as a ruling
(`Ruling: ... — why — cost if wrong`).

## Purpose

The platform's outputs stop at its own screens and a zip. GMF's engineers work in GitHub, its
architects in Confluence, its delivery in Jira, its designers in Figma. This sub-project adds a
Settings → Connectors tab and real integrations:

- **GitHub**: push a build (generated or from edits) to a branch and open a pull request; act as
  a source for sub-project 6 (clone a branch, list branches, read the head commit).
- **Confluence**: publish a process's architecture document (design narrative, decisions, open
  questions, mapping, configuration, messaging, sequence, data model, gate record) as a page,
  updated in place on re-publish.
- **Jira**: create issues from findings and open questions (and from unexplained parity
  differences), linked back so a second publish updates instead of duplicating.
- **Figma**: read frames of a GMF Figma file and attach them to a process as UI design
  references, seen by the architect and fed to the designer.

User's words (binding): "Settings connectors tab for GitHub, Atlassian and Figma"; connectors were
first allowed to be mocked, then the user required everything functional: connectors really call
the services when credentials are configured; tests use local fakes. In GMF the hosts are
GMF-controlled and allowlisted (GitHub Enterprise, Atlassian Data Center or cloud URLs configured
by GMF), off by default, credentials through the platform's Key Vault file-mounted secret
mechanism (`<NAME>_FILE`).

Success criteria:

1. With a connector enabled in Helm and in Settings, its base URL on the install's allowlist and
   its secret mounted, "Test connection" answers with the account or site it reached; each
   publish action really creates or updates the remote object, and the link appears in the UI.
2. With a connector disabled (the default), nothing calls out: its actions are hidden in the UI
   and refused by the API (409), and no NetworkPolicy or allowlist entry exists for it.
3. A base URL whose host is not on the install's allowlist cannot be saved (422), and the HTTP
   client refuses any request or redirect to another host.
4. No credential value appears in the database, a log, a job payload, an audit line, a pod spec
   or the browser; Settings shows only whether the secret file is present and its Key Vault name.
5. Every test and publish is audited (actor, connector, operation, target, outcome).
6. The test suite runs every connector against local fake servers that speak the real APIs'
   wire formats (including git smart HTTP for pushes); `make egress-audit` passes with no
   GitHub, Atlassian or Figma host in code or bundle.

## 1. Configuration model

Two layers, both required for a connector to work.

**Install layer (Helm, GMF operators)** — the hard ceiling:

```yaml
connectors:
  github:
    enabled: false
    baseUrl: ""          # e.g. https://github.gmf.example.com (GHES) or https://api.<sub>.ghe.com
    apiUrl: ""           # optional; default <baseUrl>/api/v3 for GHES
    allowedHosts: []     # hosts the client may reach, e.g. [github.gmf.example.com]
    cidrs: []            # NetworkPolicy egress (private endpoint / firewall subnet), port 443
    commitEmail: ""      # commit author email; empty -> exiqo-migrate@invalid
  atlassian:
    enabled: false
    flavor: datacenter   # or cloud
    confluenceUrl: ""    # GMF's Data Center or cloud Confluence base URL
    jiraUrl: ""          # GMF's Jira base URL
    allowedHosts: []
    cidrs: []
  figma:
    enabled: false
    apiUrl: ""           # Figma's REST base URL, when GMF allows its Figma organisation
    webUrl: ""           # base for frame links shown to people; empty -> names without links
    imageHosts: []       # optional: hosts of rendered images (off when empty)
    allowedHosts: []
    cidrs: []
keyVault:
  secrets:
    githubToken: ""          # GITHUB_TOKEN (PAT or GitHub App installation token source)
    atlassianToken: ""       # ATLASSIAN_TOKEN (Data Center PAT, or cloud API token)
    atlassianUser: ""        # ATLASSIAN_USER (cloud only: the account email for basic auth)
    figmaToken: ""           # FIGMA_TOKEN (personal access token of a GMF service account)
```

The ConfigMap carries `CONNECTORS` (JSON of the non-secret fields per connector, after schema
validation) and the secret rows join `exiqo.secretTable` as `GITHUB_TOKEN_FILE`,
`ATLASSIAN_TOKEN_FILE`, `ATLASSIAN_USER_FILE`, `FIGMA_TOKEN_FILE` (`when` the connector is
enabled; api and worker roles). `api/app/secrets.SECRET_FILE_FIELDS` gains `github_token`,
`atlassian_token`, `atlassian_user`, `figma_token`.

**Application layer (Settings → Connectors, admin)** — within the ceiling: per connector an
`enabled` toggle (cannot be on when the install layer is off), the base URLs (must have a host in
`allowedHosts`, scheme `https`), and connector defaults: GitHub `default_owner`; Confluence
`space_key`, `parent_page_id`; Jira `project_key`, `issue_type` (default `Task`), `labels`
(default `["exiqo"]`); Figma `file_keys` (list of allowed file keys, optional). Stored in the
`settings` table under `connectors` (no secret fields exist in this model).

Ruling: credentials only from Key Vault mounted files, never entered in the UI — that is the
platform's secret mechanism and the user's requirement; a UI-entered token would need storage the
platform otherwise avoids — cost if wrong: rotating a token is a Key Vault change plus a pod
restart (the CSI driver's rotation also works, see below).

Ruling: Settings shows the Key Vault object name of each connector secret (from the Helm values,
exposed read-only as `CONNECTOR_SECRET_NAMES`) and whether its file is present and non-empty — the
admin needs to know which secret to rotate, never its value — cost if wrong: none.

Ruling: secret files are read on every connector call (not cached at start-up), so the Secrets
Store CSI driver's rotation reaches the connector without a restart — cost if wrong: one small
file read per call.

Ruling: no defaults point at public SaaS hosts (`api.github.com`, `*.atlassian.net`,
`api.figma.com` appear nowhere in code); every URL comes from configuration — GMF decides which
services are GMF-controlled, and the egress audit must stay clean — cost if wrong: the install
guide has to name each value, which it does.

## 2. Connector framework (`api/app/connectors/`)

- `base.py`: `ConnectorKind = Literal["github", "atlassian", "figma"]`; `ConnectorConfig`
  (merged install + application layer); `class Connector(Protocol)`: `kind`, `enabled`,
  `async test() -> ConnectionTest`; exceptions `ConnectorDisabled`, `ConnectorNotConfigured`,
  `ConnectorError(message, status=None)`, `HostNotAllowed`.
  `ConnectionTest(BaseModel)`: `ok`, `account: str | None`, `site: str | None`, `scopes: list[str]`,
  `latency_ms`, `message`.
- `http.py`: `ConnectorClient` over `httpx.AsyncClient`: `https` only (`http` to `127.0.0.1` only
  when `CONNECTORS_ALLOW_INSECURE_LOOPBACK=true`, a test setting the chart schema rejects); every
  request's host checked against `allowedHosts`; redirects followed manually up to 3, each target
  re-checked (an off-host redirect raises `HostNotAllowed`); timeouts 10 s connect, 30 s read;
  retries on 429/502/503/504 with `Retry-After` honoured, at most 3; `User-Agent: <BRAND_NAME>-Migrate`;
  the `Authorization` header is added by the connector and redacted in every log and exception
  text; responses over 20 MB refused; JSON only where the API is JSON.
- `registry.py`: `get_connector(kind, *, settings, stored) -> Connector` and
  `async connector_for(session, settings, kind) -> Connector` (loads the stored layer); `connectors_view(...)` for
  Settings and `GET /connectors` (enabled flags only, for any signed-in user, so the UI knows what
  to show).
- `audit.py`: `audit_connector(session, actor, kind, op, target, outcome, detail)` writes
  `audit_log` with `action = f"connector {kind} {op}"`; `detail` never includes a header, token or
  request body; it holds ids, URLs and counts.
- `links.py` + table `connector_links`: `id`, `project_id`, `process_id`, `kind`, `object_type`
  (`branch`, `pull_request`, `confluence_page`, `jira_issue`, `figma_frame`), `local_ref` (build id,
  finding key, open-question index, parity diff id, frame node id), `remote_id`, `remote_url`,
  `remote_version` (Confluence page version), `created_by`, `created_at`, `updated_at`;
  `UniqueConstraint(kind, object_type, process_id, local_ref)`.

Every publish runs as a worker job (`connector.github.push`, `connector.confluence.publish`,
`connector.jira.issues`, `connector.figma.sync`), so long calls do not hold an API request and
egress is from the worker's policy; tests (`POST /connectors/{kind}/test`) run inline in the API
with a 15 s overall timeout.

Ruling: both api and worker pods get the connector egress (the test button runs in the API) —
moving the test into a job would make a two-second check a queued job with polling — cost if
wrong: one NetworkPolicy rule fewer if GMF prefers worker-only egress (a values flag
`connectors.testFromWorker` is not offered; the policy names both roles).

## 3. GitHub

Supports GitHub Enterprise Server (REST at `<baseUrl>/api/v3`, git at `<baseUrl>/<owner>/<repo>.git`)
and GitHub Enterprise Cloud with data residency or github.com (REST at `apiUrl`), whichever GMF
configures. Authentication: a token (`GITHUB_TOKEN_FILE`) sent as `Authorization: Bearer`.

Operations:

- `test()`: `GET /user` (account login) and `GET /rate_limit`; for a fine-grained token the
  `X-OAuth-Scopes`/`X-Accepted-GitHub-Permissions` headers are reported when present.
- `head(repo, branch) -> sha`: `GET /repos/{o}/{r}/branches/{branch}`.
- `list_branches(repo) -> list[str]`: paginated, max 1 000.
- `clone(repo, branch, dest, depth=1, timeout_s=300) -> sha`: `git clone --depth 1 --single-branch
  --branch <b> --no-tags` with credentials passed by environment
  (`GIT_CONFIG_COUNT=1`, `GIT_CONFIG_KEY_0=http.extraHeader`, `GIT_CONFIG_VALUE_0=Authorization: Bearer <t>`)
  in the scrubbed child environment, plus `GIT_TERMINAL_PROMPT=0`, `GIT_CONFIG_NOSYSTEM=1`,
  `GIT_LFS_SKIP_SMUDGE=1`, `-c protocol.allow=never -c protocol.https.allow=always`,
  `-c core.hooksPath=/dev/null`, `-c submodule.recurse=false`.
- `push_build(build_root, repo, branch, message, base_branch=None) -> sha`: a fresh repository in
  a temp folder; when `base_branch` is given, fetch it shallowly and commit on top so the PR diff
  shows only the build's changes; copy the build tree (the stack's `download_excluded` removed,
  `EXIQO_BASELINE.json` and `EXPORTED_FROM.md` added as in the download), commit with
  `user.name = "<BRAND_NAME> Migrate"` and `user.email = "exiqo-migrate@invalid"`, push `--force-with-lease` to `exiqo/...` branches only.
  Git authenticates with `Authorization: Basic base64(x-access-token:<token>)` (the form GitHub
  accepts for a PAT and an App installation token alike; REST stays `Bearer`), sent as an
  `http.<baseUrl>/.extraHeader` scoped to the configured host. A re-push of the same build
  replaces its `exiqo/...` branch (the lease is only against the head read just before the push):
  commits a reviewer adds to that branch are overwritten, so review feedback goes in PR comments
  or on another branch. An empty repository gets the build as its first commit and no pull request.
- `open_pull_request(repo, head, base, title, body) -> {number, url}`: `POST /repos/{o}/{r}/pulls`;
  an existing open PR for the same head is returned (`GET /pulls?head=o:b&state=open`) and its body
  updated.

Ruling: pushes use the `git` CLI over HTTPS, not the REST Git Data API — a generated build is
hundreds of files, which is hundreds of REST calls, while git is already in the image and speaks
the same allowlisted host — cost if wrong: the REST path would be a second implementation behind
the same `push_build`.

Ruling: the platform only pushes to branches under `exiqo/` and never to the default or a
protected branch; changes reach `main` only through a PR that GMF's own reviewers merge —
cost if wrong: none; a team that wants direct pushes merges the PR.

Ruling: the commit email is `exiqo-migrate@invalid` (RFC 2606 reserved), so no R Systems or vendor
address appears and the egress audit ignores it — cost if wrong: GMF can set
`connectors.github.commitEmail` in values (offered, default empty → the reserved address).

Actions: Build tab "Push to GitHub" (`engineer`, `admin`): repository (defaults to the process's
connected repo from sub-project 6, or `default_owner/<app slug>`), branch
`exiqo/<process slug>/build-<short build id>` (or `iteration-<n>` from sub-project 6), "open pull
request" checkbox (default on, base = repo default branch). Result: links to branch, commit and PR
stored in `connector_links` and shown on the build.

## 4. Confluence

Flavors: `datacenter` uses REST v1 `/rest/api/content` (create `POST`, update `PUT` with
`version.number + 1`, attachments `POST /rest/api/content/{id}/child/attachment` with
`X-Atlassian-Token: no-check`), authentication `Bearer <PAT>`; `cloud` uses REST v2
`/wiki/api/v2/pages` (create, update with version) and v1 attachments, authentication basic with
`ATLASSIAN_USER:ATLASSIAN_TOKEN`.

Ruling: two small flavor adapters behind one `ConfluencePublisher` — Atlassian Cloud has moved page
CRUD to v2 while Data Center has only v1, and GMF may run either — cost if wrong: one adapter is
dead code for a given install.

The document (`render_architecture_page(process, design, gates, build, stack) -> str`) is
Confluence storage format (XHTML), generated by code from stored data only: title
`<App> · <Process> · Architecture` (unique per space); sections Overview (narrative), Target stack
and solution layout, Decisions (table), Open questions (table with verdicts), Mapping (activity →
file/class/method, origin), Contracts, Configuration (keys; secrets named, values never),
Messaging, Resilience, Sequence (table), Data model (tables and columns from the design views),
Gate record (G1–G6 state, actor, time, hashes), Figma references (links, section 6), Source
(TIBCO path, spec/design/build hashes, ExiQO iteration). Diagrams are tables, and the design
record JSON is attached as `design-<design hash 12>.json`. Every string is XML-escaped; nothing
from a model is inserted as markup.

Publish (`architect`, `admin`): creates the page under `parent_page_id` in `space_key`, or updates
the linked page (version + 1, a "republished from design vN" version message). A 409 version
conflict refetches the version once and retries.

Ruling (X20, one deleted-object policy for every connector): a linked page that answers 404 marks
the link stale and the publish is refused (audited `refused`); a stale link is refused before any
request. Only an explicit "Create again" (`{create_again: true}`, the Design tab's opt-in) makes a
new page; it first tries the old page id (it may have been restored). Without a link the page is
created, but never over a page of the same title already in the space: that is refused, so a page
a person wrote is never adopted or overwritten. Publishes of a process are queued once
(`dedupe_key`) and run one at a time (advisory lock `confluence:<process id>`) — cost if wrong: a
lost create answer leaves a page the platform does not link; the publish then says so and a person
renames or links it.

Ruling: tables instead of rendered diagrams — Confluence storage format renders tables natively,
a server-side diagram renderer would be a new dependency and a picture of data an editor cannot
search — cost if wrong: add an SVG attachment later from the web's existing diagram components.

## 5. Jira

Both flavors use REST v2 (`/rest/api/2/issue`, `/rest/api/2/myself`, `/rest/api/2/issue/{key}`),
which Cloud and Data Center both serve, with the same authentication as Confluence (Jira and
Confluence share the Atlassian credential; separate URLs).

Sources (`architect`, `sme`, `qa`, `admin` — any role that owns the item's gate, or admin):

| Source | Summary | Labels |
| --- | --- | --- |
| Finding (decision `fix` or undecided) | `[<Process>] <finding title>` | `exiqo`, `finding`, severity |
| Open question (undecided) | `[<Process>] Open question: <question>` (≤ 255 chars) | `exiqo`, `open-question` |
| Unexplained parity difference | `[<Process>] Parity: <scenario> <field path>` | `exiqo`, `parity` |

Description (wiki markup, escaped): the text, recommendation or expected/actual, the TIBCO
citation (activity, kind, text), and a back-reference line `ExiQO: project <id>, process <ir id>,
<local ref>` (no URL, the platform has no link back). Selected items are created in one job;
each created issue is recorded in `connector_links`; a re-publish of a linked item updates
summary and description (`PUT`), never creates a duplicate. When the remote issue is gone (404),
the link is marked stale and a new issue is created only on explicit "create again".

Ruling: v2 for both flavors, plain wiki-markup descriptions — v3's ADF exists only in Cloud, and
v2 is still served there — cost if wrong: an ADF renderer if Atlassian retires v2 on Cloud.

Ruling: issues are not synced back (no status pull, no webhooks) — the platform has no inbound
link from GMF systems, and a gate is decided in the platform — cost if wrong: people look at Jira
for status.

## 6. Figma

Figma's REST API is read-oriented: files, nodes, images, comments; it cannot create design files.
"Publish UI designs" therefore means, realistically: bring the UI designs that exist in GMF's
Figma into the migration as references, and publish their links onward (Confluence page section,
PR body).

Operations (`FIGMA_TOKEN_FILE`, header `X-Figma-Token`):

- `test()`: `GET /v1/me` (handle, email domain only shown). Frame links use `webUrl`.
- `frames(file_key) -> list[Frame]`: `GET /v1/files/{key}?depth=2` → top-level frames per page
  (`node_id`, `name`, `page`, `width`, `height`), only for keys in `file_keys` when that list is set.
- `frame_text(file_key, node_ids) -> dict[node_id, str]`: `GET /v1/files/{key}/nodes?ids=…` → the
  text of `TEXT` nodes in reading order, ≤ 4 000 characters per frame, masked.
- `render(file_key, node_ids) -> dict[node_id, url]`: `GET /v1/images/{key}?ids=…&format=png&scale=1`;
  the PNG is downloaded only when the returned URL's host is in `imageHosts` (≤ 5 MB each), stored
  under `DATA_DIR/figma/<project>/<file key>/<node id>.png`, served by the API to signed-in users.

Attach (`architect`, `admin`): pick a file key, see its frames, tick frames, attach to a process.
Stored as `connector_links` (`object_type = figma_frame`) plus `design_references` JSON on the
process's next design run input: `{file_key, node_id, name, url, text}`. The designer prompt
receives frame names and text inside `<source>` for processes with a REST or HTTP starter; the
Design tab shows thumbnails (when rendered) or names with links; the Confluence page lists them.

Ruling: thumbnails are optional and off unless GMF allowlists the image host — Figma serves
rendered images from a cloud storage host separate from the API, which GMF may not want to open —
cost if wrong: names, text and links still work; only the picture is missing.

## 7. Network, secrets and audit

- Egress: `egress.fqdns` (shown in About as `EGRESS_ALLOWLIST`) gains each enabled connector's
  `allowedHosts` (and Figma `imageHosts`); NetworkPolicy `exiqo-connectors-egress` allows api and
  worker pods to the union of enabled connectors' `cidrs` on 443 (no rule rendered when none is
  enabled; the schema requires each enabled connector's own `cidrs`: `egress.allowCidrs` does not
  substitute for them).
- Schema: each `*Url` must be `https://`, its host in that connector's `allowedHosts` (the app
  also checks the install URLs at start-up: one off its allowlist is logged as an error and that
  connector is not ready, a 409 on every action); enabled
  connectors require their Key Vault secret names (Atlassian cloud also `atlassianUser`);
  `CONNECTORS_ALLOW_INSECURE_LOOPBACK` cannot be set.
- `deploy/egress-allowlist.txt` gains nothing (no host in code); the reserved commit email is
  ignored by the audit as an RFC 2606 name.
- The worker's child environment for git gets the token only through `GIT_CONFIG_VALUE_0` for the
  one command; it is never written to `.git/config` or a file.
- Audit actions: `connector <kind> test`, `connector github push`, `connector github pull-request`,
  `connector confluence publish`, `connector jira create`, `connector jira update`,
  `connector figma sync`, `connector settings changed` (fields changed, not values of secrets,
  which do not exist here). Every refusal is audited `refused`, including those a route answers
  (422 or 409) before anything is queued (ruling X20).
- Secrets are read from `<NAME>_FILE` on every call. The plain `<NAME>` variable is a local
  development fallback, read only with `CONNECTORS_ALLOW_INSECURE_LOOPBACK` on.

## 8. API

- `GET /settings/connectors` (signed in; admin sees everything, others the enabled flags) —
  per connector: `install_enabled`, `enabled`, URLs, `allowed_hosts`, defaults, `secret_names`,
  `secrets_present` (bool per secret), `last_test` (`ConnectionTest`, time, actor).
- `PUT /settings/connectors` (admin) — partial update; 422 with the reason (`host x not in
  allowedHosts`, `https required`, `enable it in the install first (connectors.<kind>.enabled)`).
- `POST /connectors/{kind}/test` (admin) — `ConnectionTest`, audited.
- `GET /connectors` — `{github: {enabled}, atlassian: {enabled, confluence: bool, jira: bool}, figma: {enabled}}`.
- `POST /builds/{id}/github/push` (engineer, admin) `{repo, branch?, open_pr=true, base?}` → 202 job;
  409 while the same build is queued or running for the same repository and branch
  (`dedupe_key` `github:<build>:<repo>:<branch>`, also the job's advisory lock).
- `POST /processes/{id}/confluence/publish` (architect, admin) `{create_again=false}` (optional
  body) → 202 job; `GET` → link.
- `POST /processes/{id}/jira/issues` (architect, sme, qa, admin) `{items: [{type, ref}]}` → 202 job.
- `GET /figma/files/{key}/frames` (architect, admin), `POST /processes/{id}/figma/frames`
  `{file_key, node_ids}` → 202 job; `GET /processes/{id}/figma/frames`;
  `GET /figma/frames/{link id}/image` → PNG (signed in).
- `GET /processes/{id}/links` → every `connector_links` row of the process (signed in).
- Disabled connector → 409 `"the <Name> connector is not enabled"`; missing secret → 409
  `"the <Name> connector has no credential: mount <secret name>"`.

## 9. Web

- Settings becomes tabbed: **General** (today's panels) and **Connectors**. Connectors shows one
  card per connector: install state ("Enabled by the install" / "Off in this install: set
  connectors.<kind>.enabled"), the toggle, URL fields, defaults, secret names with present/missing
  pills, "Test connection" with the result (account, site, latency) and the last test time.
  Non-admins see the cards read-only.
- Actions where the work is: Build tab "Push to GitHub" (dialog, result links); Design tab
  "Publish to Confluence" (link to the page, last published version) and "Figma references"
  (browse frames, attach, thumbnails); Validate/Design/Verify "Create Jira issues" from selected
  findings, open questions, parity differences, with the issue key chips once created.
- Hidden when the connector is disabled; disabled with the role reason when the persona lacks the
  role; viewers see links but no actions.

## 10. Testing

- Fakes in `api/tests/fakes/`: `github.py` (REST subset with real response shapes: `/user`,
  `/rate_limit`, branches, pulls; git smart HTTP via `git http-backend` behind a small WSGI adapter,
  checking the `Authorization` header), `confluence.py` (v1 and v2 page create/update/version
  conflict, attachments), `jira.py` (v2 issue create/update/get, 404), `figma.py` (`/v1/me`,
  files, nodes, images returning a URL on a second fake host). Each runs as a real local HTTP
  server on `127.0.0.1:<free port>` (uvicorn in a thread), and records requests for assertions.
- Unit: host allowlist and redirect refusal, retries with `Retry-After`, header redaction in
  errors, storage-format escaping, Jira description escaping, frame text extraction, settings
  validation.
- API: every operation against the fakes; disabled → 409 and zero requests to the fake; viewer
  403 (oidc); audit lines; idempotent republish (one page, version 2; one issue updated).
- Helm: values → ConfigMap `CONNECTORS`, secret file env, NetworkPolicy present only when enabled,
  schema refusals.
- E2E: admin enables GitHub in Settings against the fake (the e2e stack starts the fakes), tests
  the connection, pushes a build, sees the PR link; viewer sees read-only cards.
- `make egress-audit` passes; a unit test greps `api/` and `engine/` for the public SaaS hosts.

## Out of scope

- GitLab, Bitbucket, Azure DevOps Repos; Jira status sync or webhooks; Figma write operations
  (none exist) or FigJam; OAuth app flows (tokens come from Key Vault); per-user credentials
  (connectors act as one GMF service identity, and the audit records the person who asked).
