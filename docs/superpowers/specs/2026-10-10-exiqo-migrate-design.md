# ExiQO Migrate: TIBCO BusinessWorks to .NET platform

Design spec, 10 October 2026. Approved in conversation by Abhijeet Tiwari (R Systems).

## 1. Purpose

One platform, one screen, seven tabs, that takes any TIBCO BusinessWorks estate (BW5 `.process` and BW6 `.bwp`) to a compiled, tested, running .NET 10 service with its Azure infrastructure code, and proves every step on screen with numbers.

Built for the GM Financial pursuit (about 650 BusinessWorks workflows, exit TIBCO by February or March 2028, Azure shop, two years of unsatisfying `.md`-based AI attempts, 70 to 80% of effort lost to testing) but designed to migrate any BusinessWorks estate.

### The rules the design follows

1. Plain code decides structure, citations, coverage, gates, scaffolds, compile, test results, field comparison and routing scores. Models draft and explain; they never approve.
2. Every number on screen has a defined formula and a clickable evidence artifact.
3. The synthetic estate is labelled synthetic on screen. Parity figures are labelled "derived from the TIBCO model" until a real TIBCO sandbox is connected.
4. No claim without an implementation behind it. "RSI SMEs fine-tuned vectors" is not said; "every SME decision becomes retrievable migration memory" is built and said.
5. Nothing the client keeps is a `.md` file. Design records, reports and evidence are rendered in the product and exportable as JSON, PDF or a zip of code and infrastructure.

## 2. What the person sees

A single page with a stepper across the top. Each step shows one of: not started, running, needs review, approved, failed. Each step has an Evidence drawer that names the artifact proving it and links to it.

| # | Tab | Gate | Evidence |
|---|---|---|---|
| 1 | Upload | scope confirmed | parse report: files found and parsed, zero unknown activity types, every XML element mapped or explained |
| 2 | Discover | none | domain list and graph built from the parsed model only; per-workflow I/O card |
| 3 | Validate | G1 rules, G2 findings | citations verified n/n, coverage n/n, challenger verdicts, signed gate records with spec hash |
| 4 | Design | G3 architect | design record with mapping table step to C# file, class, method; hash bound to the spec hash |
| 5 | Build | G4 engineer | `dotnet build` log, TRX test results per rule and branch, metrics radar, `terraform validate` and `terragrunt hclfmt` output |
| 6 | Verify | G5 QA lead | Swagger run, scenario results, field-by-field parity table with classification |
| 7 | Deploy | G6 operations | `terragrunt run-all plan` output, pipeline definition, connect-later state |

Plus three secondary screens reachable from the top bar: Settings (providers, models, routing, evals, toolchains), History (runs, tokens, cost, decisions) and Readiness (expectation, status, evidence, gap).

### 2.1 Upload

Drop a zip or a folder, or point at a server path. File tree with counts by type: `.bwp`, `.process`, `.bwm`, `.xsd`, `.wsdl`, `.json` schema, `.substvar`, shared resources (`.jdbcResource`, `.httpClientResource`, `.jmsConnResource`, `.sharedhttp`, `.sharedjdbc`, `.sharedjmscon`), SQL, `MANIFEST.MF`, `.application`. Validation before scan: duplicates (packaged copies in `.application` folders), unreadable files, unsupported extensions. "Scan" starts a job; progress streams. The parse report is a table: file, dialect, process, activities, complexity band, status, and a red panel for anything unknown or unexplained. A row opens the workflow on the canvas in tab 2.

### 2.2 Discover

Domain dropdown populated by code, named by AI. Code groups processes into domains using, in order: BW6 module and application boundaries, shared tables and queues, call graph connectivity, and name stems. AI proposes a business name for each group with citations to the evidence (table names, queue names, process names). The SME can rename or merge domains; the decision is stored.

Domain graph (Cytoscape): processes, subprocesses, queues, topics, tables, HTTP endpoints, with edges calls, reads, writes, publishes, consumes. Filters by complexity band and migration status. Click a process: the process canvas (React Flow, ELK layout), activity cards coloured by type, conditional links labelled with their expression, "otherwise" links dashed, inspector with Config, SQL, Mappings, Rules, Findings, XML tabs; selecting a step scrolls and highlights the source element.

Per-workflow I/O card, built from the parsed model:

- Trigger: REST (verb, path), SOAP, JMS (queue or topic, selector), timer (schedule).
- Request contract and response contract (from XSD, WSDL or JSON schema), rendered as a field tree.
- Side effects: tables read and written (with the statements), messages published, services called, subprocesses called, faults thrown.
- Sample outputs: one example per scenario produced by the parity evaluator (section 6), labelled "derived from the TIBCO model".

### 2.3 Validate

Run analysis (job). Streaming log, each line tagged `code` or `ai`. Order fixed in code: static risk rules, Workflow Analyst, citation verifier, coverage matrix, Risk Reviewer, Independent Challenger.

- Summary card, then two banners: "Citations verified n of n" and "Coverage n of n steps, branches and mappings". Failures listed, never hidden.
- Rule cards: ID, text, citation chips (step and expression; click jumps to the canvas step and highlights the XML), confidence 0 to 1, challenger verdict (agree or disagree with reason), Approve, Edit, Reject. Editing an approved rule reopens G1 and moves the spec hash.
- Finding cards: severity, text, evidence citations, decision Keep behaviour, Fix, Ask business, note field.
- Ask AI panel: questions answered from the knowledge base with citations that link to the canvas.
- G1 enabled only when every rule is decided and both banners pass. G2 enabled only when every finding is decided and G1 is approved. The API refuses gate changes from an agent caller.

### 2.4 Design

Design record rendered on screen (stored as JSON, exportable as PDF), produced by the Target Designer agent over a deterministic skeleton:

- Solution layout: projects, folders, files.
- Mapping table: every TIBCO step to C# file, class and method, with the pattern applied (section 7.2) and the rule and finding IDs it carries.
- Contracts: XSD and JSON schema to C# records.
- Configuration: every substvar and shared resource setting to an `appsettings.json` key or a Key Vault secret name.
- Messaging: queues, topics and subscriptions to Azure Service Bus entities.
- Resilience: timeouts, retries and circuit breakers to `Microsoft.Extensions.Http.Resilience` settings.
- Sequence diagram per trigger (rendered from the IR, not drawn by a model).
- Decisions and open questions, each with a recommendation and the note that a recommendation is not a decision.
- Expected differences: findings decided Fix, so the parity review knows what to expect.

G3: architect approves; the record's hash is recorded with the approver and the spec hash.

### 2.5 Build

Generate (job). Target explorer beside source explorer: `.bwp` and XML on the left, `.sln`, `.csproj`, `.cs`, `infra/`, `Dockerfile`, pipelines on the right, Monaco viewer with the TIBCO expression kept as a comment above the C# it became.

Order of work, shown live:

1. Scaffold the solution deterministically from the IR and design record.
2. Generate xUnit tests first: one per approved rule (named in the rule's own words), one per branch, one per parity scenario (expected output fixture).
3. Code Builder agent fills mapping and service method bodies; reviewer panel (equivalence, security, standards) triages findings as patch, defer or decision needed.
4. `dotnet build`, then `dotnet test` with TRX and coverage; repair loop capped at five, each repair logged with its diff.
5. Generate infrastructure assets (section 8) and validate them.

Metrics radar, formulas fixed:

| Metric | Formula |
|---|---|
| Rule carry-forward | approved rules with a passing test / approved rules |
| Test pass rate | passed tests / total tests |
| Branch coverage | IR branches with a passing test / IR branches |
| Code coverage | coverlet line coverage of the generated service projects |
| Standards adherence | 1 minus (analyzer warnings / files), floored at 0; `dotnet format --verify-no-changes` must pass |
| Compile | pass or fail, warning count |
| Infra validity | `terraform validate` and `terragrunt hclfmt --check` pass |

G4: engineer approves the build. The gate refuses if any approved rule has no test body or any test fails.

### 2.6 Verify

Start the generated service (managed subprocess, port allocated by the API). Embedded Swagger UI (iframe to the service's `/swagger`). Authentication: the generated service issues a development JWT from `POST /auth/dev-token` when `Auth:Mode=Development`; production mode reads Entra ID settings from configuration and the dev endpoint is compiled out.

Scenario runner: the scenario set (section 6.3) is sent to the running .NET service and to the parity evaluator. Field-by-field table per scenario: field path, expected, actual, match, classification: match, defect, intended change (traced to a finding decided Fix), noise (timestamps, generated IDs, ordering; rules reviewed by a person). Parity per scenario and per workflow: matching fields / compared fields. For JMS-triggered workflows the runner publishes to the in-memory bus of the running service and reads what it published and wrote.

Test data panel: rows generated from the schemas, enumerations and branch predicates; masking rules applied to fields tagged sensitive (SSN, account numbers); a note that production test data is a separate workstream.

G5: QA lead accepts the parity report; unexplained differences block the gate.

### 2.7 Deploy

Connect Azure form: tenant, subscription, resource group, Container Registry, runtime (Container Apps or AKS), Service Bus namespace, Key Vault, Log Analytics. Credentials are a service principal stored encrypted; nothing is stored in the browser. "Connect later" is a first-class state: the tab still shows the generated assets and the plan output from the last validation.

Plan view: `terragrunt run-all plan` output, parsed into adds, changes and destroys per module. Apply is not in this build: the button is present, disabled, with the reason shown.

G6: operations owner approves the plan.

### 2.8 Settings

- Providers: Claude via Claude Code OAuth token (primary), Azure OpenAI or Azure AI Foundry (fallback). Keys from environment only; the screen shows configured or missing, never values.
- Model per stage, per provider.
- Routing: complexity score 0 to 10 per task (section 5.4) to tier: 8 to 10 strongest, 4 to 7 standard, 0 to 3 efficient. Auto mode switches to the fallback provider on error or rate limit and records the switch.
- Per-provider evaluation: run the understanding pipeline on a chosen set of workflows with each configured provider; report citation-verified rate, coverage, challenger agreement, rules per workflow, latency and cost side by side. This is the measured answer to "how efficient is the fallback".
- Toolchains: presence and version of `dotnet`, `terraform`, `terragrunt`, `docker`, `az`.
- Tracing: Langfuse on or off and its URL.

### 2.9 History and tokenomics

Every job and every model call: stage, agent, provider, model, tokens in and out, cost, latency, workflow, gate outcome. Totals by phase and per workflow by complexity band. Every human decision on the same timeline.

### 2.10 Readiness

A table of the expectations from the SME review (synthetic estate, domain entry point, canvas, compilable .NET, metrics on screen, functional tests, Swagger same inputs same outputs, Azure deployment assets, provider choice, routing, knowledge layer, test data, tokenomics), each with status computed from the presence of its evidence artifact, the artifact link, and the gap text if not met. This is the honest internal view and is also presentable.

## 3. Architecture

```
exiqo-migrate/
  web/                 Next.js 15 (App Router), TypeScript, Tailwind, RSI design tokens
  api/                 FastAPI, Pydantic v2, SQLAlchemy 2 + asyncpg, Alembic, SSE job streams
  engine/              Python 3.12 package, no web dependencies
    tibco_parser/      BW5 and BW6 parsers, resources, substvar, contracts, completeness
    ir/                neutral model, canonical activity catalogue, complexity
    domains/           domain discovery, graph builder
    analysis/          static risk rules, evidence index, citation verifier, coverage matrix
    agents/            provider abstraction, routing, analyst, risk reviewer, challenger, designer, builder, test engineer, reviewers
    parity/            evaluator, scenario generator, field comparison, classification
    dotnet_gen/        design record, pattern catalogue, templates, generator, dotnet runner, TRX parser, metrics
    infra_gen/         Terraform modules, Terragrunt live, Dockerfile, pipelines, validators
    knowledge/         graph tables, embeddings, hybrid search, migration memory, Ask
  samples/synthetic-gmf/   synthetic BW6 estate plus one BW5 process (section 9)
  deploy/              compose files, platform Dockerfiles
  docs/                this spec, plans, demo runbook
  docker-compose.yml   postgres:16 with pgvector; profile observability adds Langfuse
  Makefile             setup, dev, test, e2e, demo
```

Runtime on a demo laptop: Postgres in Docker; API and web on localhost; `dotnet`, `terraform` and `terragrunt` on the host path (the API shells out and reports absence in Settings).

### 3.1 Data

Postgres is the single store. Core tables:

- `projects`, `uploads`, `files` (path, type, dialect, sha256, bytes in object storage on disk under `data/uploads/<project>/`)
- `processes` (IR JSON, spec hash, complexity, domain id), `activities`, `transitions`, `contracts`, `resources`, `substvars`
- `domains` (name, evidence, renamed_by), `graph_nodes`, `graph_edges`
- `rules`, `findings`, `citations`, `coverage_items`, `challenges`
- `gates` (process id, gate, state, by, at, spec hash, note), `audit_log` (append only)
- `design_records`, `builds`, `build_files`, `test_results`, `metrics`
- `scenarios`, `scenario_runs`, `field_diffs`
- `infra_assets`, `plans`
- `jobs`, `job_events`, `model_calls`
- `memory_items` (kind: decision, pattern, mapping, note; text; embedding vector(384); source refs), `chunks` (embeddings of rules, findings, summaries)
- `settings`, `providers`, `evals`

Embeddings: `fastembed` BAAI/bge-small-en-v1.5, 384 dimensions, computed locally. Raw XML is never embedded.

Spec hash: SHA-256 over the canonical JSON of the IR, the approved rules and the finding decisions for a process. Gates G1 and G2 bind to the spec hash; G3 to G6 bind to the spec hash plus the design record hash.

### 3.2 Jobs

Long operations run as jobs in the API process (asyncio tasks with a bounded worker pool), persisted in `jobs` with `job_events` for streaming. The web subscribes over SSE at `/jobs/{id}/events`. Job kinds: scan, discover, analyse, eval, design, generate, build, test, infra, verify, plan. A job restarted after an API restart resumes from the last persisted stage or marks itself failed with the reason.

### 3.3 API surface (summary)

`/projects`, `/projects/{id}/upload`, `/projects/{id}/scan`, `/projects/{id}/domains`, `/processes/{id}` (IR, canvas, io-card, xml), `/processes/{id}/analyse`, `/rules`, `/findings`, `/gates`, `/processes/{id}/design`, `/processes/{id}/build`, `/builds/{id}/files`, `/builds/{id}/tests`, `/builds/{id}/metrics`, `/builds/{id}/run` (start and stop the service), `/scenarios`, `/verify`, `/infra`, `/plan`, `/settings`, `/providers/eval`, `/history`, `/readiness`, `/ask`, `/jobs/{id}/events`.

Gate endpoints require a user principal header (`X-User`); a request carrying `X-Agent` is refused with 403. In the demo, users are picked from a signed-in user menu; production authentication is Entra ID in front of the API.

## 4. Parsing and the neutral model

### 4.1 Inputs

- BW6: `.bwp` (WS-BPEL 2.0 with `tibex` extensions), `.bwm` module descriptors, `META-INF/MANIFEST.MF`, `.substvar`, shared resources, XSD, WSDL, JSON schema, `.application` packaging folders (detected as duplicates and flagged).
- BW5: `.process` (`xmlns.tibco.com/bw/process/2003`), `.sharedjdbc`, `.sharedhttp`, `.sharedjmscon`, `.serviceagent`, global variables (`defaultVars.substvar`), `.aeschema`, XSD, WSDL.
- SQL files for DDL and seed, used by the evaluator and the test database.

Parsers accept CRLF and LF. They never drop an unknown element: every element is either mapped into the model or listed in the completeness report with its path and a reason.

### 4.2 Canonical activity catalogue

`rest.receive`, `http.receive`, `soap.receive`, `jms.receive`, `timer.start`, `jdbc.query`, `jdbc.update`, `jdbc.call`, `http.invoke`, `rest.invoke`, `soap.invoke`, `jms.send`, `jms.publish`, `call.process`, `mapper`, `assign`, `throw`, `catch`, `reply`, `end`, `log`, `java.invoke`, `xslt.transform`, `file.read`, `file.write`, `group.loop`, `group.iterate`, `group.transaction`, `group.critical`, `flow.parallel`, `wait`, `checkpoint`, `confirm`.

Each BW5 and BW6 native type maps to one canonical type. Unknown native types are reported and block the gate for that process until a mapping is added to the catalogue.

### 4.3 IR

`Application { id, name, dialect, modules[], resources[], substvars[], contracts[] }`
`Process { id, application, module, package, name, dialect, source_path, starter, activities[], transitions[], groups[], variables[], fault_handlers[], calls[], complexity }`
`Activity { id, name, type, native_type, config{}, input_bindings[] (expression, target field), output_schema, source_line }`
`Transition { from, to, kind: success | conditional | otherwise | error, expression, source_line }`
`Group { id, kind, members[], config }`
`Resource { id, kind: jdbc | http | jms | file, settings{}, masked_fields[] }`
`Contract { id, kind: xsd | wsdl | json, path, root_elements[] }`

### 4.4 Complexity

Score 0 to 10 from activity count, branch count, mapping expression size and function usage, XSLT presence, Java code presence, subprocess depth, group nesting, number of external systems. Bands: low 0 to 3, medium 4 to 6, high 7 to 10. Used for the parse report, the routing score and the wave planner.

## 5. Understanding and gates

### 5.1 Pipeline (fixed order, in code)

1. Static risk rules over the IR: read-then-write without a transaction group, `LIKE` on an identifier column, hard-coded endpoints, unused schema fields, unreachable branches, swallowed errors, sensitive fields in log statements.
2. Workflow Analyst (model): summary and numbered rules, each with citations `{activity_id, kind: sql | expression | condition | config, text}`.
3. Citation verifier (code): every citation names an existing activity and quotes text present in that activity's IR, character for character after whitespace normalisation. Failure marks the rule `unverified` with an owner.
4. Coverage matrix (code): every activity, conditional transition and mapping is cited by a rule or marked technical with a reason.
5. Risk Reviewer (model): business impact text for each static finding, plus any additional finding with citations (verified the same way).
6. Independent Challenger (model, strongest tier, does not see the analyst's reasoning): agree or disagree per rule with a reason; disagreements are flagged for the SME, never auto-resolved.

### 5.2 Gates

| Gate | Owner | Enabled when | Binds to |
|---|---|---|---|
| G1 rules | business SME | every rule decided, citations n/n, coverage n/n | spec hash |
| G2 findings | architect and business owner | every finding decided, G1 approved | spec hash |
| G3 design | architect | design record generated, G2 approved | spec hash + design hash |
| G4 build | engineer | compile passes, every approved rule has a test with a body, all tests pass, G3 approved | spec hash + design hash + build hash |
| G5 parity | QA lead | scenario set run, no unexplained differences, G4 approved | build hash + parity report hash |
| G6 deploy | operations owner | plan generated and validated, G5 approved | build hash + plan hash |

A changed upstream hash resets every downstream gate and records why.

### 5.3 Providers and routing

`LLMProvider` interface: `complete(request) -> {text, usage, cost, latency, model}`. Implementations: `ClaudeCodeProvider` (Claude Agent SDK with `CLAUDE_CODE_OAUTH_TOKEN`), `AzureOpenAIProvider` (`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, deployment names). A `RoutedProvider` chooses the model from the task's complexity score and the Settings table, falls back to the secondary provider on error or rate limit, and records every choice in `model_calls`. Prompt caching is enabled where the provider supports it; the hit rate is reported.

### 5.4 Task complexity score

Per task, not per workflow: process complexity (4.4) blended with task kind weights (analyse 1.0, challenge 1.2, design 1.1, build 1.3 plus 2 if Java code or XSLT 2.0 functions are present, test 0.9). Clamped 0 to 10. Shown on the job log line so the routing is visible.

### 5.5 Migration memory

Stored and retrievable: every SME decision (rule edits, finding keep or fix with notes, domain renames), every accepted mapping pattern (TIBCO expression to C# with the review verdict), design decisions, and an RSI-authored seed set of TIBCO-to-.NET patterns labelled `source: rsi-seed`. Retrieval is hybrid (full text plus vector) and is injected into the analyst, designer and builder prompts as "prior decisions on similar steps", each with its source. The UI shows which memory items influenced an output.

## 6. Parity evaluator

### 6.1 What it is

An interpreter over the IR that produces the expected output of a workflow for a given input, by executing the model the way the BusinessWorks engine would: starter input, input bindings evaluated per activity, activity executed by type, outputs stored as process variables, transitions chosen by evaluating their conditions, reply or end assembled.

It is labelled on every screen as "expected output derived from the TIBCO model". Its interface (`ExpectedOutputSource`) is the same one a real TIBCO sandbox adapter implements in the pilot.

### 6.2 Execution

- XPath: `elementpath` (XPath 2.0) with a variable context (`$Start`, `$ActivityName`, process variables, substvars via `bw:getModuleProperty`).
- XSLT input bindings: SaxonC (`saxonche`) for XSLT 2.0; lxml XSLT 1.0 fallback with a logged downgrade.
- `jdbc.*`: the SQL executed as written against the seeded test Postgres (parameters bound in order).
- `http.invoke`, `rest.invoke`, `soap.invoke`: stub registry keyed by resource and path with scenario-defined responses, including timeouts and errors for resilience branches.
- `jms.send`, `jms.publish`: captured as side effects.
- `call.process`: recursive evaluation.
- `group.transaction`: a database transaction; `group.loop` and `group.iterate`: iteration over the XPath set; `flow.parallel`: branches evaluated in order, merge as modelled.
- `throw`: a fault with its name and data; `catch`: handled as modelled.

### 6.3 Scenarios

Generated per process: a happy path from the schema (types, enumerations, patterns), one scenario per conditional transition (simple predicates solved: numeric thresholds, string length, existence, enumeration membership), one per fault path, one per stubbed external failure. SMEs can add scenarios. Each scenario stores inputs, stub responses, seed state, expected output and expected side effects.

### 6.4 Comparison

Both outputs flattened to field paths. Each field: match, defect, intended change (a finding decided Fix whose citation touches this field's lineage), or noise (configured rules: timestamps, generated IDs, ordering). Side effects compared the same way (rows written, messages published with their payloads). Parity = matching fields / compared fields, excluding noise.

## 7. .NET generation

### 7.1 Solution shape

One solution per TIBCO application or BW5 project: `<App>.sln` with `<App>.Api` (ASP.NET Core controllers, Swagger, auth), `<App>.Core` (contracts, services, rules), `<App>.Infrastructure` (Dapper repositories with the SQL verbatim, Service Bus, HTTP clients), `<App>.Tests` (xUnit, Testcontainers). Target `net10.0`. Nullable enabled, analyzers on, `Directory.Build.props` with the standards.

### 7.2 Pattern catalogue (deterministic)

| TIBCO | .NET |
|---|---|
| `rest.receive` / `http.receive` / `soap.receive` | controller action on the same route and verb; SOAP via CoreWCF where present |
| `jms.receive` | `IMessageBus` consumer; `Azure.Messaging.ServiceBus` implementation; in-memory implementation for tests and local run |
| `jms.send` / `jms.publish` | `IMessageBus.Publish` with the entity name from the design record |
| `timer.start` | `BackgroundService` with the schedule from config |
| `jdbc.query` / `jdbc.update` / `jdbc.call` | repository method on Dapper with the statement verbatim and parameters bound in order; rule and finding IDs in the XML doc comment |
| `call.process` | service method; separate project only when the subprocess is shared across applications |
| `mapper` / `assign` / input bindings | generated C# mapping method; the TIBCO expression kept as a comment; simple paths generated by code, arithmetic and functions by the builder agent |
| `xslt.transform` | Saxon .NET with the stylesheet carried over, or a generated mapping when the stylesheet is simple |
| `group.transaction` | Npgsql transaction scope around the members |
| `group.loop` / `group.iterate` | loops; chunking when the IR shows batch semantics |
| `flow.parallel` | `Task.WhenAll` |
| `group.critical` | row lock or distributed lock (Redis) per design decision |
| `throw` | typed exception; `ProblemDetails` mapping in one handler |
| `catch` | exception filter or handler as modelled |
| HTTP client resource | `IHttpClientFactory` client with `Microsoft.Extensions.Http.Resilience` timeout, retry and circuit breaker from the resource settings |
| substvars | `appsettings.json` keys; secrets as Key Vault references |
| `log` | `ILogger` with structured fields; sensitive fields masked |
| `java.invoke` | the Java carried into a documented decision: port to C# with tests, or wrap; never silently dropped |

### 7.3 Contracts

XSD, WSDL and JSON schema to C# records with `System.Text.Json` attributes, generated by code (`xsd_to_csharp` in `dotnet_gen`), with the XML element path kept as an attribute for lineage.

### 7.4 Tests first

`<Process>RulesTests.cs`: one `[Fact]` per approved rule, named `R{n}_{rule text slug}`; `<Process>BranchTests.cs`: one per conditional transition; `<Process>ScenarioTests.cs`: one per parity scenario using the expected output fixture. Testcontainers Postgres seeded from the SQL files. A test with no body fails with "no test body for R{n}" so the gate can count it.

### 7.5 Runner and metrics

`dotnet restore`, `dotnet build -warnaserror:false` with warnings collected, `dotnet format --verify-no-changes`, `dotnet test --logger trx --collect:"XPlat Code Coverage"`. TRX parsed to per-test results mapped to rule, branch and scenario IDs; coverlet Cobertura parsed for coverage. Repair loop: on failure the builder agent sees the failing test, the rule and the TIBCO step, proposes a diff, the diff is applied and logged; five attempts, then stop and report.

### 7.6 Running the service

`dotnet run --project <App>.Api --urls http://127.0.0.1:<port>` managed by the API (start, health check, stop, log capture). Swagger at `/swagger`. Development JWT from `POST /auth/dev-token` when `Auth:Mode=Development`.

## 8. Infrastructure generation

Generated in the Build step beside the solution, consumed by the Deploy step.

```
infra/
  modules/
    resource_group/  container_registry/  container_app/  aks_workload/
    postgres_flexible/  service_bus/  key_vault/  monitoring/  identity/
  live/
    terragrunt.hcl                 root: azurerm backend, provider, common inputs
    dev/<app>/<module>/terragrunt.hcl
    test/<app>/<module>/terragrunt.hcl
    prod/<app>/<module>/terragrunt.hcl
Dockerfile                          multi-stage, net10.0 runtime image, non-root
.dockerignore
docker-compose.override.yml         local run with Postgres and the in-memory bus
.github/workflows/<app>.yml         build, test, image push, terragrunt plan, apply on approval
azure-pipelines.yml                 the same stages for Azure DevOps
```

Inputs come from the IR and design record: Service Bus queues, topics and subscriptions from the messaging entities; Key Vault secret names from the resource and substvar mapping; container app ingress from the REST starters; scheduled jobs from timer starters; Postgres database names from the JDBC resources. Validation on generation: `terraform fmt -check`, `terraform init -backend=false` and `terraform validate` per module, `terragrunt hclfmt --check`. Results shown on screen and stored as evidence. `terragrunt run-all plan` runs only when Azure is connected; otherwise the Deploy tab shows the last validation and the connect-later state.

## 9. Synthetic estate

`samples/synthetic-gmf/`, labelled synthetic on screen and in a `SYNTHETIC.txt` at its root. Built on the real BW6 file layout and dialect (BPEL with `tibex` extensions, `module.bwm`, `META-INF/MANIFEST.MF`, `default.substvar`, shared resources, XSD, WSDL, JSON schema), checked against TIBCO's public sample structure for fidelity, with original content.

Application `gmf.credit` with three modules and one BW5 project:

| Module | Process | Trigger | What it does | Seeded rules and findings |
|---|---|---|---|---|
| CreditOrigination | `CreditApplicationIntake` | REST `POST /applications` | validates the applicant, VIN subprocess, duplicate check (JDBC), bureau subprocess, decision subprocess, audit subprocess, reply | SSN format rule; duplicate within 30 days raises a fault; audit on every request |
| CreditOrigination | `BureauOrchestrator` | subprocess | parallel HTTP to Equifax and TransUnion stubs, merge | higher score wins; single-bureau flag when one times out; both fail raises a fault; timeout 1000 ms, breaker 50% over 20 |
| CreditOrigination | `DecisionEngine` | subprocess | DTI arithmetic in mappings, tier branches, term cap | Tier A 740+ and DTI < 0.35; Tier B 680 to 739; Tier C 620 to 679 and DTI < 0.45, term capped at 72; else decline; missing income raises a fault |
| CreditOrigination | `ValidateVIN` | subprocess | VIN length and check digit | 17 characters, check digit rule |
| CreditOrigination | `AuditLog` | subprocess | JDBC insert | finding: SSN written to the audit table in clear |
| DealerFunding | `DealerFundingRequest` | JMS queue `gmf.dealer.funding.request` | dealer active check, amount tolerance, write funding, publish `gmf.funding.events` | amount within 2% of approved; findings: read-then-write without a transaction, dealer id matched with LIKE |
| DealerFunding | `FundingStatusLookup` | REST `GET /funding/{dealId}` | JDBC read, reply | |
| LoanServicing | `PaymentPosting` | JMS queue `gmf.payments.inbound` | transaction group: fees, interest, principal allocation, late fee | late fee 25.00 after a 10-day grace period; allocation order; finding: grace comparison uses `>=` where the policy says `>` |
| LoanServicing | `AccountLookup` | REST `GET /accounts/{accountNumber}` | JDBC read, masked reply | SSN masked to last four |
| LoanServicing | `NightlyDelinquencyScan` | timer 02:00 daily | JDBC query, JMS send per account to `gmf.collections.queue` | 30 days past due; finding: hard-coded endpoint in the mapping |
| LegacyAddress (BW5) | `LegacyAddressStandardize` | HTTP receiver | trim, uppercase, state code validation, reply | state code must be in the list; finding: no otherwise transition |

Database `gmf` schema: `applicant`, `vehicle`, `credit_application`, `bureau_pull`, `decision`, `dealer`, `deal`, `funding`, `loan_account`, `payment`, `delinquency`, `audit_log`, with DDL and seed rows under `Database/`. HTTP stubs for the bureaus under `Stubs/`.

## 10. Web application

Next.js 15 App Router, TypeScript strict, Tailwind with RSI tokens (Poppins; purple `#9B5DE5`; dark purple `#20063B`; coral `#FF495C`; warm grey `#F4F3EE`; background `#FAF9F7`; semantic grey deterministic, teal AI, green gate, amber decision, red error), light theme only. Libraries: React Flow + elkjs (canvas), Cytoscape (domain graph), Recharts (radar), Monaco (code), swagger-ui-react is not used; the service's own Swagger UI is embedded in an iframe. State via TanStack Query; SSE via `EventSource`. No em dashes, no marketing language, plain labels.

Layout: top bar (logo, product name, project selector, user menu), the stepper, the active tab's content, a footer with the RSI copyright. Secondary screens open as routes from the top bar.

## 11. Security and data handling

- Keys and tokens from environment variables only; the Settings screen shows presence, never values.
- Uploads are rebuilt path by path; absolute paths, `..` segments and zip entries pointing outside the project folder are refused.
- Fields tagged sensitive (SSN, account numbers, card numbers) are masked before logs, traces and embeddings. Shared resource passwords are masked in the IR.
- Text inside TIBCO files is data, never instructions: prompts wrap it in delimited blocks and the agents have no tool that writes outside the build directory.
- Gate changes require a user principal; agent callers are refused.
- Generated services: no secrets in `appsettings.json`; Key Vault references; non-root container.

## 12. Testing the platform

- `engine/tests`: parsers (every synthetic file and the public BW5 and BW6 samples already on disk), completeness, domain discovery, verifier, coverage, evaluator (scenario results asserted against hand-computed expected values), generator (golden files for the scaffold), TRX and coverage parsing, infra validators, memory retrieval.
- `api/tests`: endpoints, gate rules (403 for agents, resets on hash change), job streaming.
- `web/e2e`: Playwright through all seven tabs on the synthetic estate.
- Acceptance run (`make demo-check`): upload the synthetic zip, scan, discover, analyse `DealerFundingRequest` and `CreditApplicationIntake`, approve G1 to G3 as named users, build with every test green, infra validated, verify with parity 100% on the generated scenarios except the fields tied to findings decided Fix, plan generated in connect-later mode.

## 13. Out of scope for this build

Live Azure apply, Neo4j, deck changes (650 workflows, case study, timeline), TIBCO SME review of the synthetic estate, production authentication wiring, multi-tenant isolation.

## 14. Risks

| Risk | Handling |
|---|---|
| Synthetic estate not convincing to a TIBCO expert | built from the real file layout and dialect; checked by a BW6 structural validator; reviewed by the TIBCO consultant before the client call |
| Evaluator semantics differ from the real engine | labelled on screen; interface ready for a sandbox adapter; differences classified, never hidden |
| Builder agent output fails to compile | deterministic scaffold compiles on its own; agent only fills method bodies; repair loop capped and logged |
| Model keys missing on the demo laptop | code-only paths (parse, discover, scaffold, tests from rules, infra) work without a key and say so on screen |
| `dotnet`, `terraform`, `terragrunt` missing | Settings shows presence; Build reports the missing tool and stops at that stage |
| Demo time | the acceptance run is the rehearsal; everything can be pre-run and reopened from History |
