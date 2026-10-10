# ExiQO Migrate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A seven-tab platform that takes a TIBCO BusinessWorks estate to a compiled, tested, running .NET 10 service with Terraform and Terragrunt assets, proving each step on screen.

**Architecture:** Monorepo. `engine/` is a pure Python package (parsers, IR, domains, analysis, agents, parity evaluator, .NET and infra generators, knowledge). `api/` is FastAPI over Postgres with pgvector and streams jobs over SSE. `web/` is Next.js 15 with one stepper screen and seven tabs. Everything deterministic lives in `engine/`; models only draft.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, elementpath, saxonche (XSLT 2.0) with lxml fallback, fastembed, Claude Agent SDK, openai (Azure), pytest; Next.js 15, TypeScript, Tailwind, React Flow + elkjs, Cytoscape, Recharts, Monaco, TanStack Query, Playwright; .NET 10 SDK, xUnit, Dapper, Npgsql, Testcontainers, Azure.Messaging.ServiceBus, Microsoft.Extensions.Http.Resilience; Terraform (azurerm) + Terragrunt.

**Spec:** `docs/superpowers/specs/2026-10-10-exiqo-migrate-design.md`

## Global Constraints

- Python 3.12; `engine/` has no web or database dependencies except `engine/knowledge` (SQLAlchemy core + asyncpg).
- Postgres 16 with pgvector is the only store; embeddings `BAAI/bge-small-en-v1.5`, `vector(384)`.
- Generated .NET targets `net10.0`; nullable enabled; analyzers on.
- Parsers accept CRLF and LF; unknown elements and native types are reported, never dropped.
- Keys only from environment: `CLAUDE_CODE_OAUTH_TOKEN`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT_STRONG`, `AZURE_OPENAI_DEPLOYMENT_STANDARD`, `AZURE_OPENAI_DEPLOYMENT_EFFICIENT`, `LANGFUSE_*`; `ANTHROPIC_API_KEY` is stripped from agent processes.
- Gate endpoints require header `X-User`; header `X-Agent` gets HTTP 403.
- Sensitive fields (`ssn`, `account_number`, `card_number`, any field tagged `sensitive` in config) are masked before logs, traces and embeddings.
- UI copy: no em dashes, no marketing language, light theme, RSI tokens: Poppins; `#9B5DE5`, `#20063B`, `#FF495C`, `#F4F3EE`, `#FAF9F7`; semantic grey `#425466`/`#F4F3EE` deterministic, teal `#0E7A6E`/`#E1F3F0` AI, green `#1E7B3C`/`#E2F3E6` gate, amber `#9A5E0A`/`#FBF0DF` decision, red `#A33A32`/`#F9E5E3` error.
- Every screen labels the synthetic estate as synthetic and parity output as "derived from the TIBCO model".
- Commit after every task with a conventional message.

## Review Focus

1. An upload zip containing `../` entries or absolute paths must be refused with a clear error, not written outside `data/uploads/<project>/`. Test in Task 17.
2. A `.bwp` whose activity uses an unknown `tibex:activityType` must parse, flag the type in the report, and block G1 for that process. Tests in Tasks 5 and 18.
3. Editing an approved rule's text must reopen G1, move the spec hash and reset G2 to G6 with an audit line. Test in Task 18.
4. A failing `dotnet build` (missing SDK or compile error) must surface the log on screen and leave G4 disabled, never mark the build green. Tests in Tasks 12 and 20.
5. A parity field that differs only because of a finding decided Fix must classify as "intended change", not "defect". Test in Task 10.

---

## File structure

```
exiqo-migrate/
  pyproject.toml                  workspace: engine + api, pytest config
  Makefile                        setup, dev, api, web, test, e2e, demo-check, samples-zip
  docker-compose.yml              postgres (pgvector/pgvector:pg16), langfuse profile
  .env.example
  engine/
    __init__.py
    ir/models.py                  Pydantic IR (section 4.3)
    ir/catalogue.py               NATIVE_TO_CANONICAL, CANONICAL_TYPES
    ir/complexity.py              score()
    ir/spec_hash.py               spec_hash()
    tibco_parser/scanner.py       scan_folder()
    tibco_parser/bw6.py           parse_bwp()
    tibco_parser/bw5.py           parse_process()
    tibco_parser/resources.py     parse_resource()
    tibco_parser/substvar.py      parse_substvar()
    tibco_parser/contracts.py     parse_contract()
    tibco_parser/completeness.py  CompletenessCollector
    tibco_parser/xmlutil.py       read_xml() (CRLF/LF, line numbers)
    domains/discover.py           discover_domains()
    domains/graph.py              build_graph(), io_card()
    analysis/models.py            Rule, Finding, Citation, Challenge, Coverage*
    analysis/static_rules.py      run_static_rules()
    analysis/evidence.py          EvidenceIndex
    analysis/verifier.py          verify_citations()
    analysis/coverage.py          coverage_matrix()
    agents/provider.py            LLMRequest, LLMResponse, LLMProvider, NullProvider
    agents/claude_code.py         ClaudeCodeProvider
    agents/azure_openai.py        AzureOpenAIProvider
    agents/routing.py             task_complexity(), RoutingTable, RoutedProvider
    agents/prompts/*.md           prompt templates (internal)
    agents/analyst.py             analyse()
    agents/risk.py                review_risk()
    agents/challenger.py          challenge()
    agents/designer.py            design_narrative()
    agents/builder.py             fill_method_bodies(), propose_repair()
    agents/reviewers.py           review_panel()
    agents/evals.py               run_provider_eval()
    parity/models.py              Scenario, ExpectedResult, SideEffect, FieldDiff, ParityReport
    parity/xpath.py               XPathContext
    parity/xslt.py                transform()
    parity/stubs.py               StubRegistry
    parity/db.py                  ScenarioDB (seed, transaction, execute)
    parity/evaluator.py           Evaluator
    parity/scenarios.py           generate_scenarios()
    parity/compare.py             compare()
    dotnet_gen/patterns.py        PATTERNS
    dotnet_gen/design.py          build_design_skeleton()
    dotnet_gen/csharp_contracts.py xsd_to_records(), json_schema_to_records()
    dotnet_gen/generator.py       generate_solution()
    dotnet_gen/tests_gen.py       generate_tests()
    dotnet_gen/templates/         Jinja2 templates for sln, csproj, Program.cs, controllers, repos, bus, tests
    dotnet_gen/runner.py          DotnetRunner
    dotnet_gen/trx.py             parse_trx()
    dotnet_gen/coverage.py        parse_cobertura()
    dotnet_gen/metrics.py         compute_metrics()
    dotnet_gen/repair.py          repair_loop()
    infra_gen/generator.py        generate_infra()
    infra_gen/validators.py       validate_infra()
    infra_gen/templates/          Terraform modules, terragrunt, Dockerfile, pipelines
    knowledge/schema.sql          graph, chunks, memory tables
    knowledge/store.py            KnowledgeStore (asyncpg)
    knowledge/embeddings.py       embed()
    knowledge/search.py           hybrid_search()
    knowledge/memory.py           MigrationMemory
    knowledge/ask.py              ask()
    tests/                        pytest, fixtures from samples/
  samples/synthetic-gmf/          section 9 estate; SYNTHETIC.txt
  samples/public/                 the four public zips (already on disk) for parser breadth tests
  api/
    app/main.py                   app factory, CORS, routers
    app/config.py                 Settings (pydantic-settings)
    app/db.py                     engine, session, Alembic env
    app/models/*.py               ORM
    app/schemas/*.py              Pydantic API models
    app/jobs/runner.py            JobRunner, SSE
    app/routers/{projects,processes,analysis,gates,design,build,verify,infra,settings,history,readiness,ask}.py
    app/services/*.py             orchestration per tab
    alembic/
    tests/
  web/
    app/layout.tsx, app/page.tsx (stepper + tabs), app/settings, app/history, app/readiness
    components/stepper/, tabs/{upload,discover,validate,design,build,verify,deploy}/, canvas/, graph/, evidence/
    lib/api.ts, lib/sse.ts, lib/theme.css
    e2e/flow.spec.ts
  deploy/                         platform Dockerfiles
  docs/DEMO.md                    runbook (internal)
```

---

## Milestone M0: scaffold

### Task 1: Monorepo scaffold, compose, toolchain check

**Files:**
- Create: `pyproject.toml`, `Makefile`, `docker-compose.yml`, `.env.example`, `.gitignore`, `engine/__init__.py`, `api/app/__init__.py`, `engine/tests/conftest.py`, `scripts/toolchain.py`
- Test: `engine/tests/test_toolchain.py`

**Interfaces:**
- Produces: `scripts/toolchain.py: detect() -> dict[str, ToolInfo{present: bool, version: str|None, path: str|None}]` for `dotnet`, `terraform`, `terragrunt`, `docker`, `az`, `node`.

- [ ] **Step 1: Write `engine/tests/test_toolchain.py`**: `test_detect_returns_all_keys()` asserts keys `{"dotnet","terraform","terragrunt","docker","az","node"}` and each value has `present` bool.
- [ ] **Step 2: Run** `pytest engine/tests/test_toolchain.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** `scripts/toolchain.py` with `detect()` using `shutil.which` and `--version`; `pyproject.toml` with `[project] name="exiqo-migrate"`, deps: `pydantic>=2`, `lxml`, `elementpath`, `saxonche`, `fastembed`, `jinja2`, `pyyaml`, `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]>=2`, `asyncpg`, `alembic`, `pydantic-settings`, `httpx`, `sse-starlette`, `python-multipart`, `openai`, `claude-agent-sdk`, `psycopg[binary]`; dev: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`. `docker-compose.yml` with `postgres` (`pgvector/pgvector:pg16`, db `exiqo`, port 5432) and `langfuse` under profile `observability`. `Makefile` targets: `setup`, `db`, `api`, `web`, `test`, `e2e`, `demo-check`, `toolchain`.
- [ ] **Step 4: Run** `pytest engine/tests/test_toolchain.py -v` → PASS; `make toolchain` prints the table.
- [ ] **Step 5: Install toolchains in the dev container**: .NET 10 SDK (dotnet-install script or `mcr.microsoft.com/dotnet/sdk:10.0` via Docker if direct download is refused), Terraform (or OpenTofu from GitHub releases if releases.hashicorp.com is refused; the validators accept either binary), Terragrunt from GitHub releases. Record what was installed in `docs/TOOLCHAIN.md`.
- [ ] **Step 6: Commit** `chore: scaffold monorepo, compose, toolchain detection`.

---

## Milestone M1: synthetic estate

### Task 2: Synthetic GMF-style BW6 estate

**Files:**
- Create: `samples/synthetic-gmf/SYNTHETIC.txt`, `samples/synthetic-gmf/gmf.credit.application/` (`META-INF/MANIFEST.MF`, `META-INF/TIBCO.xml`), modules `CreditOrigination/`, `DealerFunding/`, `LoanServicing/` each with `META-INF/MANIFEST.MF`, `META-INF/module.bwm`, `META-INF/default.substvar`, `Processes/<package>/*.bwp`, `Schemas/*.xsd`, `Service Descriptors/*.wsdl|*.json`, `Resources/<package>/*.jdbcResource|*.httpClientResource|*.jmsConnResource`, `Policies/`; `LegacyAddress/` BW5 project (`LegacyAddressStandardize.process`, `defaultVars/defaultVars.substvar`, `AddressService.sharedhttp`, `Schemas/Address.xsd`); `Database/schema.sql`, `Database/seed.sql`; `Stubs/equifax.json`, `Stubs/transunion.json`.
- Create: `scripts/bw6_structure_check.py`
- Test: `engine/tests/test_synthetic_structure.py`

**Interfaces:**
- Produces: the eleven processes of spec section 9 with the exact ids `creditorigination.CreditApplicationIntake`, `creditorigination.BureauOrchestrator`, `creditorigination.DecisionEngine`, `creditorigination.ValidateVIN`, `creditorigination.AuditLog`, `dealerfunding.DealerFundingRequest`, `dealerfunding.FundingStatusLookup`, `loanservicing.PaymentPosting`, `loanservicing.AccountLookup`, `loanservicing.NightlyDelinquencyScan`, `LegacyAddressStandardize`; queues `gmf.dealer.funding.request`, `gmf.payments.inbound`, `gmf.collections.queue`; topic `gmf.funding.events`; tables per spec section 9.

- [ ] **Step 1: Read the public BW6 sample on disk** (`/home/claude/poc/GMF_PoC_1_BW6_CreditApp_TN2018.zip`, unzip into a scratch folder) and record in `scripts/bw6_structure_check.py` the structural rules a `.bwp` must satisfy: root `bpws:process` with `tibex:` namespace declarations, `bpws:import` of the module XSDs, `bpws:partnerLinks`, `bpws:variables`, `bpws:extensions` with `tibex:` entries, `bpws:scope` containing `bpws:flow` with `bpws:links`, activities carrying `tibex:xpdlId` and `tibex:activityType`-equivalent attributes (`tibex:extActivity`, `tibex:receiveEvent`, `bpws:invoke`, `bpws:reply`, `bpws:throw`, `bpws:assign`), `tibex:inputBinding` XSLT, `bpws:sources`/`bpws:targets` with `bpws:transitionCondition`. The check must pass on the public sample's `LookupDatabase.bwp` and `MainProcess.bwp`.
- [ ] **Step 2: Write `engine/tests/test_synthetic_structure.py`**: parametrised over every `.bwp` under `samples/synthetic-gmf`, asserts `bw6_structure_check.check(path) == []`; plus `test_expected_processes_present()` asserting the eleven ids are discoverable by path; `test_sql_loads()` executes `schema.sql` then `seed.sql` on a temporary Postgres (Testcontainers via `testcontainers[postgres]`, add to dev deps) and asserts row counts: `dealer` 5, `loan_account` 8, `credit_application` 6.
- [ ] **Step 3: Run** → FAIL.
- [ ] **Step 4: Author the estate** per spec section 9, original content, with: REST bindings via `bw.restjson` style JSON schemas for `CreditApplicationIntake`, `FundingStatusLookup`, `AccountLookup`; JMS receivers with selectors on `DealerFundingRequest` and `PaymentPosting`; timer starter `0 0 2 * * ?` on `NightlyDelinquencyScan`; JDBC activities with the exact statements the rules need (`select ... where ssn = ?` for intake, `select ... where dealer_id like ?` for the LIKE finding, read-then-write pair in `DealerFundingRequest` without a transaction group, a `tibex:transaction` group in `PaymentPosting`); XSLT input bindings with the DTI arithmetic `number($Start/parameters/monthlyDebt) div number($Start/parameters/monthlyIncome)`; transition conditions written as BW XPath (`string-length($QueryRecords/Record[1]/rating) > 0` style); parallel `bpws:flow` for the two bureau invokes with `httpClientResource` timeouts 1000 ms and circuit breaker 50%/20; `bpws:throw` faults named `DuplicateApplicationFault`, `MissingIncomeFault`, `BureauUnavailableFault`, `DealerInactiveFault`. BW5 process with `pd:ProcessDefinition`, `pd:startType`, `pd:activity` elements, `pd:transition` with `xpath` conditions, `pd:inputBindings` XSLT.
- [ ] **Step 5: Run** the structure and SQL tests → PASS. Zip the estate with `make samples-zip` to `samples/synthetic-gmf.zip`.
- [ ] **Step 6: Commit** `feat(samples): synthetic GMF-style BW6 estate with BW5 legacy process`.

---

## Milestone M2: parsers, IR, domains, graph

### Task 3: IR models, catalogue, complexity, spec hash

**Files:**
- Create: `engine/ir/models.py`, `engine/ir/catalogue.py`, `engine/ir/complexity.py`, `engine/ir/spec_hash.py`
- Test: `engine/tests/test_ir.py`

**Interfaces:**
- Produces (Pydantic v2, all `BaseModel`):
  - `InputBinding(target: str, expression: str, language: Literal["xpath","xslt"], source_line: int|None)`
  - `Activity(id: str, name: str, type: str, native_type: str, config: dict[str, Any], input_bindings: list[InputBinding], output_schema: str|None, source_line: int|None, group_id: str|None)`
  - `Transition(id: str, from_id: str, to_id: str, kind: Literal["success","conditional","otherwise","error"], expression: str|None, source_line: int|None)`
  - `Group(id: str, kind: Literal["loop","iterate","transaction","critical","parallel","scope"], member_ids: list[str], config: dict)`
  - `Starter(activity_id: str, kind: Literal["rest","http","soap","jms","timer","subprocess"], config: dict)`
  - `FaultHandler(id: str, fault_name: str|None, activity_ids: list[str])`
  - `ProcessCall(activity_id: str, target_process: str)`
  - `UnknownElement(path: str, tag: str, reason: str)`
  - `Complexity(score: float, band: Literal["low","medium","high"], factors: dict[str, float])`
  - `Process(id, application, module, package, name, dialect: Literal["bw5","bw6"], source_path, starter: Starter|None, activities, transitions, groups, variables: list[Variable(name, schema, scope)], fault_handlers, calls, complexity: Complexity|None, unknown_elements, contracts_used: list[str], raw_xml_sha256: str)`
  - `Resource(id, kind: Literal["jdbc","http","jms","file"], name, source_path, settings: dict, masked_fields: list[str])`
  - `SubstVar(name, value, type, source_path)`; `Contract(id, kind: Literal["xsd","wsdl","json"], path, root_elements: list[str], text: str)`
  - `Application(id, name, dialect, modules: list[str], processes: list[Process], resources, substvars, contracts, sql_files: list[str])`
  - `catalogue.CANONICAL_TYPES: frozenset[str]` (spec 4.2); `catalogue.NATIVE_TO_CANONICAL: dict[str, str]` covering BW6 `bw.restjson.*`, `bw.http.*`, `bw.jdbc.*`, `bw.jms.*`, `bw.generalactivities.*`, `bw.internal.*`, `bw.xml.*`, `bw.java.*`, `bw.file.*`, BPEL `invoke|reply|throw|assign|receive|empty|wait|rethrow`, and BW5 `com.tibco.plugin.*` and `com.tibco.pe.core.*` types; `canonical(native: str) -> str|None`.
  - `complexity.score(process: Process) -> Complexity` with factors `activities, branches, mapping_size, functions, xslt, java, subprocess_depth, group_nesting, external_systems`; bands low ≤3, medium 4 to 6, high ≥7.
  - `spec_hash.spec_hash(process_ir: dict, rules: list[dict], decisions: list[dict]) -> str` SHA-256 hex over canonical JSON (`sort_keys=True, separators=(",",":")`).

- [ ] **Step 1: Write tests**: `test_canonical_known()` (`"bw.jdbc.update" -> "jdbc.update"`, `"com.tibco.plugin.jdbc.JDBCUpdateActivity" -> "jdbc.update"`, `"bw.restjson.JsonReceive"` style REST starter → `"rest.receive"`), `test_canonical_unknown_returns_none()`, `test_complexity_bands()` with a 3-activity process → low and a 25-activity 8-branch XSLT process → high, `test_spec_hash_stable_and_sensitive()` (same input twice equal; changing one rule's text changes the hash).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(ir): models, activity catalogue, complexity, spec hash`.

### Task 4: XML utilities, substvars, resources, contracts

**Files:**
- Create: `engine/tibco_parser/xmlutil.py`, `engine/tibco_parser/substvar.py`, `engine/tibco_parser/resources.py`, `engine/tibco_parser/contracts.py`
- Test: `engine/tests/test_parser_support.py`

**Interfaces:**
- `xmlutil.read_xml(path: Path) -> lxml.etree._Element` normalising CRLF, keeping `sourceline`; `xmlutil.local(tag) -> str`; `xmlutil.NS` dict of known namespaces (`bpws`, `tibex`, `pd`, `xsl`, `xsd`, `wsdl`, `jdbc`, `http`, `jms`, `tibprop`).
- `substvar.parse_substvar(path) -> list[SubstVar]` (BW6 `.substvar` and BW5 `defaultVars.substvar`; same element shape `globalVariable/name,value,type`).
- `resources.parse_resource(path) -> Resource` for `.jdbcResource`, `.httpClientResource`, `.jmsConnResource`, `.sharedjdbc`, `.sharedhttp`, `.sharedjmscon`; passwords masked (`masked_fields` lists the keys); HTTP settings keep `timeout_ms`, `circuit_breaker_failure_rate`, `circuit_breaker_window` when present.
- `contracts.parse_contract(path) -> Contract` for `.xsd` (root elements), `.wsdl` (port types and operations into `root_elements` as `PortType.Operation`), `.json` schema (`title` or top-level `$defs`).

- [ ] **Step 1: Tests** against `samples/synthetic-gmf`: `test_substvar_values()`, `test_jdbc_resource_password_masked()` (settings have `password == "***"` and `"password" in masked_fields`), `test_http_resource_resilience_settings()` (timeout 1000, failure rate 50, window 20), `test_xsd_root_elements()` (intake schema roots include `CreditApplicationRequest`, `CreditApplicationResponse`), `test_crlf_file_reads()` (write a CRLF copy to tmp and parse).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(parser): xml utilities, substvars, shared resources, contracts`.

### Task 5: BW6 parser with completeness

**Files:**
- Create: `engine/tibco_parser/bw6.py`, `engine/tibco_parser/completeness.py`
- Test: `engine/tests/test_bw6.py`

**Interfaces:**
- `completeness.CompletenessCollector` with `explain(path: str, reason: str)`, `unknown(path, tag, reason)`, `unknown_type(process_id, native_type)`, `report() -> CompletenessReport(files_found, files_parsed, failures: list[ParseFailure(path, error)], unknown_activity_types: list[UnknownType(process_id, native_type)], unknown_elements: list[UnknownElement], ok: bool)`.
- `bw6.parse_bwp(path: Path, module: str, application: str, collector: CompletenessCollector) -> Process`. Every child element of the BPEL tree is either consumed into the IR or recorded through `collector.explain` (known, intentionally not modelled, e.g. `tibex:DesignNote`, layout attributes) or `collector.unknown`. Process id is `<package>.<Name>` from the path under `Processes/`. Transition kinds: `bpws:link` with `transitionCondition` → `conditional`; `tibex:otherwise`-style or `bpws:link` without condition from a node that has conditional siblings → `otherwise`; fault links → `error`; else `success`. Activities get `source_line` from `sourceline`. `input_bindings` from `tibex:inputBinding` XSLT: one `InputBinding` per `xsl:template`-produced target path, `expression` is the `select`/`value-of` text, language `xslt` when the binding uses functions or conditionals, else `xpath`. Groups from `bpws:scope`/`bpws:flow`/`bpws:forEach`/`bpws:repeatUntil` and `tibex:` transaction/critical markers. `calls` from `bpws:invoke` whose partnerLink targets a module process (`tibex:callProcess`). `starter` from the first `tibex:receiveEvent` or `bpws:receive` carrying a binding (`rest`, `http`, `soap`, `jms`, `timer`), else `subprocess`. Native type from `tibex:activityType`-equivalent attribute (`tibex:extActivity/@...` or the activity's binding namespace); unknown → `type="unknown"` plus `collector.unknown_type`.

- [ ] **Step 1: Tests**: `test_parse_all_synthetic_bwp_zero_unknown()` (every `.bwp` parses; `report().unknown_activity_types == []`; `unknown_elements == []`); `test_dealer_funding_structure()` (starter kind `jms` with queue `gmf.dealer.funding.request`; activities include a `jdbc.query` whose config `sql` contains `like`; a `jdbc.update`; a `jms.publish` to `gmf.funding.events`; no group of kind `transaction`); `test_payment_posting_transaction_group()`; `test_bureau_parallel_flow()` (a `parallel` group with two `http.invoke` members; resource refs resolve to the HTTP resource ids); `test_decision_branches()` (conditional transitions carry the expressions with `740`, `680`, `620`; one `otherwise`); `test_unknown_activity_type_is_reported()` (copy a `.bwp` to tmp, change one activity's native type to `bw.custom.Mystery`, parse, assert `report().unknown_activity_types == [UnknownType(process_id, "bw.custom.Mystery")]` and the activity has `type == "unknown"`); `test_public_sample_parses()` parametrised over the 18 unique `.bwp` in the public TN2018 and tibcobwblog zips under `samples/public/` with zero failures.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(parser): BW6 .bwp parser with completeness report`.

### Task 6: BW5 parser and scanner

**Files:**
- Create: `engine/tibco_parser/bw5.py`, `engine/tibco_parser/scanner.py`
- Test: `engine/tests/test_bw5.py`, `engine/tests/test_scanner.py`

**Interfaces:**
- `bw5.parse_process(path: Path, project: str, collector) -> Process` (`pd:ProcessDefinition`; `pd:starter` → starter; `pd:activity` → activities with `pd:type` as native type; `pd:transition` with `pd:xpath` → conditional, `pd:conditionType == "otherwise"` → otherwise, `error` → error; `pd:group` → groups; `pd:inputBindings` → bindings; `pd:activity[@type='com.tibco.pe.core.CallProcessActivity']` → calls).
- `scanner.FileEntry(path: str, kind: Literal["bwp","process","bwm","xsd","wsdl","json","substvar","resource","sql","manifest","other"], dialect: Literal["bw5","bw6",None], sha256: str, duplicate_of: str|None)`
- `scanner.ScanResult(applications: list[Application], report: CompletenessReport, files: list[FileEntry], synthetic: bool)`
- `scanner.scan_folder(root: Path) -> ScanResult`: discovers BW6 applications (folders with `META-INF/module.bwm`, grouped by `.application` manifests or by parent folder) and BW5 projects (folders containing `.process`), flags `.application/`-packaged duplicates by sha256 as `duplicate_of`, collects SQL files, sets `synthetic=True` when `SYNTHETIC.txt` is present.

- [ ] **Step 1: Tests**: `test_bw5_legacy_address()` (starter `http`, a `mapper`, conditional transition with the state list, finding-seed: no `otherwise`), `test_bw5_public_codereview_parses()` over the 96 public BW5 files (zero failures), `test_scan_synthetic()` (`len(applications) == 2` (`gmf.credit`, `LegacyAddress`), eleven processes, `report.ok`, `synthetic`), `test_scan_flags_packaged_duplicates()` on the public TN2018 zip (two `duplicate_of` entries).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(parser): BW5 parser and estate scanner`.

### Task 7: Domain discovery, graph, I/O card

**Files:**
- Create: `engine/domains/discover.py`, `engine/domains/graph.py`, `engine/domains/models.py`
- Test: `engine/tests/test_domains.py`

**Interfaces:**
- `models.Evidence(kind: Literal["module","table","queue","call","name"], text: str)`; `models.DomainCandidate(id: str, process_ids: list[str], evidence: list[Evidence], suggested_name: str)`; `models.Node(id, kind: Literal["domain","application","process","activity","table","queue","topic","endpoint","resource"], name, attrs: dict)`; `models.Edge(src, dst, kind: Literal["contains","calls","reads","writes","publishes","consumes","exposes","uses"], attrs: dict)`; `models.Graph(nodes, edges)`; `models.IOCard(process_id, trigger: dict, request_contract: dict, response_contract: dict, side_effects: list[dict], faults: list[str], subprocesses: list[str])`.
- `discover.discover_domains(apps: list[Application]) -> list[DomainCandidate]`: union-find over processes joined by module membership, shared tables, shared queues or topics, and call edges; `suggested_name` is the module name title-cased (AI renaming happens in the API, Task 15).
- `graph.build_graph(apps) -> Graph`; tables extracted from SQL text with a conservative regex over `from|join|update|insert into|delete from`; `graph.io_card(process: Process, app: Application, graph: Graph) -> IOCard`.

- [ ] **Step 1: Tests**: `test_three_domains_from_modules()` (CreditOrigination, DealerFunding, LoanServicing plus LegacyAddress as its own), `test_graph_edges()` (`DealerFundingRequest` writes `funding`, publishes `gmf.funding.events`, consumes `gmf.dealer.funding.request`; `CreditApplicationIntake` calls `BureauOrchestrator`), `test_io_card_intake()` (trigger `{"kind":"rest","verb":"POST","path":"/applications"}`, response fields include `decision`, `tier`, `apr`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(domains): discovery, estate graph, I/O cards`.

---

## Milestone M3: analysis, agents, gates, memory

### Task 8: Analysis models, static rules, evidence, verifier, coverage

**Files:**
- Create: `engine/analysis/models.py`, `engine/analysis/static_rules.py`, `engine/analysis/evidence.py`, `engine/analysis/verifier.py`, `engine/analysis/coverage.py`
- Test: `engine/tests/test_analysis.py`

**Interfaces:**
- `models.Citation(activity_id: str, kind: Literal["sql","expression","condition","config","schema"], text: str)`; `Challenge(verdict: Literal["agree","disagree"], reason: str, model: str)`; `Rule(id: str, text: str, citations: list[Citation], confidence: float, status: Literal["draft","approved","edited","rejected","unverified"], challenge: Challenge|None, memory_refs: list[str], owner: str|None)`; `Finding(id: str, severity: Literal["high","medium","low"], text: str, citations: list[Citation], source: Literal["static","ai"], decision: Literal["keep","fix","ask"]|None, note: str|None, business_impact: str|None)`; `VerificationResult(verified: int, total: int, failures: list[CitationFailure(rule_id, citation, reason)])`; `CoverageItem(kind: Literal["activity","transition","mapping"], ref: str, covered_by: list[str], technical_reason: str|None)`; `CoverageResult(covered: int, total: int, items: list[CoverageItem])`.
- `static_rules.run_static_rules(process: Process, contracts: list[Contract], substvars: list[SubstVar]) -> list[Finding]` implementing: `S1` read-then-write on the same table without a transaction group, `S2` `like` on a column named in `{ssn, dealer_id, account_number, vin}` or any identifier column, `S3` hard-coded URL or host in an expression or config, `S4` schema field never populated by any mapping (response contracts), `S5` conditional set with no otherwise, `S6` catch with no activities, `S7` sensitive field written to a log or audit insert.
- `evidence.EvidenceIndex(process)` with `has(activity_id, kind, text) -> bool` (whitespace-normalised substring match against the activity's SQL, binding expressions, outgoing transition expressions, config values, output schema name).
- `verifier.verify_citations(process, rules: list[Rule]) -> VerificationResult`, marking rules `unverified` in place.
- `coverage.coverage_matrix(process, rules, technical: dict[str, str]) -> CoverageResult` where `technical` maps `ref -> reason` for items declared technical by the analyst.

- [ ] **Step 1: Tests** on the synthetic estate: `test_static_dealer_funding_s1_s2()`, `test_static_audit_log_s7()`, `test_static_legacy_no_otherwise_s5()`, `test_static_nightly_hardcoded_s3()`, `test_static_intake_s4_unused_field()` (response schema field `transUnionScore` never mapped), `test_verifier_rejects_paraphrase()` (a citation quoting `numofpulls+1` when the source says `numofpulls + 1` passes after whitespace normalisation, but `pulls plus one` fails), `test_coverage_counts()`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(analysis): static rules, evidence index, citation verifier, coverage`.

### Task 9: Providers, routing, agents, evals

**Files:**
- Create: `engine/agents/provider.py`, `engine/agents/claude_code.py`, `engine/agents/azure_openai.py`, `engine/agents/routing.py`, `engine/agents/prompts/{analyst,risk,challenger,domain_namer,designer,builder,repair,test_engineer,reviewer_equivalence,reviewer_security,reviewer_standards}.md`, `engine/agents/analyst.py`, `engine/agents/risk.py`, `engine/agents/challenger.py`, `engine/agents/domain_namer.py`, `engine/agents/evals.py`, `engine/agents/jsonout.py`
- Test: `engine/tests/test_agents.py`

**Interfaces:**
- `provider.LLMRequest(system: str, messages: list[dict], model: str, max_tokens: int, json_schema: dict|None, cache: bool, tags: dict[str,str])`; `LLMResponse(text, usage_in: int, usage_out: int, cached_tokens: int, cost_usd: float, latency_ms: int, model: str, provider: str)`; `class LLMProvider(Protocol): name: str; async def complete(req) -> LLMResponse; def models() -> dict[Literal["strong","standard","efficient"], str]`; `NullProvider` raises `ProviderUnavailable`; `FakeProvider(responses: list[str])` for tests.
- `claude_code.ClaudeCodeProvider()` using the Claude Agent SDK with `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY` removed from the child environment; models from `MODEL_STRONG|STANDARD|EFFICIENT` env (defaults `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-4-5`).
- `azure_openai.AzureOpenAIProvider()` using `openai.AsyncAzureOpenAI` and the three deployment env vars.
- `routing.task_complexity(process: Process, task_kind: Literal["analyse","challenge","design","build","test","repair","name"]) -> float` per spec 5.4; `RoutingTable(bands: dict[str, tuple[int,int]])` default strong 8 to 10, standard 4 to 7, efficient 0 to 3; `RoutedProvider(primary: LLMProvider, fallback: LLMProvider|None, table: RoutingTable, recorder: Callable[[ModelCallRecord], Awaitable[None]])` with `complete_for(task_kind, process, req) -> LLMResponse` that picks the tier, retries on the fallback on `ProviderError`, and records `ModelCallRecord(task_kind, complexity, tier, provider, model, usage, cost, latency, fell_back: bool, tags)`.
- `jsonout.parse_json_block(text) -> dict` tolerant of fences; agents request `json_schema` output.
- `analyst.analyse(process, contracts, memory_items: list[MemoryItem], provider: RoutedProvider) -> AnalystOutput(summary: str, rules: list[Rule], technical: dict[str,str])`; `risk.review_risk(process, static_findings, provider) -> list[Finding]` (adds `business_impact` to static findings, may add `source="ai"` findings); `challenger.challenge(process, rules, provider) -> list[Rule]` (fills `challenge`; prompt includes the IR and rules only); `domain_namer.name_domains(candidates, provider) -> list[DomainCandidate]`.
- `evals.run_provider_eval(processes, providers: list[LLMProvider], contracts) -> list[EvalRow(provider, model, process_id, citations_verified, citations_total, coverage, coverage_total, challenger_agree, rules, latency_ms, cost_usd)]`.
- Prompts wrap TIBCO content in `<source>` blocks with the instruction that it is data, and require citations to quote text verbatim.
- `provider.mask_sensitive(text: str) -> str` masks SSN patterns (`\d{3}-\d{2}-\d{4}`) and values of fields named in the sensitive list before any text reaches a log, a trace or `ModelCallRecord.tags`; `test_mask_sensitive()` covers it.

- [ ] **Step 1: Tests** with `FakeProvider`: `test_routing_bands()`, `test_routed_provider_falls_back_and_records()`, `test_analyst_parses_rules_and_marks_unverified()` (fake returns one good citation and one paraphrase; after `verify_citations` one rule is `unverified`), `test_challenger_prompt_excludes_reasoning()` (captured prompt lacks the analyst summary), `test_eval_rows()`; `test_claude_provider_env_strips_api_key()` (unit: the child env built by the provider has no `ANTHROPIC_API_KEY`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(agents): providers, complexity routing, analyst, risk, challenger, evals`.

### Task 10: Knowledge store, embeddings, hybrid search, migration memory, Ask

**Files:**
- Create: `engine/knowledge/schema.sql`, `engine/knowledge/store.py`, `engine/knowledge/embeddings.py`, `engine/knowledge/search.py`, `engine/knowledge/memory.py`, `engine/knowledge/ask.py`
- Test: `engine/tests/test_knowledge.py` (Testcontainers Postgres with pgvector image)

**Interfaces:**
- `schema.sql`: `graph_nodes(id text pk, project_id, kind, name, attrs jsonb)`, `graph_edges(src, dst, kind, attrs)`, `chunks(id, project_id, process_id, kind: summary|rule|finding|decision|design, text, embedding vector(384), tsv tsvector)`, `memory_items(id, project_id, kind: decision|pattern|mapping|note|seed, text, context jsonb, source text, embedding vector(384), tsv, created_by, created_at)`; ivfflat cosine indexes; GIN on tsv.
- `embeddings.embed(texts: list[str]) -> list[list[float]]` (fastembed, 384).
- `store.KnowledgeStore(dsn)` with `ensure_schema()`, `load_graph(project_id, graph: Graph)`, `upsert_chunks(project_id, process_id, items: list[(kind, text)])`, `find_dependents(project_id, node_id) -> list[Node]`, `neighbors(node_id)`.
- `search.hybrid_search(store, project_id, query, k=8, vector_floor=0.66, min_terms=2) -> list[Hit(id, kind, process_id, text, score)]`.
- `memory.MigrationMemory(store)` with `record(project_id, kind, text, context: dict, source: str, created_by: str) -> str`, `retrieve(project_id, query: str, kinds: list[str], k=6) -> list[MemoryItem]`, `seed_rsi_patterns(project_id)` loading `engine/knowledge/seed_patterns.yaml` (RSI-authored TIBCO-to-.NET patterns, each `source: rsi-seed`).
- `ask.ask(store, memory, project_id, question, provider: RoutedProvider|None) -> Answer(text, citations: list[Hit])`; with no provider, returns the top hits as the answer.

- [ ] **Step 1: Tests**: `test_schema_and_graph_roundtrip()`, `test_hybrid_search_exact_identifier()` (`numofpulls`-style identifier found by keyword half), `test_hybrid_search_off_topic_returns_empty()` ("capital of France"), `test_memory_record_and_retrieve()`, `test_raw_xml_never_embedded()` (upsert refuses text containing `<bpws:` or `<pd:`), `test_seed_patterns_load()`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(knowledge): graph store, hybrid search, migration memory, ask`.

---

## Milestone M4: parity evaluator

### Task 11: Scenario models, XPath context, XSLT, stubs, scenario DB

**Files:**
- Create: `engine/parity/models.py`, `engine/parity/xpath.py`, `engine/parity/xslt.py`, `engine/parity/stubs.py`, `engine/parity/db.py`
- Test: `engine/tests/test_parity_support.py`

**Interfaces:**
- `models.Scenario(id, process_id, name, kind: Literal["happy","branch","fault","external_failure","sme"], input: dict, stub_responses: dict[str, StubResponse(status: int, body: dict|None, delay_ms: int)], seed_state: list[SeedRow(table, row: dict)], expected: ExpectedResult|None, source: Literal["generated","sme"])`; `SideEffect(kind: Literal["row_written","message_published","service_called","subprocess_called"], target: str, payload: dict)`; `Fault(name: str, data: dict)`; `TraceStep(activity_id, input: dict, output: dict, chosen_transition: str|None)`; `ExpectedResult(output: dict, side_effects: list[SideEffect], fault: Fault|None, trace: list[TraceStep], source: Literal["model","sandbox"])`; `FieldDiff(path, expected, actual, status: Literal["match","defect","intended","noise"], finding_id: str|None)`; `ParityReport(scenario_id, fields: list[FieldDiff], side_effects: list[FieldDiff], parity: float, unexplained: int)`.
- `xpath.XPathContext(variables: dict[str, lxml element], substvars: dict[str,str])` with `evaluate(expr: str) -> Any` via `elementpath` XPath 2.0, supporting `$Var/path`, `bw:getModuleProperty('name')`, `tib:` date functions used by the estate.
- `xslt.transform(stylesheet: str, context: XPathContext) -> lxml element` using `saxonche`, falling back to lxml with a `warnings.warn("xslt downgraded to 1.0")`.
- `stubs.StubRegistry` with `register(resource_id, path, response: StubResponse)` and `call(resource_id, path, body) -> StubResponse | TimeoutError`.
- `db.ScenarioDB(dsn)` with `reset(schema_sql, seed_sql, seed_rows)`, `transaction()` context, `execute(sql, params) -> list[dict] | int`.
- `models.dict_to_xml(d: dict, root: str) -> element` and `xml_to_dict(el) -> dict` for the input and output boundaries.

- [ ] **Step 1: Tests**: `test_xpath_variable_path()`, `test_xpath_dti_arithmetic()` (`number(..) div number(..)` gives 0.3), `test_xslt_binding_transform()` (a DecisionEngine binding produces `tier`), `test_stub_timeout()`, `test_scenario_db_reset_and_transaction_rollback()`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(parity): scenario models, xpath, xslt, stubs, scenario db`.

### Task 12: Evaluator, scenario generator, comparison

**Files:**
- Create: `engine/parity/evaluator.py`, `engine/parity/scenarios.py`, `engine/parity/compare.py`
- Test: `engine/tests/test_evaluator.py`

**Interfaces:**
- `evaluator.Evaluator(app: Application, db: ScenarioDB, stubs: StubRegistry)` with `async run(process_id: str, scenario: Scenario) -> ExpectedResult` executing per spec 6.2: starter input → activities in link order → bindings → execute by canonical type → variables → transitions by `XPathContext.evaluate` (first true conditional, else otherwise) → reply/end → `ExpectedResult(source="model")`. `call.process` recurses. Faults propagate to the nearest `catch` or surface as `fault`.
- `scenarios.generate_scenarios(process, contracts, app) -> list[Scenario]` per spec 6.3: happy path values from XSD types and the estate's seed data; one per conditional transition by solving predicates of the forms `$x > N`, `$x >= N`, `$x < N`, `string-length($x) > 0`, `exists($x)`, `$x = 'A'`, membership in an enumeration; one per `throw`; one per HTTP stub failure.
- `compare.compare(expected: ExpectedResult, actual: ExpectedResult, noise_rules: list[NoiseRule(path_glob: str, reason: str)], fix_findings: list[Finding], lineage: dict[str, list[str]]) -> ParityReport`; a field whose lineage touches an activity cited by a finding decided `fix` classifies `intended`.

- [ ] **Step 1: Tests**: `test_intake_happy_path_expected()` (tier `A`, apr `5.49`, audit row written, bureau pulls recorded), `test_decision_branch_scenarios_cover_all_tiers()` (four branch scenarios plus the fault), `test_bureau_single_bureau_flag_on_timeout()`, `test_dealer_funding_publishes_event()`, `test_payment_posting_late_fee()` (posting 11 days after due date adds 25.00; 10 days does not, because the source uses `>=`? No: assert the source's exact behaviour: `>=` means day 10 adds the fee), `test_compare_intended_change()` (expected 25.00 vs actual 0.00 on day 10 with finding `S_grace` decided fix → `intended`), `test_compare_defect()`, `test_compare_noise()`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(parity): model-derived evaluator, scenario generation, field comparison`.

---

## Milestone M5: .NET generation

### Task 13: Pattern catalogue, design skeleton, contracts to C#

**Files:**
- Create: `engine/dotnet_gen/patterns.py`, `engine/dotnet_gen/design.py`, `engine/dotnet_gen/csharp_contracts.py`, `engine/dotnet_gen/models.py`
- Test: `engine/tests/test_design.py`

**Interfaces:**
- `models.MappingRow(activity_id, activity_name, canonical_type, pattern: str, project: str, file: str, class_name: str, method: str, rule_ids: list[str], finding_ids: list[str], origin: Literal["code","agent"])`; `DesignRecord(process_id, spec_hash, solution: SolutionLayout(name, projects: list[ProjectLayout(name, kind: Literal["api","core","infrastructure","tests"], files: list[str])]), mapping: list[MappingRow], contracts: list[ContractMap(contract_id, record_names)], config: list[ConfigMap(substvar, key, secret: bool)], messaging: list[MessagingEntity(kind: queue|topic|subscription, name, direction)], resilience: list[ResilienceMap(resource_id, timeout_ms, retry, breaker)], sequence: list[SequenceStep(from_, to, label)], decisions: list[Decision(title, choice, alternatives, reason)], open_questions: list[OpenQuestion(question, recommendation)], expected_differences: list[ExpectedDifference(finding_id, description)], narrative: str|None, design_hash: str)`.
- `patterns.PATTERNS: dict[str, PatternSpec(name, project, file_template, class_template, method_template, nuget: list[str], description)]` covering every canonical type in spec 7.2.
- `design.build_design_skeleton(process, app, rules, findings, io_card) -> DesignRecord` (deterministic; `narrative=None`; `design_hash` SHA-256 of the record without narrative).
- `csharp_contracts.xsd_to_records(contract: Contract, namespace: str) -> str` and `json_schema_to_records(contract, namespace) -> str` producing `public sealed record` types with `[JsonPropertyName]` and a `[TibcoPath("...")]` attribute per property.

- [ ] **Step 1: Tests**: `test_every_canonical_type_has_pattern()`, `test_design_dealer_funding_mapping()` (jdbc query → `DealerFundingRepository.FindDealer`, jms receive → `DealerFundingConsumer`, publish → `IMessageBus.PublishAsync("gmf.funding.events")`, finding ids attached to the LIKE row), `test_design_config_secret_detection()` (password-like substvars → `secret=True`), `test_xsd_to_records_intake()` (contains `public sealed record CreditApplicationRequest` with `Ssn` and `MonthlyIncome`), `test_design_hash_ignores_narrative()`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(dotnet): pattern catalogue, design record skeleton, contract records`.

### Task 14: Solution generator, tests generator, runner, TRX, coverage, metrics

**Files:**
- Create: `engine/dotnet_gen/generator.py`, `engine/dotnet_gen/tests_gen.py`, `engine/dotnet_gen/templates/*.j2`, `engine/dotnet_gen/runner.py`, `engine/dotnet_gen/trx.py`, `engine/dotnet_gen/coverage.py`, `engine/dotnet_gen/metrics.py`
- Test: `engine/tests/test_generator.py` (golden files under `engine/tests/golden/`), `engine/tests/test_runner.py` (skipped when `dotnet` is absent, with the skip reason printed)

**Interfaces:**
- `generator.GenFile(path: str, content: str, origin: Literal["code","agent"], activity_id: str|None)`; `GeneratedSolution(root: Path, name: str, files: list[GenFile], build_hash: str)`.
- `generator.generate_solution(app, process_ids: list[str], design: DesignRecord, rules, findings, scenarios, out_dir: Path) -> GeneratedSolution`: `<App>.sln`, `Directory.Build.props` (net10.0, nullable, `TreatWarningsAsErrors=false`, analyzers), projects `Api`, `Core`, `Infrastructure`, `Tests`; `Program.cs` with Swagger, `Auth:Mode` handling and `POST /auth/dev-token` registered only in Development; controllers per REST starter; `IMessageBus` + `InMemoryMessageBus` + `ServiceBusMessageBus`; repositories with the SQL verbatim and `/// <remarks>Rules: R1, R3. Findings: F2 (kept). TIBCO: UpdatePulls line 948</remarks>`; mapping methods with the TIBCO expression as a comment and body `throw new NotImplementedException("agent fills: <activity>")` for `origin=agent` rows and generated code for simple paths; `appsettings.json` with keys and `KeyVault` references for secrets; `Dockerfile` placeholder path reserved for Task 16.
- `tests_gen.generate_tests(process, rules, scenarios, design) -> list[GenFile]`: `<Process>RulesTests.cs` with `[Fact(DisplayName="R1 ...")] public Task R1_slug()` whose body is `Assert.Fail("no test body for R1")` until filled; `<Process>BranchTests.cs`; `<Process>ScenarioTests.cs` driving the controller or consumer with the scenario input and asserting the expected output fixture JSON under `Tests/Fixtures/<scenario>.json`; `TestDatabase.cs` with Testcontainers Postgres seeded from `Database/`.
- `runner.DotnetRunner(root: Path, dotnet: str = "dotnet")` with `restore() -> StepResult(ok, log)`, `build() -> BuildResult(ok, warnings: int, errors: list[str], log)`, `format_check() -> StepResult`, `test() -> TestRun(ok, cases: list[TestCase(name, display_name, outcome: passed|failed|skipped, message, rule_id, branch_id, scenario_id, duration_ms)], coverage_line: float|None, trx_path, log)`, `run_service(project: str, port: int, env: dict) -> ServiceHandle(pid, url, stop())` with health wait on `GET /health`.
- `trx.parse_trx(path) -> list[TestCase]`; `coverage.parse_cobertura(path) -> float`.
- `metrics.compute_metrics(rules, branches: int, test_run: TestRun, build: BuildResult, format_ok: bool, infra_ok: bool|None) -> Metrics(rule_carry_forward, test_pass_rate, branch_coverage, code_coverage, standards_adherence, compile_ok, warnings, infra_ok, formulas: dict[str,str])` per spec 2.5.

- [ ] **Step 1: Tests**: `test_generate_solution_golden()` (file list and three golden files: `Directory.Build.props`, `DealerFundingRepository.cs`, `DealerFundingRequestRulesTests.cs`), `test_sql_verbatim_in_repository()`, `test_dev_token_endpoint_only_in_development()`, `test_parse_trx_sample()` (fixture TRX with 3 passed, 1 failed), `test_metrics_formulas()`, `test_runner_builds_scaffold()` (requires `dotnet`: scaffold for `FundingStatusLookup` compiles with `build().ok` and the rule tests fail with "no test body").
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS (runner test runs where `dotnet` is present). **Step 5: Commit** `feat(dotnet): solution and test generation, dotnet runner, TRX, coverage, metrics`.

### Task 15: Designer narrative, builder, reviewers, repair loop

**Files:**
- Create: `engine/agents/designer.py`, `engine/agents/builder.py`, `engine/agents/reviewers.py`, `engine/dotnet_gen/repair.py`
- Test: `engine/tests/test_build_agents.py`

**Interfaces:**
- `designer.design_narrative(design: DesignRecord, process, rules, findings, memory_items, provider) -> DesignRecord` filling `narrative`, `decisions`, `open_questions` (never changing `mapping`, `design_hash` unchanged).
- `builder.fill_method_bodies(solution: GeneratedSolution, design, process, rules, memory_items, provider) -> list[GenFile]` returning only `origin=agent` files with bodies filled; prompt carries the TIBCO expression, the target signature, the rules and the retrieved memory patterns.
- `reviewers.review_panel(solution, design, rules, findings, provider) -> list[ReviewFinding(reviewer: equivalence|security|standards, file, line, text, triage: patch|defer|decision)]`.
- `repair.repair_loop(runner: DotnetRunner, solution, process, rules, provider, max_attempts=5, recorder) -> RepairResult(attempts: list[RepairAttempt(n, failing: list[str], diff: str, applied: bool)], final: TestRun)`.

- [ ] **Step 1: Tests** with `FakeProvider`: `test_narrative_keeps_hash()`, `test_builder_only_touches_agent_files()`, `test_repair_stops_at_five()` (fake runner always fails → 5 attempts, `final.ok is False`), `test_repair_never_edits_tests()` (a proposed diff touching `Tests/` is rejected and logged).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(agents): designer narrative, code builder, review panel, repair loop`.

---

## Milestone M6: infrastructure generation

### Task 16: Terraform modules, Terragrunt live, Dockerfile, pipelines, validators

**Files:**
- Create: `engine/infra_gen/generator.py`, `engine/infra_gen/validators.py`, `engine/infra_gen/templates/modules/{resource_group,container_registry,container_app,aks_workload,postgres_flexible,service_bus,key_vault,monitoring,identity}/{main.tf,variables.tf,outputs.tf}`, `templates/live/terragrunt.hcl.j2`, `templates/live/env_module.hcl.j2`, `templates/Dockerfile.j2`, `templates/dockerignore`, `templates/compose.override.yml.j2`, `templates/github_workflow.yml.j2`, `templates/azure-pipelines.yml.j2`
- Test: `engine/tests/test_infra.py`

**Interfaces:**
- `generator.generate_infra(app, design: DesignRecord, solution: GeneratedSolution, envs=("dev","test","prod"), runtime: Literal["container_app","aks"]="container_app") -> list[GenFile]` writing under `<solution root>/infra/` and the solution root per spec section 8; Service Bus entities from `design.messaging`; Key Vault secret names from `design.config` where `secret`; ingress from REST starters; `azurerm` provider `~> 4.0`; remote state `azurerm` backend keyed `<app>/<env>/<module>.tfstate`.
- `validators.validate_infra(root: Path, terraform: str|None, terragrunt: str|None) -> InfraValidation(fmt_ok: bool|None, modules: list[ModuleValidation(name, init_ok, validate_ok, log)], hclfmt_ok: bool|None, tools_present: dict[str,bool], ok: bool)`; `None` when a tool is absent, `ok` false in that case with the reason.

- [ ] **Step 1: Tests**: `test_infra_file_set()` (expected paths for three envs and nine modules), `test_service_bus_entities_from_design()` (queue `gmf.dealer.funding.request`, topic `gmf.funding.events` appear in `live/dev/<app>/service_bus/terragrunt.hcl` inputs), `test_dockerfile_targets_net10()`, `test_validate_infra_passes()` (skipped with reason when `terraform`/`tofu` or `terragrunt` is absent).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(infra): terraform modules, terragrunt live config, dockerfile, pipelines, validators`.

---

## Milestone M7: API and web

### Task 17: API foundation: config, DB, ORM, jobs, uploads, scan

**Files:**
- Create: `api/app/main.py`, `api/app/config.py`, `api/app/db.py`, `api/app/models/{project,process,analysis,gate,design,build,verify,infra,job,settings}.py`, `api/app/schemas/*.py`, `api/app/jobs/runner.py`, `api/app/routers/projects.py`, `api/app/routers/processes.py`, `api/app/services/scan.py`, `api/alembic/*`
- Test: `api/tests/test_projects.py`, `api/tests/test_jobs.py`

**Interfaces:**
- `POST /projects {name}` → `{id}`; `POST /projects/{id}/upload` multipart zip or `{path}` JSON → `{upload_id, files: [FileEntry]}`; refuses zip entries with absolute paths or `..` with 400 `{"detail":"unsafe path: ..."}`; writes under `data/uploads/<project>/`.
- `POST /projects/{id}/scan` → `{job_id}`; job runs `scan_folder`, persists applications, processes (IR JSON), report, files; `GET /projects/{id}/report` → CompletenessReport plus `synthetic: bool`.
- `GET /processes/{id}` (IR), `GET /processes/{id}/canvas` (nodes and edges for React Flow with `source_line`), `GET /processes/{id}/xml` (text), `GET /processes/{id}/io-card`.
- `GET /jobs/{id}` and `GET /jobs/{id}/events` (SSE, `event: log|stage|done|error`, `data` JSON with `tag: code|ai`).
- `jobs.JobRunner.submit(kind, payload, run: Callable[[JobContext], Awaitable[dict]]) -> job_id`; `JobContext.log(text, tag)`, `stage(name)`.
- `config.Settings` reads `PROVIDER=auto|fake|claude|azure` (default `auto`: Claude if its token is present, else Azure, else `NullProvider`), `DATABASE_URL`, `DATA_DIR`, `APP_SECRET`, `MODEL_*`, `AZURE_OPENAI_*`, `LANGFUSE_*`.

- [ ] **Step 1: Tests** (httpx `AsyncClient`, Testcontainers Postgres): `test_upload_refuses_unsafe_zip()`, `test_upload_and_scan_synthetic()` (job completes; eleven processes; `synthetic` true), `test_sse_streams_log_events()`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(api): projects, upload, scan, process views, job runner with SSE`.

### Task 18: API: domains, analysis, rules, findings, gates, audit, memory, ask

**Files:**
- Create: `api/app/routers/{domains,analysis,gates,ask}.py`, `api/app/services/{domains,analysis,gates}.py`
- Test: `api/tests/test_analysis_gates.py`

**Interfaces:**
- `GET /projects/{id}/domains` → `[{id, name, suggested_name, process_ids, evidence, renamed_by}]`; `PATCH /domains/{id} {name}`; `POST /domains/{a}/merge/{b}`; `GET /projects/{id}/graph`.
- `POST /processes/{id}/analyse` → job: static rules, analyst, verifier, coverage, risk, challenger; persists rules, findings, coverage, challenges, model calls; `GET /processes/{id}/analysis` → `{summary, rules, findings, verification, coverage, spec_hash, gates}`.
- `PATCH /rules/{id} {status, text?}` (edit sets `status=edited`, re-verifies, recomputes spec hash, resets G1 to G6 with audit line `rule edited`); `PATCH /findings/{id} {decision, note}`.
- `POST /gates/{process_id}/{gate} {state: approved|rejected, note}` with `X-User`; `X-Agent` → 403; enablement rules per spec 5.2 enforced server side with the reason in 409.
- `GET /processes/{id}/audit`; `POST /ask {project_id, question}` → `Answer`.
- Every SME decision (rule edit, finding decision, domain rename) is recorded through `MigrationMemory.record`.

- [ ] **Step 1: Tests**: `test_analyse_without_provider_uses_static_only_and_says_so()`, `test_agent_header_forbidden_on_gate()`, `test_g1_requires_all_rules_decided_and_checks()`, `test_rule_edit_resets_gates_and_hash()`, `test_unknown_activity_type_blocks_g1()`, `test_decisions_recorded_in_memory()`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(api): domains, analysis pipeline, rules, findings, gates, audit, ask`.

### Task 19: API: design, build, verify, infra, plan, settings, history, readiness, evals

**Files:**
- Create: `api/app/routers/{design,build,verify,infra,settings,history,readiness}.py`, `api/app/services/{design,build,verify,infra,readiness,providers}.py`
- Test: `api/tests/test_build_verify.py`

**Interfaces:**
- `POST /processes/{id}/design` → job (skeleton, narrative); `GET /processes/{id}/design` → DesignRecord; G3 via gates endpoint.
- `POST /processes/{id}/build {runtime}` → job: generate solution, generate tests, builder, reviewers, restore, build, format, test, repair loop, infra generate, infra validate, metrics; `GET /builds/{id}` → `{status, build_hash, metrics, steps: [{name, ok, log}], repair: RepairResult}`; `GET /builds/{id}/files?origin=` → tree; `GET /builds/{id}/files/{path}` → content with `tibco_ref`; `GET /builds/{id}/tests` → cases; `GET /builds/{id}/reviews`.
- `POST /builds/{id}/run` → `{url, port}`; `DELETE /builds/{id}/run`; `GET /builds/{id}/run/logs`.
- `GET /processes/{id}/scenarios`; `POST /processes/{id}/scenarios` (SME scenario); `POST /builds/{id}/verify` → job: for each scenario, evaluator expected vs actual via HTTP (REST starters) or the running service's in-memory bus endpoint `POST /_bus/publish` (JMS starters; exposed only in Development); `GET /builds/{id}/parity` → `{reports: [ParityReport], parity: float, unexplained: int, source: "model"}`; `GET /processes/{id}/test-data` → generated rows with masking applied.
- `GET /builds/{id}/infra` → files and `InfraValidation`; `POST /projects/{id}/azure {tenant, subscription, ...}` stored encrypted (Fernet key from `APP_SECRET`); `POST /builds/{id}/plan` → job running `terragrunt run-all plan` when connected, else returns `{connected: false, last_validation}`; `GET /builds/{id}/plan`.
- `GET/PUT /settings` (providers presence, models per stage, routing bands, toolchains via `scripts.toolchain.detect`, Langfuse); `POST /providers/eval {process_ids}` → job → `GET /providers/eval/{id}`.
- `GET /history?project_id=` → jobs, model calls, decisions on one timeline with totals by phase and per workflow; `GET /projects/{id}/readiness` → rows per spec 2.10 computed from evidence presence.

- [ ] **Step 1: Tests**: `test_design_then_g3()`, `test_build_fails_without_dotnet_and_g4_disabled()` (monkeypatch `dotnet` absent), `test_build_scaffold_passes_with_dotnet()` (skipped without `dotnet`), `test_verify_parity_report_shape()` (FakeProvider, running the scaffold with code-generated bodies for `FundingStatusLookup`), `test_plan_connect_later()`, `test_readiness_rows()`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(api): design, build, verify, infra, plan, settings, history, readiness, evals`.

### Task 20: Web shell, theme, stepper, Upload and Discover tabs

**Files:**
- Create: `web/package.json`, `web/next.config.ts`, `web/tailwind.config.ts`, `web/app/layout.tsx`, `web/app/page.tsx`, `web/lib/{api.ts,sse.ts,theme.css,types.ts}`, `web/components/stepper/Stepper.tsx`, `web/components/evidence/EvidenceDrawer.tsx`, `web/components/tabs/upload/UploadTab.tsx`, `web/components/tabs/discover/{DiscoverTab,DomainSelect,DomainGraph,IOCard}.tsx`, `web/components/canvas/{ProcessCanvas,ActivityNode,Inspector}.tsx`, `web/public/rsi-logo.svg`
- Test: `web/e2e/upload-discover.spec.ts`

**Interfaces:**
- `lib/api.ts` typed client over the routes in Tasks 17 to 19; `lib/sse.ts: subscribeJob(jobId, onEvent)`.
- `Stepper` props `{steps: StepState[]; active: number; onSelect}` where `StepState = {key, label, status: "not_started"|"running"|"needs_review"|"approved"|"failed", evidence: EvidenceRef[]}`.
- Upload tab: drop zone (zip or folder), path field, file tree with counts, validation panel, Scan button, streaming progress, parse report table, red panel for unknowns, synthetic banner.
- Discover tab: `DomainSelect` (rename and merge), `DomainGraph` (Cytoscape, filters), `ProcessCanvas` (React Flow + ELK, colour strips by type, rule and finding badges, conditional link labels, dashed otherwise), `Inspector` tabs Config, SQL, Mappings, Rules, Findings, XML (scroll and highlight by `source_line`), `IOCard` with the "derived from the TIBCO model" label on sample outputs.

- [ ] **Step 1: Playwright test**: upload `samples/synthetic-gmf.zip`, scan, expect `116`-style counters replaced by the estate's numbers (`11` processes, `0` unknown), select domain `Dealer Funding`, open `DealerFundingRequest`, click the LIKE query step, expect the XML tab to highlight a line containing `like`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** with the theme tokens from Global Constraints; Poppins via `next/font`. **Step 4: Run** → PASS. **Step 5: Commit** `feat(web): shell, stepper, upload and discover tabs, canvas`.

### Task 21: Web: Validate and Design tabs, Ask panel

**Files:**
- Create: `web/components/tabs/validate/{ValidateTab,RuleCard,FindingCard,Banners,ChallengeBadge,AskPanel}.tsx`, `web/components/tabs/design/{DesignTab,MappingTable,SequenceDiagram,ConfigTable,DecisionList}.tsx`, `web/components/gates/GateButton.tsx`
- Test: `web/e2e/validate-design.spec.ts`

- [ ] **Step 1: Playwright test**: run analysis (API in `FakeProvider` mode via `PROVIDER=fake` env), banners green, approve every rule, approve G1 as user `sme.vicky`, decide findings (`fix` on the read-then-write, `keep` on LIKE with a note), approve G2, open Design, see the mapping row for the LIKE query naming `DealerFundingRepository.FindDealer` with finding badge, approve G3 as `architect.lee`; edit a rule and expect G1 to show `needs_review` and the audit line.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(web): validate and design tabs, gates, ask panel`.

### Task 22: Web: Build, Verify and Deploy tabs; Settings, History, Readiness

**Files:**
- Create: `web/components/tabs/build/{BuildTab,SourceTargetExplorer,CodeViewer,BuildLog,TestResults,MetricsRadar,ReviewPanel,InfraPanel}.tsx`, `web/components/tabs/verify/{VerifyTab,SwaggerFrame,ScenarioRunner,ParityTable,TestDataPanel}.tsx`, `web/components/tabs/deploy/{DeployTab,AzureConnectForm,PlanView}.tsx`, `web/app/settings/page.tsx`, `web/app/history/page.tsx`, `web/app/readiness/page.tsx`
- Test: `web/e2e/build-verify-deploy.spec.ts` (requires `dotnet`; skipped with reason otherwise)

- [ ] **Step 1: Playwright test**: build `FundingStatusLookup`, watch the log, expect compile `pass`, tests all green, radar visible with five axes, infra validation rows, approve G4; Verify: service starts, Swagger iframe loads `/swagger`, run scenarios, parity table shows `100%` with the model-derived label, approve G5; Deploy: connect-later state, plan view shows last validation, G6 disabled with reason; Settings shows provider presence and toolchains; Readiness lists the expectation rows with evidence links.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat(web): build, verify, deploy tabs; settings, history, readiness`.

---

## Milestone M8: acceptance and delivery

### Task 23: Acceptance run, runbook, delivery

**Files:**
- Create: `scripts/demo_check.py`, `docs/DEMO.md`, `README.md`, `docs/TOOLCHAIN.md` (from Task 1)
- Modify: `Makefile` (`demo-check`)

- [ ] **Step 1: Write `scripts/demo_check.py`** driving the API end to end per spec section 12 (upload, scan, discover, analyse two processes, gates G1 to G3 as named users, build with every test green, infra validated, verify parity 100% except `intended` fields, plan in connect-later mode) and printing a pass/fail table; exit code non-zero on any failure.
- [ ] **Step 2: Run** `make demo-check` → PASS; fix anything it finds.
- [ ] **Step 3: Write `docs/DEMO.md`** (Vicky's seven-step five-minute flow mapped to the tabs, then drill-downs; pre-flight list) and `README.md` (setup, env vars, make targets, what runs without keys).
- [ ] **Step 4: Full suite** `make test && make e2e` → PASS. **Step 5: Commit** `chore: acceptance script, runbook, readme`.
- [ ] **Step 6: Deliver**: copy the repository (without `node_modules`, `.venv`, `data/`) to the connected Mac folder `GM Financial- Modernizartion/exiqo-migrate/`; log to Hivemind.
