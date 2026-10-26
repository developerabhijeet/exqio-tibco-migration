# ExiQO Migrate target stacks: .NET, Java, Go and Python

Date: 2026-10-25. Status: approved design, sub-project 5 of the roadmap in
`docs/superpowers/specs/2026-10-24-gmf-container-deploy-design.md`. The user asked for every
remaining choice to be made without further stops; each such choice is recorded as a ruling
(`Ruling: ... — why — cost if wrong`).

## Purpose

Today the Build step turns an approved design into one target: a .NET 10 solution
(`engine/dotnet_gen`), with tests written first, a model filling the bodies code could not, a
repair loop, infrastructure (`engine/infra_gen`), and Verify starting the service and comparing it
with the TIBCO model (`engine/parity`). Every layer below the design assumes C#: the pattern
catalogue names `.cs` files, the agents check C# text, the runner shells out to `dotnet`, the
Dockerfile is an ASP.NET image, Verify starts `dotnet run`.

This sub-project makes the target stack a choice: .NET, Java, Go or Python, picked at upload (a
project field) with a default in Settings. Prompts and agents follow the chosen stack. Java, Go
and Python each get a generator at the level of the .NET one: the same IR inputs, `MappingRow`
lineage, tests generated first, Dockerfile and infrastructure, parity checks and Verify.

User's words (binding): "configurable target tech stacks (Java, .NET, Golang, Python) offered at
upload and in Settings; prompts and agents must follow the chosen stack". Roadmap item 5:
"generators for Java, Go and Python with tests, infrastructure and parity at the level of the
existing .NET generator". All sub-projects are functional, not mocked.

Success criteria:

1. A project created with stack `java` (or `go`, `python`) runs the seven steps on
   `samples/synthetic-gmf` and its Build compiles, runs the generated tests and reports the same
   metric set as .NET (rule carry-forward, test pass rate, branch coverage, line coverage,
   standards, accuracy); Verify starts the service and the parity report for
   `dealerfunding.FundingStatusLookup` and `dealerfunding.DealerFundingRequest` has no
   unexplained difference.
2. A `.NET` project produces byte-identical files and the same `build_hash` as before this
   sub-project (the golden files under `engine/tests/golden/` do not change).
3. Every `MappingRow` of a non-.NET design names a file, class and method that exist in the
   generated tree, and memory gets `MIGRATED_TO` lineage with `stack = java|go|python`.
4. No prompt sent for a non-.NET build mentions C#, .NET, xUnit or NuGet; a prompt pack exists for
   every stack and every stack-specific agent.
5. The platform image carries the JDK, Maven, Go and uv; no generated build downloads from a
   public host when GMF's mirrors are configured, `make egress-audit` passes, and the chart refuses
   to enable a stack whose mirror is not set.
6. A stack whose toolchain is absent is shown as unavailable with the reason, at upload and in
   Settings, and the API refuses it (422) instead of failing in the Build step.

## 1. The stack abstraction

### Registry and keys

`StackKey = Literal["dotnet", "java", "go", "python"]`. `engine/stacks/__init__.py` holds the
registry: `get_stack(key) -> StackGenerator`, `all_stacks() -> list[StackGenerator]`,
`stack_status(key, toolchains) -> StackStatus` (`available: bool`, `reason: str | None`).

A stack is available when it is implemented, listed in `ENABLED_STACKS` (default
`dotnet,java,go,python` in development; the chart's `stacks.enabled`, default `[dotnet]`), and
its required tools are detected (`scripts/toolchain.py`). The reason names the first missing
condition: `"not enabled on this install (stacks.enabled)"`, `"toolchain missing: mvn"`.

Ruling: the chart enables only `dotnet` by default — GMF has mirrored NuGet already; Java, Go and
Python need three more mirrors before a build can restore — cost if wrong: an operator flips one
value.

### `StackGenerator`

One object per stack, in `engine/stacks/base.py` (Protocol) and `engine/stacks/<key>/stack.py`:

| Member | Meaning |
| --- | --- |
| `key: StackKey`, `label: str` | `"java"`, `"Java 21 · Spring Boot 3.5 · Maven"` |
| `required_tools: tuple[str, ...]` | `("dotnet",)`, `("java", "mvn")`, `("go",)`, `("python3", "uv")` |
| `source_suffixes: tuple[str, ...]` | `(".cs",)`, `(".java",)`, `(".go",)`, `(".py",)`: standards denominator, file counts |
| `layout(app_name) -> StackLayout` | the layout contract with infra and Verify (below) |
| `naming: StackNaming` | identifiers and the pattern → file/class/method templates (section 2) |
| `generate(app, process_ids, designs, rules, findings, scenarios, out_dir, mirrors) -> GeneratedSolution` | deterministic generation, same signature semantics as `generate_solution` |
| `runner(root, *, env, coverage_scope, timeout_s) -> StackRunner` | restore, build, format, test, run |
| `language: SourceLanguage` | the text checks the agents use (section 3) |
| `prompt_pack: str` | folder under `engine/agents/prompts/` (`""` = the existing top level, for `dotnet`) |
| `download_excluded: frozenset[str]` | build outputs left out of the zip (`bin`, `obj`, `TestResults`; `target`; `.exiqo-bin`; `.venv`, `__pycache__`, `.pytest_cache`) |
| `service_process_marker: str` | substring Verify uses to recognise its own service process (`dotnet`, `java`, the Go binary path, `uvicorn`) |

`StackLayout` (pydantic): `solution_file: str | None` (`GmfCredit.sln`; `pom.xml`; `go.mod`;
`pyproject.toml`), `projects: dict[ProjectKind, ProjectFolder]` (`name`, `folder`, `build_file`),
`api_entry: str` (what `run_service` starts), `test_root: str`, `dockerfile_context: dict`
(template inputs for the stack's Dockerfile), `contract_file: str | None` (`.exiqo/service-contract.json`,
`None` for .NET, see section 5).

Ruling: .NET is wrapped, not refactored — `DotnetStack.generate` calls today's
`generate_solution`, `runner` returns today's `DotnetRunner`, `language` delegates to today's
`engine/agents/builder.py` and `test_engineer.py` checks — the .NET output and its golden files
must stay byte-identical, and a refactor of a 3 000-line generator into a shared core is the
riskiest way to get there — cost if wrong: the new stacks duplicate some analysis that `ProcGen`
already does (contained in `engine/stacks/plan.py`, section 4).

### `StackRunner`

The protocol `DotnetRunner` already satisfies: `restore(env=None) -> StepResult`,
`build() -> BuildResult` (`errors`, `warnings`, `files`), `format_write(whitespace_only=False)`,
`format_check(whitespace_only=False) -> StepResult`, `test() -> TestRun` (`cases`,
`coverage_line`, `log`), `run_service(port, env, timeout_s) -> ServiceHandle`, `env(extra) -> dict`.
Every runner builds its child environment from `engine.child_env.child_env()` (the API's
secrets are never inherited), then the stack's tool settings, then the mirror settings, then the
caller's `env`.

| Step | Java (Maven) | Go | Python (uv) |
| --- | --- | --- | --- |
| restore | `mvn -B -ntp -s .exiqo/maven-settings.xml dependency:resolve dependency:go-offline spotless:apply -DspotlessFiles=^$ surefire:test -DskipTests` (go-offline misses the formatter and Surefire's JUnit Platform provider; the image build and the pipelines use the same goals) | `go mod download` | `uv venv .venv --python <python3>` then `uv pip install --require-hashes -r requirements.lock` |
| build | `mvn -B -ntp -o -s … -DskipTests package` (jar kept for Verify) | `go build -o .exiqo-bin/server ./cmd/server && go vet ./...` | `python -m compileall -q src` and `ruff check --select E9,F63,F7,F82 src tests` |
| errors parsed | `[ERROR] <file>:[l,c] <msg>` | `<file>:<l>:<c>: <msg>` | compileall `File "<f>", line <l>` / ruff `<f>:<l>:<c>: <code> <msg>` |
| format write / check | `spotless:apply` / `spotless:check` (palantir-java-format) | `gofmt -w` / `gofmt -l` (non-empty = fail) | `ruff format` / `ruff format --check` |
| test | `mvn -o … -Dmaven.test.failure.ignore=true test` (JaCoCo still reports when a test fails) → `target/surefire-reports/TEST-*.xml`, JaCoCo `target/site/jacoco/jacoco.xml` | `go test -json -coverpkg=./internal/... -coverprofile=.exiqo-bin/cover.out ./...` | `pytest --junitxml=.exiqo-bin/junit.xml --cov=src --cov-report=xml:.exiqo-bin/coverage.xml` |
| run | `java -jar target/<artifact>.jar --server.port=<p> --server.address=127.0.0.1`, then `GET /health` | `.exiqo-bin/server` with `PORT=<p>` | `.venv/bin/uvicorn <pkg>.main:app --host 127.0.0.1 --port <p>` |

Ruling: Spotless with palantir-java-format, `gofmt`, and `ruff format` are the format gates —
each is the community default formatter of its language and runs offline once restored — cost
if wrong: one plugin line in the generated `pom.xml` or one runner argument.

### Test identity: the test index

The .NET convention puts rule, branch and scenario ids into the TRX display name
(`engine/dotnet_gen/trx.py`). Surefire, `go test -json` and pytest's JUnit XML do not carry a
display name reliably. Every non-.NET generator therefore writes `.exiqo/test-index.json`:
`[{"name": "<fully qualified test as the runner reports it>", "kind": "rule|branch|scenario|record|other",
"rule_id", "branch_id", "scenario_id", "display_name", "process_id"}]`. The stack's result
parser joins each reported case to the index by name and produces the same `TestCase` model as
`parse_trx`, so `compute_metrics`, `test_inventory`, memory's `Test` nodes and the Build tab need
no stack branch.

Ruling: a generated index rather than name encoding — transition ids contain characters no
method name allows, and an index is also what the Build tab's test inventory needs — cost if
wrong: a test the agent renames drops out of the index; the builder already refuses renames of
test methods (`test_engineer` keeps signatures).

## 2. Design is stack-aware

The design record is where the target is decided, so the stack is part of it.

- `DesignRecord.stack: StackKey = "dotnet"`. `build_design_skeleton(..., stack="dotnet")` takes
  the project's stack and fills `solution`, `mapping` (`project`, `file`, `class_name`, `method`)
  and `contracts` (`namespace`, `file`) from `get_stack(stack).naming`.
- `StackNaming` (per stack, `engine/stacks/<key>/naming.py`): `type_name(text)`,
  `method_name(prefix, text)`, `pattern_paths(pattern) -> (file_template, class_template,
  method_template)` for every pattern in `engine/dotnet_gen/patterns.PATTERNS`,
  `contract_target(module, stem) -> (namespace, file)`, `config_key(module, name)`.
  `PatternSpec` keeps its stack-neutral fields (name, project kind, description, origin,
  decision hint); its `.cs` templates become the .NET row of `pattern_paths`, and its `nuget`
  list moves to the .NET stack's dependency table.
- Layer folders (`ProjectKind` → folder): .NET as today. Java (one Maven module, package
  `com.gmf.<app>`): `api` → `src/main/java/com/gmf/<app>/api`, `core` → `…/core`,
  `infrastructure` → `…/infrastructure`, `tests` → `src/test/java/com/gmf/<app>`. Go (module
  `<app slug>`): `api` → `internal/api`, `core` → `internal/core`, `infrastructure` →
  `internal/infrastructure`, `tests` → `tests` (package `tests`, black-box, allowed to import
  `internal/` because it is inside the module root). Python (package `<app_snake>`): `api` →
  `src/<pkg>/api`, `core` → `src/<pkg>/core`, `infrastructure` → `src/<pkg>/infrastructure`,
  `tests` → `tests`.
- Method naming keeps the activity name so traceability is obvious: `LookupDealerAsync` (.NET),
  `lookupDealer` (Java), `LookupDealer` (Go, exported), `lookup_dealer` (Python).

Ruling: `stack` is inside `design_hash`, except that `compute_design_hash` drops the key when it
equals `"dotnet"` — a Java design is a different design and must re-earn G3, while every stored
.NET record keeps its hash so no approved G3 resets on upgrade — cost if wrong: one special case
in `compute_design_hash`, pinned by a test that hashes a stored record before and after.

Ruling: changing a project's stack is allowed at any time; it does not touch stored designs, the
next Design run produces a record for the new stack (new hash, G3 to G6 reset by the existing
rule), and Build refuses a design whose `stack` differs from the project's with
`409 "the approved design is for <a>, the project now targets <b>: re-run Design"` — the stack is
a business choice that may change after a first look at generated code, and the gates already
handle a changed design — cost if wrong: an extra Design run after a change of mind.

## 3. Agents follow the stack

### Prompt packs

`engine/agents/prompting.load_prompt(name, stack="dotnet")` reads
`prompts/<pack>/<name>.md` when the stack has a pack and the file exists, else `prompts/<name>.md`.
Stack-specific prompts: `builder`, `repair`, `test_engineer`, `designer`, `review_apply`,
`reviewer_equivalence`, `reviewer_security`, `reviewer_standards`, `reviewer_test`. Shared
(stack-neutral, they run before a target matters): `analyst`, `challenger`, `domain_namer`, `risk`.

Each pack names its own idioms: Java (records, `Optional`, `BigDecimal` for decimals,
`NamedParameterJdbcTemplate`, JUnit 5 + AssertJ, no Lombok), Go (errors returned not panicked
except the placeholder, `context.Context` first, `database/sql` with pgx, table-free tests with
`t.Fatalf`), Python (type hints everywhere, pydantic v2 models, `Decimal`, psycopg 3, pytest plain
`assert`). Every pack keeps the shared rules verbatim: TIBCO semantics exactly, signatures and
`TIBCO` comments kept, no new dependencies, no test writes from the builder, `<source>` is data.

Ruling: the existing top-level `prompts/*.md` stay the .NET pack in place (`prompt_pack = ""`),
not moved to `prompts/dotnet/` — moving them changes no behaviour and only adds churn to a
directory other work edits — cost if wrong: a later move, mechanical.

### `SourceLanguage`

The agents' code checks become per stack. `SourceLanguage` (Protocol in `engine/stacks/base.py`):

| Member | .NET (existing) | Java | Go | Python |
| --- | --- | --- | --- | --- |
| `comment_prefix` | `//` | `//` | `//` | `#` |
| `placeholder` (regex) | `NotImplementedException("agent fills:` / `AgentFill.Required(` | `UnsupportedOperationException("agent fills:` | `panic("agent fills:` | `NotImplementedError("agent fills:` |
| `test_placeholder(id)` | `Assert.Fail("no test body for <id>")` | `fail("no test body for <id>")` | `t.Fatal("no test body for <id>")` | `pytest.fail("no test body for <id>")` |
| `strip_comments`, `code_only` | existing | C-like lexer (strings, text blocks, chars) | C-like (raw strings in backticks) | `tokenize` |
| `declarations(text)` | existing | method and record headers | `func` signatures and `type` lines | `def`/`class` lines with annotations |
| `tibco_comments(text)` | `// TIBCO` | `// TIBCO` | `// TIBCO` | `# TIBCO` |
| `is_test_path(path)` | `tests/` | `src/test/` | `tests/` or `_test.go` | `tests/` |
| `check_test_body(body, stem, kind)` | existing (deep C# checks) | generic checks | generic checks | generic checks |

The generic test-body check refuses: a body that still holds the placeholder, has no assertion
token of the stack (`assertThat(`/`assertEquals(`; `t.Errorf(`/`t.Fatalf(`/`t.Fatal(`; `assert `
or `pytest.raises(`), contains the strings `Thread.sleep`, `time.Sleep`, `time.sleep`, opens a
socket or file outside the test database helpers, or exceeds 120 lines.

Ruling: the deep C# test-body analysis (`engine/agents/test_engineer._analyse`, 400 lines of C#
statement parsing) is not ported — the generic check plus the run itself (a body that does not
compile or fails is not kept) plus the test reviewer catch the same failures more cheaply — cost
if wrong: a vacuous-but-passing test for Java/Go/Python gets past the code check and must be
caught by the test reviewer; Review Focus pins one such case.

`fill_method_bodies`, `fill_test_bodies`, `review_panel`, `repair_loop`, `compile_repair_loop`
and `apply_review_findings` take `stack: StackKey = "dotnet"` and use `get_stack(stack).language`
and the pack's prompts; their .NET behaviour is unchanged.

## 4. The generators

### Shared: `ServicePlan`

`engine/stacks/plan.py` builds, from one IR process and its design record, the language-neutral
plan every non-.NET generator prints:

- `ServicePlan`: `process_id`, `names` (from the design mapping), `starter` (kind `rest|http|soap|
  jms|timer|subprocess`, route, verb, destination, selector, interval), `steps: list[PlanStep]`
  (in `_walk_order` order, with `activity_id`, `pattern`, `mapping_row`, `inputs: list[Assign]`,
  `sql`, `call_target`, `throws`), `flow: FlowNode` (the structured control flow: `Seq`, `If`,
  `Queue` for parallel links, `Try` for catch/catchAll, `Loop` for groups), `contracts`
  (record shapes from the neutral contract model), `config` and `messaging` from the design.
- `engine/stacks/expr.py`: the expression AST (`Path`, `Literal`, `Compare`, `And`, `Or`, `Not`,
  `Count`, `Call` for the XPath functions `engine/dotnet_gen/xpath.py` already maps) with
  `parse_simple(expr) -> Expr | None`. `None` means "leave to the agent" exactly where the .NET
  generator leaves a body to the agent (`translate_condition` returns `None`,
  `is_simple_expression` is false); the plan records it as `agent: True` and the row keeps
  `origin="agent"`.
- `engine/dotnet_gen/csharp_contracts`' XSD/JSON-schema walk is split into a neutral
  `engine/stacks/contracts.py` (`plan_contracts`, `ContractModel` with records, enums, lists,
  optionality and lineage paths) and the C# renderer, which keeps its output byte-identical.
- SQL placeholders: `convert_placeholders(sql, style)` with styles `named_at` (.NET `@p0`,
  existing `to_named_parameters`), `jdbc` (keeps `?`), `dollar` (`$1`, Go pgx), `pyformat`
  (`%s`, psycopg); literals, comments and dollar-quoted strings are skipped as today.

### Per stack

Each generator writes the same parts as .NET, with the same determinism (same inputs →
byte-identical files and `build_hash`), the same manifest (`.exiqo/generated-files.txt`), the
same `RESERVED_FOR_INFRA` paths, and `GenFile.origin`/`activity_id`/`process_id`/`scaffold`:

| Part | Java 21, Spring Boot 3.5, Maven | Go, `net/http`, go modules | Python 3.12, FastAPI, pytest |
| --- | --- | --- | --- |
| build file | `pom.xml` (parent `spring-boot-starter-parent`, versions from `engine/stacks/java/versions.py`), `.exiqo/maven-settings.xml` (mirror) | `go.mod` + `go.sum` (from the pinned `engine/stacks/go/go.sum.lock`) | `pyproject.toml` + `requirements.lock` (hash-pinned, from `engine/stacks/python/requirements.lock`) |
| entry and host | `Application.java`, `SecurityConfig` (resource server, Entra JWT; `Auth__Mode=Development` → HS256 dev key), `EnvBinding` (`EnvironmentPostProcessor`, section 5) | `cmd/server/main.go`, `internal/platform/{config,auth,health,bus}.go` | `src/<pkg>/main.py`, `src/<pkg>/platform/{config,auth,bus}.py` |
| contracts | Java `record`s with Jackson `@JsonProperty` and `@TibcoPath` | structs with `json:"…"` tags and a `// TibcoPath:` comment | pydantic models with `Field(alias=…)` and `json_schema_extra={"tibco_path": …}` |
| service per process | `core/<Process>Service.java` | `internal/core/<process>_service.go` | `src/<pkg>/core/<process>_service.py` |
| REST starter | `api/<Process>Controller.java` | `internal/api/<process>_handler.go` (Go 1.22 method patterns on `http.ServeMux`) | `src/<pkg>/api/<process>_routes.py` (`APIRouter`) |
| JDBC | `infrastructure/<Process>Repository.java` (`NamedParameterJdbcTemplate`, SQL verbatim with `?`) | `internal/infrastructure/<process>_repository.go` (`database/sql` + pgx stdlib, `$n`) | `src/<pkg>/infrastructure/<process>_repository.py` (psycopg 3, `%s`) |
| JMS starter / publish | Service Bus processor + in-memory bus | same with `azservicebus` | same with `azure-servicebus` (sync client in a thread) |
| timer starter | `@Scheduled` job | goroutine with `time.Ticker` | asyncio task started in the lifespan |
| runtime helpers | `TibValue`, `TibText`, `Masking`, `ProcessFault` | `internal/tib` package | `src/<pkg>/tib.py` |
| tests | JUnit 5 + AssertJ + `@SpringBootTest(webEnvironment=RANDOM_PORT)` | `testing` + `net/http/httptest` | pytest + FastAPI `TestClient` |

Dependencies are fixed per stack in `versions.py` (no agent may add one): Java —
spring-boot-starter-web, -actuator, -oauth2-resource-server, -jdbc, postgresql,
azure-messaging-servicebus, azure-identity, azure-security-keyvault-secrets, spring-boot-starter-test,
jacoco-maven-plugin, spotless-maven-plugin. Go — `github.com/jackc/pgx/v5`,
`github.com/Azure/azure-sdk-for-go/sdk/{azidentity,messaging/azservicebus,security/keyvault/azsecrets}`,
`github.com/golang-jwt/jwt/v5`, `github.com/MicahParks/keyfunc/v3`. Python — fastapi, uvicorn,
pydantic, psycopg[binary], azure-identity, azure-servicebus, azure-keyvault-secrets,
pyjwt[crypto], httpx; dev: pytest, pytest-cov, ruff.

Ruling: Spring Boot 3.5 on Java 21 (the user's suggestion), not Spring Boot 4 on Java 25 — 3.5 is
the line GMF's Java estate and its security scanners already know, and the version table is one
file — cost if wrong: bump `versions.py` and adjust `SecurityConfig` for Boot 4's API.

Ruling: Go uses the standard library router (`http.ServeMux` with Go 1.22+ method and wildcard
patterns), not chi — one dependency fewer to mirror and scan, and the generated routes are
plain verb + path — cost if wrong: swapping the router touches one template.

Ruling: FastAPI + pytest for Python (the user's suggestion), uv for the per-build environment —
uv installs from a hash-pinned lock against a mirror offline-friendly and fast, and the image
already uses it at build time — cost if wrong: replace two runner commands with `python -m venv`
and a pip binary, which the image purges today.

Ruling: dependency integrity is pinned by ExiQO, not resolved per build — Go builds use the
committed `go.sum.lock` with `GOFLAGS=-mod=readonly`, `GOSUMDB=off`, `GOTOOLCHAIN=local`; Python
builds use `requirements.lock` with `--require-hashes`; Maven resolves exact versions from the
mirror (Maven has no lock format) — no build may reach `sum.golang.org` or resolve a version
range, and an integrity failure is a build error, not a silent upgrade — cost if wrong: refreshing
the locks is a `make stack-locks` run on a machine with mirror access.

Ruling (X19): the Java, Go and Python services run on a JSON process state (Jackson `JsonNode`,
a map, a dict) read with TibValue-style path helpers, mirroring TIBCO's untyped flow for parity,
not on typed records as the .NET services do; the generated contract records validate request
bodies at the controller boundary where that is cheap (the body is read into its record first and
a mismatch answers 400 problem+json, then the process runs on the JSON tree) — the paths are those
of the TIBCO mappings, so a JSON state translates them without a typed-shape resolver, and Verify
compares JSON anyway — cost if wrong: a wrong field name inside a process surfaces at test or run
time instead of at compile time, which the generated scenario tests and Verify catch.

Ruling: non-.NET generated tests use the database at `TEST_DATABASE_URL` only (no
Testcontainers) — worker pods have no Docker, and locally compose already provides Postgres — cost
if wrong: a developer without `TEST_DATABASE_URL` gets a clear skip message in the generated
test support, the same as .NET's no-Docker path.

## 5. One runtime contract for Verify and infrastructure

Verify (`api/app/services/verify.py`) and infrastructure (`engine/infra_gen`) talk to the
generated service only through its runtime contract. The .NET service already defines it; the
other stacks implement the same contract so Verify and infra stay stack-neutral:

- HTTP: `GET /health` (anonymous), `POST /auth/dev-token {"subject"}` (Development only),
  `POST /_bus/publish {"entity", "payload", "properties"}`, `GET /_bus/messages?entity=`,
  `GET /_bus/dead-letters` (in-memory bus only), the starter routes as in the IR, faults as
  `application/problem+json` with the fault name.
- Configuration by environment, `__` separated as ASP.NET reads it: `Auth__Mode`,
  `Messaging__Mode` (`InMemory`, `ServiceBus`), `Messaging__ServiceBus__FullyQualifiedNamespace`,
  `KeyVault__Uri`, `ConnectionStrings__<Resource>`, `Clients__<Client>__BaseAddress`,
  `Cors__Origins__<i>`, and design keys `Gmf__<Module>__<Name>`; secrets from Key Vault named
  `Gmf--<Module>--<Name>`; `PORT` (Go/Python) or `--server.port` (Java).
- Java binds these with a generated `EnvironmentPostProcessor` that maps `A__B__C` to `a.b.c`
  (keeping the original key too); Go and Python read them with their generated `config` module.
- `.exiqo/service-contract.json` (non-.NET only): `{"connections": [...], "clients": [...],
  "secrets": [{"name", "key"}], "plain_keys": [...], "port_env": "PORT"|null, "health": "/health"}`.
  `verify.connection_names` and `infra_gen._app_contract` read it when present and fall back to
  the .NET appsettings/Program.cs parsing.

Ruling: the .NET configuration convention is the contract for every stack — it lets
`verify.service_env`, `infra_gen._app_env`, the Helm/Terraform modules and the parity harness
stay as they are, and it is what operators already know from the .NET builds — cost if wrong:
non-idiomatic environment names in Spring (mitigated by the binder, which also accepts Spring's own names).

### Infrastructure per stack

`generate_infra(..., stack=...)` uses `get_stack(stack).layout(...).dockerfile_context` and the
stack's `Dockerfile.j2` under `engine/stacks/<key>/templates/`; everything else (Terraform modules,
Terragrunt tree, pipelines, compose, run-local scripts) is shared. Images come from build
arguments with short upstream defaults (`ARG BASE_IMAGE_BUILD=maven:3.9-eclipse-temurin-21`,
`ARG BASE_IMAGE_RUNTIME=eclipse-temurin:21-jre`; `golang:<go version>` and
`gcr.io/distroless/static-debian12:nonroot`; `python:3.12-slim`), non-root numeric users, and
the mirror passed as a build argument (`MAVEN_MIRROR_URL`, `GOPROXY`, `UV_INDEX_URL`). The
pipelines' build step passes the same arguments. `run-local.sh` starts the stack with compose as
for .NET.

Ruling: the Go runtime image default is a fully qualified distroless reference — there is no
short official name; it appears unquoted in a Dockerfile template, which the egress audit treats
as an image reference, not a host, as it already does for `mcr.microsoft.com` — cost if wrong: add
`gcr.io` to the allowlist's "names that are never fetched by the platform" section.

### Verify

`start_service` uses `get_stack(build.stack).runner(root).run_service(port, env, timeout)`;
`_owned_service` checks `service_process_marker`; `START_TIMEOUT_S` stays 240 s (the JVM starts in
well under that). Hosting, the run proxy, the parity drive (`drive_rest`, `drive_jms`) and the
report are unchanged.

## 6. Build pipeline

`api/app/services/build.run_build` takes the stack from the approved design (checked against the
project) and replaces each .NET call with the stack's: `generate`, `runner`, `language` for the
agents, the stack's prompt pack, `test_inventory` from the test index (non-.NET) or the C#
`[Fact]` scan (.NET), `metrics` with `build.files` counted by `source_suffixes`. The stage names
stay (`restore`, `build`, `format`, `test`, …) so the Build tab is unchanged. `NO_DOTNET` becomes a
per-stack message from `stack_status`. `Build.stack` is stored (new column, default `dotnet`),
shown in the Build tab and the export note, and projected to memory (`Solution.stack`, every
new-code uid `…:<stack>:…`).

## 7. Selection: upload and Settings

- `Project.target_stack` (new column, `NOT NULL DEFAULT 'dotnet'`). `POST /projects` accepts
  `target_stack` (optional; default the Settings default); `PATCH /projects/{id}` changes it
  (write role: `admin` or `architect`). 422 when the stack is unknown or unavailable, with the
  reason.
- Settings: `default_stack` stored in the `settings` table (admin writes, existing router
  guard); `GET /settings` gains `stacks: [{key, label, available, reason, required_tools,
  default}]` and `GET /stacks` (any signed-in role) returns the same list for the upload screen.
- Web: the Upload tab shows a stack picker (four cards with the label and availability, the
  unavailable ones disabled with the reason as tooltip) above the drop zone, preselected from the
  project; the project selector's "new project" form gets the same picker. Settings gets a
  "Target stack" panel (default, and the toolchain table extended with `java`, `mvn`, `go`,
  `python3`, `uv`). Design, Build and Verify show a stack badge. A viewer sees the picker
  disabled ("viewer is read only"); the API enforces it.

## 8. Toolchains, mirrors and the image

- `deploy/docker/platform.Dockerfile` toolchain stage adds, each fetched for `TARGETARCH` from a
  build-argument base URL and refused unless its checksum matches: Temurin JDK 21
  (`TEMURIN_BASE_URL`, `TEMURIN_VERSION`, `TEMURIN_SHA256_{AMD64,ARM64}`) into `/opt/java`;
  Maven 3.9 (`MAVEN_BASE_URL`, `MAVEN_VERSION`, `MAVEN_SHA512`) into `/opt/maven`; Go
  (`GO_BASE_URL`, `GO_VERSION`, `GO_SHA256_{AMD64,ARM64}`) into `/usr/local/go`; uv (already a
  build stage) copied to `/usr/local/bin/uv` in the final stage. Final-stage environment:
  `JAVA_HOME=/opt/java`, `MAVEN_OPTS=-Dmaven.repo.local=/cache/stacks/m2`,
  `GOMODCACHE=/cache/stacks/gomod`, `GOCACHE=/cache/stacks/gobuild`, `GOTOOLCHAIN=local`,
  `GOTELEMETRY=off`, `GOFLAGS=-mod=readonly`, `GOSUMDB=off`, `UV_CACHE_DIR=/cache/stacks/uv`,
  `UV_PYTHON_DOWNLOADS=never`, `UV_NO_PROGRESS=1`.
- Mirrors (settings, Helm `mirrors.*`, ConfigMap): `MAVEN_MIRROR_URL` (written into the
  generated `.exiqo/maven-settings.xml` as `<mirrorOf>*</mirrorOf>`), `GOPROXY` (passed to the
  Go runner; `GOPROXY=off` when empty in the chart), `UV_INDEX_URL` (and `PIP_INDEX_URL` for the
  generated Dockerfile). Empty in development means the tool's own default.
- Chart: `stacks.enabled` (default `[dotnet]`) → `ENABLED_STACKS`; `values.schema.json` requires
  `mirrors.maven` when `java` is enabled, `mirrors.goproxy` for `go`, `mirrors.pypi` for
  `python`; worker `stackCacheSize` (default 10Gi) mounts an ephemeral `/cache/stacks`; the
  worker memory limit note in values says the JVM build needs about 2 GiB per slot.
- Egress: the mirrors are GMF hosts already covered by `egress.allowCidrs`/`fqdns` (the install
  guide lists them); no new public host appears in code or templates; `make egress-audit` passes.
- `scripts/toolchain.py` `TOOLS` gains `java` (`-version`), `mvn` (`-v`), `go` (`version`),
  `python3` (`--version`), `uv` (`--version`); local `.tools/` lookups include `.tools/java/bin`,
  `.tools/maven/bin`, `.tools/go/bin`.

Ruling: the JDK, Maven and Go come from upstream release archives through GMF-overridable base
URLs with pinned checksums, the same way Terraform and Terragrunt already do — one mechanism to
audit, works on any base image, and no package manager in the final stage — cost if wrong: if GMF
prefers a JDK base image, the toolchain stage gains one more `FROM`.

## 9. Testing

- Unit (no toolchain): registry and status, naming per stack for every pattern, the hash
  compatibility rule, `ServicePlan` on both sample processes (step order, flow, agent flags equal
  to what `ProcGen` marks), expression parsing, placeholder conversion per style, test-index
  join, JUnit/test2json/Cobertura/JaCoCo/go-cover parsing from samples, `SourceLanguage` checks,
  prompt-pack parity (same names and `{placeholders}` as .NET; no `C#`, `.NET`, `xUnit`,
  `NuGet`, `csproj` in non-.NET packs).
- Golden: per stack, the repository and rules-test file of `DealerFundingRequest` and the file
  list of `gmf.credit` (`engine/tests/golden/<stack>/`), generation twice → identical
  `build_hash`; the .NET golden files unchanged.
- Toolchain tests (skip with the reason when the tool is absent, as `test_runner.py` does for
  dotnet): generate → restore → build → format check → test for each stack on the two sample
  processes, with `TEST_DATABASE_URL`; start the service and call `/health` and `/auth/dev-token`.
- API: create project with each stack, refusal of an unavailable stack, design and build carry
  the stack, the mismatch 409, viewer refused (oidc).
- E2E (Playwright): pick Java at upload, see the badge on Design and Build; Settings default.
- `scripts/demo_check.py --stack java|go|python` runs the full flow; `make demo-check` keeps
  .NET.
- Image: `make image-test` asserts `java -version`, `mvn -v`, `go version`, `uv --version`
  in the image and that `GOTOOLCHAIN=local`; `make egress-audit` passes.

## Out of scope

- Converting an existing project's builds between stacks (a new build for the new stack is the
  path), polyglot solutions (one stack per project), Kotlin/Gradle, other Python frameworks.
- Iteration re-entry (sub-project 6) and publishing to GitHub/Atlassian (sub-project 7); both
  consume `Build.stack`.
