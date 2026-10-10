# Demo runbook (internal)

The demo runs on the synthetic GM Financial estate in `samples/synthetic-gmf/`: two applications,
eleven processes, built from the BW6 file layout and dialect. It is synthetic and is called
synthetic on screen (a banner on every tab) and in everything we say about it. Expected outputs
are derived from the TIBCO model by the parity evaluator; no TIBCO engine runs here, so parity is
always presented as derived from the TIBCO model.

The five users in the top bar user menu stand in for the gate owners: `sme.vicky` (business SME,
G1 and G2), `architect.lee` (G3), `engineer.ravi` (G4), `qa.priya` (G5) and `ops.sam` (G6). A gate
decision is recorded with the user who made it; agents cannot decide gates.

## The five-minute flow: seven steps, seven tabs

Vicky's flow follows one workflow, `DealerFundingRequest` in the Dealer Funding domain, through
the seven tabs of the stepper. Steps 1 to 4 run live in about two and a half minutes with the
fake provider (longer with a real model). Build, Verify and Deploy take longer than the slot
allows when the build restores, compiles and runs its tests, so for steps 5 to 7 switch the
project selector to the rehearsal project (the one `make demo-check` or your rehearsal left
behind, already built and verified) and narrate from there. Every stepper step has an Evidence
drawer that names the artifact behind its status; open it once early so the audience knows it is
there.

**1. Upload.** Act as `sme.vicky`. Click New project, name it, and drop `samples/synthetic-gmf.zip`
on the drop zone. The file tree fills with counts by type (`.bwp`, `.bwm`, `.xsd`, `.substvar`,
shared resources, SQL). Click Scan. The progress log streams until "stored 2 applications"; the
counters show 11 processes, 0 unknown activity types and 0 unknown elements, and the parse
report lists every process with its dialect, activity count, complexity band and status. Point at
the zero counters: nothing unknown or unexplained.

**2. Discover.** Open the Discover tab. The domain dropdown was built by code from module
boundaries, shared tables and queues, calls and names; the business names were proposed by the
model with the evidence it used. Pick Dealer Funding, then `DealerFundingRequest`. The canvas
shows the activities and the conditional links with their expressions. Click the `LookupDealer`
step and the XML tab: the source opens at the SQL line inside the highlighted element. The I/O
card shows the trigger, the request and response contracts and the tables read and written.

**3. Validate.** Open Validate and click Run analysis. The log tags each line `code` or `ai`.
Both banners turn green: "Citations verified n of n" and "Coverage n of n steps, branches and
mappings". Approve the rule cards (a citation chip jumps back to the canvas step), then Approve G1.
In the findings, click Fix on the read-then-write finding and Keep behavior, with a note, on the
LIKE match; any other finding gets Ask business. Approve G2. The gate cards show `sme.vicky` and
the spec hash they are bound to.

**4. Design.** Switch to `architect.lee` and open Design. Click Generate design. The design record
shows the solution layout, the mapping table (each TIBCO step to a C# file, class and method,
with the rule and finding IDs it carries; the LIKE query row has its finding badge), the
configuration mapping, the sequence diagram rendered from the model, and the expected
differences from findings decided Fix. Approve G3; the card shows the design hash. Export PDF
is there if someone asks for the document.

**5. Build.** Switch to `engineer.ravi` and open Build (on the rehearsal project). The build log
shows the order of work: scaffold, tests first, the builder filling method bodies, the reviewer,
`dotnet restore`, `build`, `format`, `test`, then the infrastructure checks. The source and
target explorer puts the TIBCO XML beside the C# it became; clicking
`DealerFundingRequestRepository.cs` highlights the step it implements. The test list shows one
test per approved rule and per branch with its outcome, and the metrics radar has its formulas
one click away. G4 is enabled only when the build compiled and every test passed.

**6. Verify.** Switch to `qa.priya` and open Verify. Start service starts the generated .NET
service; the Swagger frame loads and Get dev token returns a development JWT. Generate scenarios,
then Run verify. The parity table reads "Expected output derived from the TIBCO model" and shows,
per scenario and field, match, defect, intended or noise. Intended differences are the fields
tied to the findings decided Fix in step 3; they show "not confirmed" because no TIBCO sandbox
ran. Unexplained differences block G5.

**7. Deploy.** Switch to `ops.sam` and open Deploy. The Azure form takes a service principal
(stored encrypted, never in the browser). Without a subscription, click Run plan: the plan view
shows the connect-later state with the last infrastructure validation. Write a note and click
Connect later; the record shows `ops.sam`. Apply is present and disabled with its reason: apply
is not part of this build.

## Drill-downs (when there are questions)

- **Field-by-field parity.** Verify, parity table: expand a scenario to see each field path,
  expected value, actual value and classification, with the noise rules applied (timestamps,
  generated IDs, ordering) listed. The overall figure is matching fields over compared fields.
- **Knowledge graph.** Discover, domain graph: processes, tables, queues and endpoints with their
  calls, reads, writes, publishes and consumes edges. Ask AI on the Validate tab answers from the
  knowledge base with citations that jump to the canvas ("Which workflows read the funding
  table?").
- **Test data with masking.** Open `CreditApplicationIntake` (Credit Origination domain) on
  Verify, test data panel: inputs generated from the schemas, enumerations and branch predicates,
  with SSNs masked to the last four digits (`***-**-1184`) and a note that production test data
  is a separate workstream.
- **Terraform and Terragrunt assets.** Build, infrastructure panel and target explorer:
  `infra/modules/` (resource group, Container Apps or AKS, Container Registry, Service Bus, Key
  Vault, PostgreSQL Flexible Server, identity, monitoring) and the Terragrunt configuration for
  `dev`, `test` and `prod` under `infra/live/`, with the `terraform fmt`, `terragrunt hclfmt` and `terraform validate` results.
  The pipeline definitions sit beside them.
- **Readiness and evidence.** Top bar, Readiness: thirteen expectations from the SME review, each
  with status computed from its evidence, the evidence link and the gap text when not met.
- **History and audit.** Top bar, History: every job and model call with provider, model, tokens,
  cost and latency, and every human decision on the same timeline. The Audit trail button on
  Validate, Design and Build lists the gate decisions, resets and the hashes they were bound to.

## Pre-flight checklist

Run through this on the demo machine the day before and again an hour before.

1. **Postgres up.** `make db` (Docker Compose when a Docker daemon runs, otherwise the Postgres at
   `DATABASE_URL`). PostgreSQL 16 with pgvector.
2. **Migrations.** `make db` also runs `alembic upgrade head`; `.venv/bin/alembic -c
   api/alembic.ini current` must print the head revision.
3. **Synthetic estate zip.** `make samples-zip` (after `make samples`) writes
   `samples/synthetic-gmf.zip`, the file you drop in step 1.
4. **NuGet.** The build restores packages. Check that `https://api.nuget.org/v3/index.json`
   answers from the demo machine, or run `make nuget-cache` on a machine with NuGet access and
   copy `.tools/nuget-cache` to the same path on the demo machine.
5. **Test database for the generated tests.** Export `TEST_DATABASE_URL` or run Docker Desktop.
   The build passes the API's `TEST_DATABASE_URL` (environment or `.env`, default
   `postgresql://exiqo:exiqo@localhost:5432/exiqo_test`; the database name must end in `_test`)
   to the generated tests, which then run against that Postgres. When it is empty the build log
   says "TEST_DATABASE_URL not set: generated tests will start a Testcontainers Postgres (needs
   Docker)", and the first run needs a Docker daemon plus a Docker Hub pull of
   `postgres:16-alpine`; without them every test fails and G4 repeats that line.
6. **Provider variables.** For a real model set `CLAUDE_CODE_OAUTH_TOKEN`, or the Azure OpenAI
   variables, in the environment or `.env`. Settings shows each provider as configured or
   missing; it never shows values. Without keys the demo runs with `PROVIDER=fake` (scripted
   replies) and says so on screen.
7. **Ports.** The API on 8000 and the web on 3000 by default (`make api`, `make web`; change with
   `API_PORT` and `WEB_PORT`). The generated service's Development CORS allows the
   API's `CORS_ORIGINS` (default `http://localhost:3000`), which the Verify tab's Get dev token
   call needs: keep the web on 3000, or start the API with `WEB_PORT` set (the Makefile then sets
   `CORS_ORIGINS` to the web origin and the API passes it to the service). Verify starts the service on a free port the API allocates.
8. **Rehearsal.** `make demo-check` against the demo API (or let it start one) and read the table:
   every row must pass in full mode. The project it leaves behind is the rehearsal project for
   steps 5 to 7.
9. **If restore fails live.** Do not retry on stage. The build card says "job finished, build
   failed", the restore step shows "NuGet unreachable" and the remedy (`make nuget-cache`), G4
   stays closed with the reason, and Verify and Deploy show why they cannot start the service or
   plan an image. Say that plainly, switch to the rehearsal project, and continue from there.

## What this container could not verify

The build environment refuses `api.nuget.org`, the Terraform and OpenTofu provider registries,
Microsoft and Docker container registries and Hugging Face. Here, `dotnet restore` fails with
NU1301, so the generated solution is never compiled, its tests never run, the service never
starts and Swagger, the dev token and parity against the running service were never exercised;
`terraform init` cannot download providers, so `terraform validate` never ran (`fmt` and
`terragrunt hclfmt` did). The acceptance script ran in offline mode and asserted those honest
failure states instead. The full-mode assertions (every test green, infrastructure validated,
parity 100% on the generated scenarios except the intended fields) must be confirmed with
`make demo-check` on a machine that reaches NuGet and the registry before the demo.
