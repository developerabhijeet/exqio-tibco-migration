# ExiQO Migrate iteration loop: re-enter Design, resume from edited code, GitHub as a source

Date: 2026-10-25. Status: approved design, sub-project 6 of the roadmap in
`docs/superpowers/specs/2026-10-24-gmf-container-deploy-design.md`. The user asked for every
remaining choice to be made without further stops; each such choice is recorded as a ruling
(`Ruling: ... — why — cost if wrong`).

## Purpose

Today the seven steps run forward once per process. When a build fails to compile, a parity
scenario differs, or a Terragrunt plan errors, the only way back is to re-run a step with the
same inputs. Nobody can tell the platform "here is the error log from our CI and two new
requirements, design it again", and code that an engineer fixed by hand after downloading the
zip cannot come back: the next build regenerates over it.

This sub-project closes the loop:

- **Re-enter Design** for a process with error logs (collected from the failed Build, Verify or
  Deploy step, and log files people upload) and updated requirements text. The architect sees
  them beside the design, the agents read them, and the gates from G3 on are earned again.
- **Upload a modified zip** of a build downloaded earlier: the platform diffs it against the
  generated baseline, keeps every hand edit, and resumes at the right phase.
- **Connect a GitHub repository** as the source: clone a branch from a GMF-allowlisted GitHub
  host, either a TIBCO estate (a new upload) or an exported build with edits (the same path as a
  modified zip), and pull later edits back from the branch.
- **Workflow memory persists across iterations**: every iteration is numbered, every phase output
  and memory revision carries the iteration, and the history of what changed and why is kept.

User's words (binding): "re-enter the design phase with error logs and updated requirements,
persisted workflow memory"; "upload a modified zip after download edits and resume from that
point, or connect a GitHub repo". All sub-projects are functional, not mocked.

Success criteria:

1. After a failed build of `dealerfunding.DealerFundingRequest`, an architect opens an iteration
   from the Build tab; the iteration lists the failed steps and failing tests; the next Design run
   receives them, G3 to G6 are reset with an audit line naming the iteration, and the designer's
   decisions reference the errors.
2. An engineer downloads build B, changes one generated body and one test, zips and uploads it;
   the platform shows exactly those two files as modified, starts a build from the edited tree,
   keeps both edits byte for byte when the regenerated file is unchanged, and merges them
   (three-way) when the regenerated file moved; a conflict keeps the hand edit and is listed.
3. A repository branch on GMF's GitHub host that holds an exported build with edits produces the
   same result as the zip; a branch holding a TIBCO estate creates an upload and a scan.
4. Memory revisions carry the iteration number; `/memory` can filter the timeline by iteration;
   requirements and uploaded logs are searchable memory with citations.
5. Viewers see iterations and their inputs but cannot open, upload or connect (server-enforced);
   no request leaves for a host not configured by GMF; secrets never appear in logs, memory or
   the database.

## 1. Iterations

### Model

`iterations` (Postgres, new):

| Column | Meaning |
| --- | --- |
| `id`, `project_id` | |
| `n` | 1, 2, 3 … per project; unique (`project_id`, `n`) |
| `process_ids` | JSONB list of the processes re-entered (one or more) |
| `kind` | `initial`, `reentry`, `edits`, `repo` |
| `status` | `open`, `designing`, `building`, `verifying`, `deploying`, `closed`, `abandoned` |
| `resume_phase` | `design`, `build`, `infra` |
| `requirements` | updated requirements text, masked, ≤ 20 000 characters |
| `baseline_build_id` | the build an edit set was made on (edits, repo) |
| `source` | JSONB: `{"upload_id"}` for a zip, `{"repo_id", "branch", "commit"}` for a repo |
| `opened_by`, `opened_at`, `closed_at`, `note` | |

`iteration_inputs` (new): `id`, `iteration_id`, `kind` (`build_failure`, `test_failure`,
`parity_defect`, `plan_error`, `uploaded_log`), `process_id`, `ref` (build id, scenario run id,
plan id, or file name), `title` (≤ 200), `text` (masked excerpt, ≤ 8 000 characters), `sha256`
(uploaded logs), `created_at`. Uploaded log files are stored under
`DATA_DIR/iterations/<iteration id>/<sha256>.log` (masked copy only).

`design_records` and `builds` gain `iteration: int NOT NULL DEFAULT 1` (verify `scenario_runs` and
`plans` belong to a build and take its iteration); `rules` gains `source: str | None` (`"iteration n"`).

Ruling: the iteration counter is per project, not per process — memory revisions are
project-scoped and numbered per project, so a per-project iteration keeps "iteration 3" one
point on one timeline; an iteration names the processes it re-enters — cost if wrong: a project
with many processes advances its counter for each re-entry; the iteration list shows which
process each one touched.

Ruling: iteration 1 exists implicitly (`kind = initial`) and is created as a row the first time
anything reads or writes iterations for a project, so every existing project has history from 1
without a data migration of its phase rows (their `iteration` default is 1) — cost if wrong: none
visible; the row's `opened_at` is the project's `created_at`.

### Rules

- One open iteration per process at a time: opening a new one for a process that is in an open
  iteration closes that one as `abandoned` with the note "superseded by iteration n".
- Every phase job started for a process stamps the process's current iteration (`iteration`
  column, job payload `iteration`), and `memory.ingest` sets `RevisionMeta.iteration` to it
  (project-wide phases use the project's highest iteration). This replaces sub-project 2's
  placeholder (`DesignRecord.version`).
- Status follows the jobs: a design job → `designing`; build → `building`; verify → `verifying`;
  plan → `deploying`; G6 approved for every process of the iteration → `closed`.
- An iteration is never deleted. Its inputs stay readable when it is closed or abandoned.

Ruling: re-entering Design always resets G3 to G6 of the process, even when the design hash does
not move — the architect approves a design in the light of the errors and requirements, and a
gate approved before them says nothing about them — cost if wrong: one extra approval when the
iteration turns out not to need a change; the reset audit line says why.

## 2. Re-entering Design

### Inputs

- **Collected failures** (`collect_failures(process_id)`), from the latest build of the process, its
  scenario runs and its latest plan: failed build steps (name and the last 60 lines of the log), compiler errors
  (`BuildResult.errors`, first 25), failing tests (name, rule/branch/scenario id, message),
  unexplained parity differences (from `scenario_runs` with status `differs` or `error` and their `field_diffs`: scenario, field path, expected, actual, lineage), and the plan
  error summary. Each becomes an `iteration_inputs` row; the person opening the iteration can
  untick any of them.
- **Uploaded logs**: up to 10 files per iteration, each ≤ 5 MB, text only (UTF-8 or Latin-1
  decoded; a NUL byte in the first 8 KB refuses the file as binary), any extension. Stored masked
  (`mask_sensitive`), raw TIBCO XML refused by `guard_text`; the excerpt kept for prompts is the
  lines matching `error|exception|fail|fatal|denied|refused|timeout` (case-insensitive) with two
  lines of context, capped at 8 000 characters, else the last 200 lines.
- **Updated requirements**: free text, ≤ 20 000 characters, masked.

### What reads them

- The Design job (`run_design`) passes an `IterationContext` (inputs and requirements) to
  `design_narrative`, rendered in the designer prompt inside `<source>` (data, not instructions) as
  an "Iteration n" block: what failed, what changed in the requirements. The designer must answer
  each input with a decision or an open question (`Decision.title` starts `Iteration n:`).
- Requirements that change behaviour go through the rules. The designer returns
  `proposed_rules: [{text, rationale}]`; they are stored as analysis rules with the existing
  `status = "draft"`, the next free `rule_key` and `source = "iteration n"`, shown in the Validate
  tab for the SME with an "from iteration n" badge. Approving one
  moves the spec hash and resets G1 to G6 by the existing rule — the platform's one path to a
  behaviour change.
- The Build job's Code Builder, Test Engineer and repair loop receive the same context (compiler
  errors and failing tests from the previous iteration as hints, in `<source>`).
- Prompt-injection text inside a log or the requirements is data: the `<source>` wrapper and the
  existing `wrap_source` escaping apply; a test sends "ignore previous instructions" in a log.

Ruling: requirements never edit the design record directly — the design hash is what code decides
from the IR and approved rules, so a behaviour change must enter as a rule (G1) and a
non-behavioural one as a decision (G3) — cost if wrong: one more approval step for an SME when a
requirement is obviously right.

### API

- `POST /processes/{id}/iterations` (`architect`, `admin`; `sme` when only requirements are given)
  — body `{"requirements": str | null, "include": [input ids from the preview] | null,
  "note": str | null}`; multipart variant with `logs[]`. Creates the iteration, its inputs,
  resets G3–G6 (audit `iteration opened` and `gate reset` with `{"iteration": n}`), queues the
  design job. 409 when a design or build job of the process is running.
- `GET /processes/{id}/iterations/preview` — the collected failures that would be included.
- `GET /projects/{id}/iterations`, `GET /iterations/{id}` (with inputs, timeline of jobs, gates
  and builds of that iteration), `GET /iterations/{id}/inputs/{input_id}` (the masked text).
- `POST /iterations/{id}/close` (`architect`, `admin`) with a note; `abandon` likewise.

## 3. Resuming from edited code

### The baseline in the download

`GET /builds/{id}/download` adds `EXIQO_BASELINE.json` at the zip root: `{"format": 1,
"project_id", "process_id", "build_id", "stack", "build_hash", "design_hash", "iteration",
"files": {"<path>": "<sha256>"}}` for every file in the zip except `EXPORTED_FROM.md` and the
baseline itself.

Ruling: the authoritative baseline is the server's `build_files` rows and the build's own folder,
not the uploaded `EXIQO_BASELINE.json`; the file only identifies the build — a zip is user-edited
data and its manifest could be edited too — cost if wrong: none; a forged id is refused unless it
names a build of the same project.

### Upload and diff

`POST /builds/{build_id}/edits` (multipart `file`, `engineer` or `admin`) or
`POST /projects/{id}/edits` (the baseline read from `EXIQO_BASELINE.json`). The archive is
extracted with `api/app/services/uploads.extract_zip` (same path checks, size limit), a single top
folder is stripped, and every file is compared with the baseline build's `build_files`:

| Class | Rule |
| --- | --- |
| `unchanged` | sha256 equal |
| `modified` | path in baseline, sha256 differs (line endings normalised to `\n` before hashing text files) |
| `added` | path not in baseline (excluding the stack's `download_excluded` folders, `EXPORTED_FROM.md`, `EXIQO_BASELINE.json`) |
| `deleted` | baseline path absent from the zip |

Each changed file is tagged by its baseline origin (`code`, `agent`, `ai`, `infra`, `tests`)
from `build_files` and `GenFile` paths. The result is an `edit_sets` row (`id`, `iteration_id`,
`baseline_build_id`, `files` JSONB with `{path, change, origin, sha256}`, `root_dir` of the
extracted tree, `created_by`) and a new iteration (`kind = edits`). Nothing is built until the
engineer confirms the preview (`POST /edit-sets/{id}/resume`).

### Resume point

- Edits only in production code, tests or infrastructure, and the process's design hash equal to
  the baseline build's → `resume_phase = build` (G3 stays as it is; the new build re-earns G4–G6
  through its own build hash).
- The approved design changed since the baseline build (someone re-designed meanwhile) →
  `resume_phase = design`: the edits are kept on the edit set, the design must be re-approved,
  then the build applies them.
- Edits only under `infra/`, `Dockerfile`, pipelines → `resume_phase = infra`: the new build
  copies the baseline tree, applies the edits and runs from the `infra validate` stage (generate,
  tests, agents, restore, build, test and repair are recorded as skipped with "infrastructure-only
  edits").

### Building from edits

A build with `edit_set_id` runs the normal pipeline with these changes:

1. **Generate** writes the fresh generation into the build folder as usual.
2. **Merge**: for each `modified` file, a three-way merge with base = the baseline build's file,
   ours = the fresh generation, theirs = the edited file (`git merge-file -p --diff3`; `git` is
   in the platform image). Fresh == base → the edited file verbatim. A conflict → the edited file
   verbatim, recorded as `conflict` with the merge output kept in `.exiqo/conflicts/<path>.merge`
   for review. `added` files are copied; `deleted` files are removed unless the fresh generation
   reintroduces a required scaffold (`origin = code` file whose path is in the solution layout's
   project files), which is kept and listed as `restored`.
3. Every merged, added or conflicting file gets `origin = "hand"` in `build_files` and a
   `hand_paths` set the agents treat as protected: the Code Builder does not fill bodies in a
   `hand` file, the Test Engineer does not rewrite a `hand` test, the repair loops refuse a diff
   touching a `hand` path (the same mechanism as the existing test-path refusal), and the review
   panel still reviews them.
4. The rest of the pipeline (restore, build, format, test, repair, infra, metrics) runs as today.
   The Build log has a "hand edits" stage listing `kept`, `merged`, `conflict`, `added`, `deleted`,
   `restored` per path.

Ruling: three-way merge with the hand edit winning conflicts, not "hand edits replace generation"
nor "generation wins" — the engineer's edit is the newest human decision, and the merge keeps every
generated improvement that did not touch the same lines — cost if wrong: a conflict hides a
generated fix behind the hand version; the conflict list and the `.merge` file show both.

Ruling: `hand` is a new `BuildFile.origin` value and a new memory `CodeFile.origin` value — the
lineage must say a person wrote this, and agents must leave it alone — cost if wrong: two enum
values to rename.

## 4. GitHub repository as a source

### Connection

Sub-project 7 owns connector configuration (host, credentials, allowlist, audit). This
sub-project consumes its GitHub connector: `GitHubConnector.clone(repo, branch, dest)`,
`list_branches(repo)`, `head(repo, branch)`, `push_branch(...)` and its host allowlist.

Ruling: the repository source uses the sub-project 7 GitHub connector, so the repository tasks of
this plan run after sub-project 7's connector framework and GitHub tasks — one credential, one
allowlist and one audit path for GitHub instead of two — cost if wrong: those tasks wait for
sub-project 7; everything else here does not.

`project_repos` (new): `id`, `project_id`, `owner`, `name`, `default_branch`, `purpose`
(`estate`, `build`), `process_id` (for `build`), `last_commit`, `connected_by`, `connected_at`.
`POST /projects/{id}/repos` (`engineer`, `admin`; `sme` for `estate`) with `{"repo": "owner/name",
"branch"}` validates that the connector is enabled and can read the repo
(`GitHubConnector.head`), stores the row and queues `source.fetch`.

### Fetch and classify

`source.fetch` (worker job): shallow clone of the branch (`--depth 1 --single-branch`, timeout
300 s, size limit `upload_max_bytes` applied to the checkout) into
`DATA_DIR/repos/<repo id>/<commit>`; then:

- `EXIQO_BASELINE.json` at the root (or under one top folder) → an edit set against that build,
  exactly as section 3 (`kind = repo`, `source = {repo_id, branch, commit}`);
- otherwise → an `Upload` (`source_kind = "repo"`, `source_name = "<owner>/<name>@<branch>:<short commit>"`)
  copied with `uploads.copy_folder` (symlinks skipped, `.git` excluded), then the normal scan.

`POST /repos/{id}/pull` fetches the branch again; an unchanged commit answers 200 `{"changed":
false}`; a new commit runs `source.fetch`.

### Pushing back

Pushing a build (generated or from edits) to a branch and opening a pull request is sub-project 7's
"push to GitHub" action; for a repo with `purpose = build` the default target is the connected
repo, and the branch name is `exiqo/<process slug>/iteration-<n>`.

## 5. Workflow memory across iterations

- Phase revisions carry `iteration` (section 1). The projectors add:
  - requirements → `Chunk` kind `requirement`, uid `chunk:<ir process id>:requirement:<n>`, `ABOUT`
    the process;
  - each iteration input → `Error` (kinds `build`, `test`, `parity`, `deploy` as today, plus
    `uploaded_log`), uid `error:iteration:<ir process id>:<input id>`, `ABOUT` the process and
    `FAILED_AT` the code file or method when the input names one;
  - hand edits → `CodeFile.origin = hand` on the new build's version; the edit set's file list as a
    `Memory` item kind `note` (`source = "edit set"`), `ABOUT` each changed `CodeFile`.
- A new memory phase value `iteration` (`Phase` literal) for the revision written when an
  iteration opens (inputs and requirements), so "what did iteration 3 bring in" is one revision.
- `/memory` revisions and timeline accept `iteration=` and show an iteration column; the memory
  explorer's timeline groups revisions by iteration.
- Agents' memory retrieval adds the current iteration's chunks and errors first (same floors).

Ruling: `requirement` and `uploaded_log` are new property values on existing labels (`Chunk.kind`,
`Error.kind`), not new labels — sub-project 2's label set is closed and its Cypher is rendered from
it — cost if wrong: a query by kind instead of by label.

## 6. Web

- **Iterations strip** under the stepper: `Iteration n · <status>` with a menu of earlier
  iterations; picking one shows that iteration's design, builds, parity and plan read-only.
- **Re-enter Design** button on the Build, Verify and Deploy tabs when the latest run failed, and
  on the Design tab always (architect/admin): a dialog with the collected failures (checkboxes),
  a log upload area, a requirements editor (prefilled with the previous iteration's text), and
  "Open iteration n+1".
- **Upload edited build** on the Build tab (engineer/admin): drop a zip; the preview table lists
  each changed file with its class, origin and a diff link (Monaco diff of baseline vs edited);
  "Resume from <phase>".
- **Repository** panel on the Upload tab (estate) and the Build tab (build): connect `owner/name`,
  pick a branch (from `list_branches`), see the last commit, "Pull latest". Shown only when the
  GitHub connector is enabled.
- The Build tab marks `hand` files with a badge, the conflict list, and the "hand edits" stage.
- Viewers see everything, buttons disabled with "viewer is read only".

## 7. Security and limits

- Upload limits: zip as today (`upload_max_bytes`), logs 10 × 5 MB, requirements 20 000
  characters; refusals are 413/422 with the reason.
- All stored text is masked; nothing from a log is executed or rendered as HTML (shown in a `pre`).
- Clones run in the worker with the scrubbed child environment, `GIT_TERMINAL_PROMPT=0`,
  `GIT_CONFIG_NOSYSTEM=1`, `protocol.allow=never` except `https` (and `http` for loopback in
  tests), no submodules, no LFS smudge (`GIT_LFS_SKIP_SMUDGE=1`), hooks disabled
  (`core.hooksPath=/dev/null`).
- Roles: open/close an iteration `architect`/`admin` (`sme` requirements-only); edits upload and
  resume `engineer`/`admin`; repo connect `engineer`/`admin` (`sme` for estate repos); viewers
  read. Enforced in the API for both auth modes.
- Audit lines: `iteration opened`, `iteration closed`, `iteration abandoned`, `edit set uploaded`,
  `edit set resumed`, `repo connected`, `repo pulled`, each with the actor and ids.

## 8. Testing

- Unit: failure collection from stored build/verify/plan rows; log excerpting and binary refusal;
  diff classification with CRLF files; resume-phase decision table; three-way merge outcomes
  (`kept`, `merged`, `conflict`, `restored`); protected `hand` paths in builder, test engineer
  and both repair loops.
- API (Postgres): open iteration resets G3–G6 and queues design with the context; viewer 403
  (oidc); sme requirements-only; 409 while a build runs; edits round trip on a real download
  (download → modify → upload → preview → resume → build files carry `hand`); a forged baseline
  of another project refused.
- Repo: against the sub-project 7 fake GitHub (smart HTTP via `git http-backend`) — estate branch
  → upload and scan; build branch → edit set; pull with no change; pull with a new commit.
- Memory: the iteration revision, requirement chunk and uploaded-log errors; revisions filtered by
  iteration.
- E2E: architect re-enters design from a failed build; engineer uploads an edited zip and sees
  the preview; viewer sees disabled controls.
- Demo check: `iteration` step — open iteration 2 with a requirement on the first target, design
  runs, G3 is pending again, memory has an `iteration` revision with iteration 2.

## Out of scope

- Merging edits across stacks (an edit set applies to builds of its baseline's stack only), GitLab
  or Bitbucket sources, webhooks from GitHub (pull is explicit), automatic re-entry without a
  person, editing code in the browser.
