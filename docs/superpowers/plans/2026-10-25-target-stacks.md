# Target Stacks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the target stack a project choice (.NET, Java, Go, Python) picked at upload with a Settings default, with Java, Go and Python generators at the level of the .NET one (same IR inputs, `MappingRow` lineage, tests first, Dockerfile/infra, parity, Verify) and agents that follow the chosen stack.

**Architecture:** A `StackGenerator` registry in `engine/stacks/` wraps today's .NET generator unchanged and adds one package per new stack, each printing a shared language-neutral `ServicePlan` built from the IR and the stack-aware design record. A per-stack `StackRunner`, `SourceLanguage` and prompt pack replace the .NET calls in the build pipeline; Verify and infrastructure stay stack-neutral because every stack implements the .NET service's runtime contract. Value lands in order: abstraction, Java end to end, Go, Python.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, Alembic, Jinja2, lxml; generated: Java 21 + Spring Boot 3.5 + Maven 3.9, Go (≥ 1.25) + `net/http` + pgx v5, Python 3.12 + FastAPI + pytest + uv; Next.js 15, React 19, TanStack Query, Playwright; Docker, Helm 3 with helm-unittest.

**Spec:** `docs/superpowers/specs/2026-10-25-target-stacks-design.md`

## Global Constraints

- Branch: work and commit on `feat/gmf-deploy` only; never create a branch; never push. Stage only the files each task names (`git add <paths>`), never `git add -A`; never stash, reset or checkout other agents' work.
- No outbound hosts except configured GMF-controlled ones: no public registry, proxy or index URL in code, templates or bundle (Maven Central, `proxy.golang.org`, `sum.golang.org`, `pypi.org` never appear); mirrors only via `MAVEN_MIRROR_URL`, `GOPROXY`, `UV_INDEX_URL`/`PIP_INDEX_URL`; `make egress-audit` passes.
- Viewer read-only server-side: every new write route sits under the existing router guards (`require_signed_in` / `require_admin_writes`); `PATCH /projects/{id}` stack change needs `admin` or `architect`.
- Secrets only via `<NAME>_FILE` (`api/app/secrets.py`); this plan adds no secret.
- .NET output byte-identical: `engine/tests/golden/*` (top level) unchanged; `DotnetStack` wraps `generate_solution`, `DotnetRunner`, the existing agent checks and the top-level prompts.
- `design_hash` compatibility: `compute_design_hash` omits `stack` when it equals `"dotnet"`.
- Determinism: same inputs → byte-identical files and `build_hash` for every stack.
- Dependency tables fixed in `engine/stacks/<stack>/versions.py`; agents may not add dependencies; Go builds `GOFLAGS=-mod=readonly`, `GOSUMDB=off`, `GOTOOLCHAIN=local`, `GOTELEMETRY=off` with the committed `engine/stacks/go/go.sum.lock`; Python installs `--require-hashes` from `engine/stacks/python/requirements.lock`; Spring Boot ≥ 3.5.7 on the 3.5 line, Java 21, Maven ≥ 3.9.9, Go ≥ 1.25 (exact newest patch available on the mirror pinned once in `versions.py` and the Dockerfile build args).
- Chart: `stacks.enabled` default `[dotnet]`; schema requires `mirrors.maven` / `mirrors.goproxy` / `mirrors.pypi` for `java` / `go` / `python`.
- Alembic: each new revision file is named `<next free number>_<slug>.py` with `down_revision` = the single head printed by `.venv/bin/alembic -c api/alembic.ini heads` at execution time (sub-projects 2–4 add revisions concurrently).
- Toolchain tests skip with the reason when the tool is absent (pattern of `engine/tests/test_runner.py::needs_dotnet`), never fail.
- Existing suites green: `make test` (pytest), `.venv/bin/ruff check .` clean, `cd web && npm run typecheck && npm run lint` clean, `make e2e` green.
- No em dash in generated files (the existing `test_no_em_dash_and_no_unresolved_template` rule applies per stack).

## Review Focus

- A process whose condition or mapping the parser cannot translate (`parse_simple` → `None`): the Java/Go/Python method keeps the TIBCO comment and the stack's placeholder, the row stays `origin="agent"`, and the build still compiles before the builder runs. Test in Task 7 (Java) and repeated per stack in Tasks 12 and 14.
- A stored, approved .NET design record read after upgrade: its `design_hash` recomputes to the same value and G3 stays approved. Test in Task 3.
- A Go or Python agent body that passes the generic check but is vacuous (`assert True`, `if false { t.Fatal() }`): refused by the generic check's assertion rule or flagged by the test reviewer pack. Test in Task 8 (shared check) with per-stack cases in Tasks 13 and 15.
- A project whose stack is switched after G3 approval: Build answers 409 with the stack message and no build row is created; the next Design run resets G3–G6. Test in Task 6.
- An install with `java` enabled but no Maven mirror: `helm template` fails with the schema message, and in development an empty `MAVEN_MIRROR_URL` writes no `<mirror>` (tool default) rather than an empty URL. Test in Task 10.

## File Structure

| Path | Responsibility |
| --- | --- |
| `engine/stacks/__init__.py` (create) | registry: `get_stack`, `all_stacks`, `stack_status`, `StackKey` |
| `engine/stacks/base.py` (create) | `StackGenerator`, `StackRunner`, `SourceLanguage`, `StackNaming` protocols; `StackLayout`, `ProjectFolder`, `StackStatus`, `Mirrors` models |
| `engine/stacks/dotnet/{__init__,stack,naming,language}.py` (create) | wrapper over `engine/dotnet_gen` and the existing agent checks |
| `engine/stacks/contracts.py` (create) | neutral contract model split out of `csharp_contracts` |
| `engine/stacks/expr.py`, `engine/stacks/plan.py`, `engine/stacks/sql.py` (create) | expression AST, `ServicePlan`, placeholder styles |
| `engine/stacks/testindex.py`, `engine/stacks/results.py`, `engine/stacks/coverage.py` (create) | test index, JUnit/test2json parsing, JaCoCo/go-cover parsing |
| `engine/stacks/generic_checks.py` (create) | generic test-body check shared by Java/Go/Python |
| `engine/stacks/{java,go,python}/{__init__,stack,naming,versions,generator,contracts,tests_gen,runner,language}.py` + `templates/` (create) | one package per stack |
| `engine/stacks/go/go.sum.lock`, `engine/stacks/python/requirements.lock` (create) | pinned integrity files |
| `engine/agents/prompts/{java,go,python}/*.md` (create) | prompt packs |
| `engine/agents/{prompting,builder,test_engineer,reviewers,designer}.py`, `engine/dotnet_gen/{repair,design,models,patterns,csharp_contracts,generator}.py` (modify) | `stack` parameter, pack loading, naming via the stack |
| `engine/infra_gen/generator.py` (modify) | Dockerfile per stack, service-contract reading |
| `api/app/models/{project,build}.py`, `api/alembic/versions/<n>_target_stack.py` (modify/create) | `Project.target_stack`, `Build.stack` |
| `api/app/services/{build,verify,hosting,settings,design}.py`, `api/app/routers/{projects,settings,build}.py`, `api/app/schemas/project.py` (modify) | stack through the pipeline, selection API |
| `api/app/routers/stacks.py` (create) | `GET /stacks` |
| `scripts/toolchain.py`, `deploy/docker/platform.Dockerfile`, `deploy/helm/exiqo/**`, `Makefile`, `docs/deploy/GMF-INSTALL.md` (modify) | toolchains, mirrors, chart, locks target, guide |
| `web/components/tabs/upload/StackPicker.tsx`, `web/components/settings/StackPanel.tsx`, `web/components/ui/StackBadge.tsx`, `web/lib/stacks.ts` (create); `web/components/tabs/upload/UploadTab.tsx`, `web/components/shell/ProjectSelector.tsx`, `web/app/settings/page.tsx`, Design/Build/Verify tab headers, `web/lib/{api,types,buildTypes}.ts` (modify) | selection UI |
| `scripts/demo_check.py` (modify) | `--stack` |

---

### Task 1: Preflight, stack protocols, registry and the .NET wrapper

**Files:**
- Create: `engine/stacks/__init__.py`, `engine/stacks/base.py`, `engine/stacks/dotnet/__init__.py`, `engine/stacks/dotnet/stack.py`, `engine/stacks/dotnet/language.py`, `engine/tests/test_stacks_registry.py`
- Modify: `api/app/config.py` (`enabled_stacks: str = "dotnet,java,go,python"`)

**Interfaces:**
- Consumes (verify they exist, Step 1): `engine.dotnet_gen.generator.generate_solution`, `GeneratedSolution`, `GenFile`, `solution_layout`, `RESERVED_FOR_INFRA`, `MANIFEST`; `engine.dotnet_gen.runner.DotnetRunner` (`restore/build/format_write/format_check/test/run_service/env`), `BuildResult`, `TestRun`, `StepResult`, `ServiceHandle`; `engine.agents.builder.{PLACEHOLDER, strip_comments, code_only, declarations, tibco_comments, is_test_path}`; `engine.agents.test_engineer.check_test_body`; `scripts/toolchain.detect`; `engine.memory.model` new-code uid helpers with a `stack` argument (sub-project 2).
- Produces in `base.py`: `StackKey = Literal["dotnet", "java", "go", "python"]`; `STACK_KEYS: tuple[StackKey, ...]`; `class Mirrors(BaseModel)`: `nuget: str = ""`, `maven: str = ""`, `goproxy: str = ""`, `pypi: str = ""`; `class ProjectFolder(BaseModel)`: `name: str`, `folder: str`, `build_file: str | None`; `class StackLayout(BaseModel)`: `solution_file: str | None`, `projects: dict[str, ProjectFolder]`, `api_entry: str`, `test_root: str`, `dockerfile_context: dict[str, Any]`, `contract_file: str | None`; `class StackStatus(BaseModel)`: `key`, `label`, `available: bool`, `reason: str | None`, `required_tools: list[str]`; Protocols `StackRunner`, `SourceLanguage` (members of spec section 3), `StackNaming` (section 2), `StackGenerator` (section 1 table).
- Produces in `__init__.py`: `get_stack(key: str) -> StackGenerator` (raises `KeyError(f"unknown stack {key!r}")`), `all_stacks() -> list[StackGenerator]` (implemented only; Tasks 7/12/14 register theirs), `stack_status(key: str, toolchains: dict[str, Any], enabled: Iterable[str]) -> StackStatus` with reasons exactly `"not implemented yet"`, `"not enabled on this install (stacks.enabled)"`, `"toolchain missing: <tool>"`.
- Produces `DotnetStack` (`key="dotnet"`, `label="C# · .NET 10 · ASP.NET Core"`, `required_tools=("dotnet",)`, `source_suffixes=(".cs",)`, `prompt_pack=""`, `download_excluded=frozenset({"bin","obj","TestResults",".exiqo"})`, `service_process_marker="dotnet"`); `generate(...)` calls `generate_solution(app, process_ids, designs, rules, findings, scenarios, out_dir, mirrors.nuget)`; `layout()` derives from `solution_layout`; `runner()` returns `DotnetRunner`.

- [ ] **Step 1: Verify the consumed interfaces exist**

Run: `.venv/bin/python -c "from engine.dotnet_gen.generator import generate_solution, GeneratedSolution, GenFile, solution_layout, RESERVED_FOR_INFRA, MANIFEST; from engine.dotnet_gen.runner import DotnetRunner, BuildResult, TestRun, StepResult, ServiceHandle; from engine.agents.builder import PLACEHOLDER, strip_comments, code_only, declarations, tibco_comments, is_test_path; from engine.agents.test_engineer import check_test_body; import engine.memory.model as m; print('ok')"`
Expected: `ok`. If `engine.memory.model` lacks the new-code uid helpers, note it in the task report; Task 9's memory assertion then checks the uid string pattern `codefile:<stack>:` directly.

- [ ] **Step 2: Write the failing tests** in `engine/tests/test_stacks_registry.py`

```python
def test_registry_has_dotnet_and_rejects_unknown():
    assert get_stack("dotnet").key == "dotnet"
    with pytest.raises(KeyError, match="unknown stack 'cobol'"):
        get_stack("cobol")

def test_status_reasons_in_order():
    tools = {"dotnet": {"present": True}, "java": {"present": False}, "mvn": {"present": False}}
    assert stack_status("dotnet", tools, ["dotnet"]).available is True
    assert stack_status("java", tools, ["dotnet"]).reason in ("not implemented yet", "not enabled on this install (stacks.enabled)")
    assert stack_status("dotnet", {"dotnet": {"present": False}}, ["dotnet"]).reason == "toolchain missing: dotnet"

def test_dotnet_wrapper_is_byte_identical(tmp_path, gmf_app, gmf_inputs):
    a = generate_solution(gmf_app, *gmf_inputs, tmp_path / "a")
    b = get_stack("dotnet").generate(gmf_app, *gmf_inputs, tmp_path / "b", Mirrors())
    assert a.build_hash == b.build_hash and [f.path for f in a.files] == [f.path for f in b.files]

def test_dotnet_language_delegates_to_builder_checks():
    lang = get_stack("dotnet").language
    assert lang.placeholder.search('throw new NotImplementedException("agent fills: X");')
    assert lang.is_test_path("tests/GmfCredit.Tests/A.cs") and not lang.is_test_path("src/A.cs")
```

`gmf_app`/`gmf_inputs` are module fixtures moved from `engine/tests/test_generator.py` into `engine/tests/conftest.py` (same bodies; `test_generator.py` imports them unchanged by name).

- [ ] **Step 3: Run** `.venv/bin/pytest engine/tests/test_stacks_registry.py -v` — Expected: FAIL (`ModuleNotFoundError: engine.stacks`).
- [ ] **Step 4: Implement** `base.py`, the registry, `DotnetStack`, `DotnetLanguage`, the setting, and the fixture move.
- [ ] **Step 5: Run** the file — Expected: 4 passed; `.venv/bin/pytest engine/tests/test_generator.py -q` — Expected: unchanged pass/skip counts.
- [ ] **Step 6: Commit** `git add engine/stacks engine/tests/test_stacks_registry.py engine/tests/conftest.py engine/tests/test_generator.py api/app/config.py && git commit -m "feat(stacks): stack protocols, registry and .NET wrapper"`

---

### Task 2: `Project.target_stack`, `Build.stack`, Settings default and the selection API

**Files:**
- Create: `api/alembic/versions/<n>_target_stack.py`, `api/app/routers/stacks.py`, `api/tests/test_stacks_api.py`
- Modify: `api/app/models/project.py`, `api/app/models/build.py`, `api/app/schemas/project.py`, `api/app/routers/projects.py`, `api/app/routers/settings.py`, `api/app/services/settings.py`, `api/app/main.py`, `scripts/toolchain.py`

**Interfaces:**
- Consumes: `stack_status`, `all_stacks`, `STACK_KEYS` (Task 1); `require_actor`, `WRITE_ROLES`, `Principal.roles` (`api/app/deps.py`).
- Produces: `Project.target_stack: Mapped[str] = mapped_column(server_default="dotnet")`; `Build.stack: Mapped[str] = mapped_column(server_default="dotnet")`; migration adds both `NOT NULL DEFAULT 'dotnet'`.
- `ProjectCreate.target_stack: StackKey | None = None`; `ProjectOut.target_stack: str`; `ProjectPatch(BaseModel)`: `target_stack: StackKey`.
- `PATCH /projects/{project_id}` → `ProjectOut`; 403 `"changing the target stack needs the architect or admin role"` for other roles (oidc and dev personas); 422 `{"detail": "<key> is not available: <reason>"}`; audit line `project stack changed` with `{old, new}`.
- `svc.DEFAULT_STACK_KEY = "default_stack"`; `SettingsPatch.default_stack: StackKey | None`; `settings_view(...)["stacks"] = [StackStatus.model_dump() | {"default": bool}]` and `["default_stack"]`.
- `GET /stacks` → `{"default": str, "stacks": [StackStatus + default]}` (any signed-in role).
- `scripts/toolchain.TOOLS` adds `"java": ["-version"]`, `"mvn": ["-v"]`, `"go": ["version"]`, `"python3": ["--version"]`, `"uv": ["--version"]`; `_local_dirs()` adds `.tools/java/bin`, `.tools/maven/bin`, `.tools/go/bin`.

- [ ] **Step 1: Write failing tests** in `api/tests/test_stacks_api.py`

```python
async def test_create_project_defaults_to_settings_default(client):
    await client.put("/settings", json={"default_stack": "dotnet"})
    p = (await client.post("/projects", json={"name": "a"})).json()
    assert p["target_stack"] == "dotnet"

async def test_unavailable_stack_is_refused_with_reason(client, monkeypatch):
    monkeypatch.setattr(stacks_mod, "_toolchains", lambda: {"dotnet": {"present": True}})
    r = await client.post("/projects", json={"name": "a", "target_stack": "java"})
    assert r.status_code == 422 and r.json()["detail"].startswith("java is not available: ")

async def test_patch_stack_needs_architect_or_admin(client, project):
    r = await client.patch(f"/projects/{project}", json={"target_stack": "dotnet"}, headers={"X-User": "sme.vicky"})
    assert r.status_code == 403 and "architect or admin" in r.json()["detail"]

async def test_viewer_cannot_patch_in_oidc(oidc_client, project, viewer_token):
    r = await oidc_client.patch(f"/projects/{project}", json={"target_stack": "dotnet"}, headers=viewer_token)
    assert r.status_code == 403 and r.json()["detail"] == "viewer is read only"

async def test_stacks_endpoint_lists_four_with_status(client):
    body = (await client.get("/stacks")).json()
    assert [s["key"] for s in body["stacks"]] == ["dotnet", "java", "go", "python"]
    assert all({"available", "reason", "label", "required_tools", "default"} <= s.keys() for s in body["stacks"])

async def test_existing_projects_and_builds_default_to_dotnet(session_after_migration):
    assert await session_after_migration.scalar(text("select target_stack from projects limit 1")) == "dotnet"
```

`oidc_client`/`viewer_token` reuse the helpers in `api/tests/test_auth.py`; `session_after_migration` runs `alembic upgrade head` on a database holding one project inserted at the previous head.

- [ ] **Step 2: Run** `.venv/bin/pytest api/tests/test_stacks_api.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the columns, migration, schemas, routes, settings key and toolchain entries. `GET /stacks` lists all four keys (implemented or not) so the picker can show "not implemented yet".
- [ ] **Step 4: Run** the file — Expected: 6 passed; `.venv/bin/pytest api/tests -q` — Expected: no regressions.
- [ ] **Step 5: Commit** `git add api/alembic/versions/<n>_target_stack.py api/app/models/project.py api/app/models/build.py api/app/schemas/project.py api/app/routers/projects.py api/app/routers/settings.py api/app/routers/stacks.py api/app/services/settings.py api/app/main.py scripts/toolchain.py api/tests/test_stacks_api.py && git commit -m "feat(stacks): project target stack, build stack, settings default and /stacks"`

---

### Task 3: Stack-aware design skeleton, naming tables and the neutral contract model

**Files:**
- Create: `engine/stacks/dotnet/naming.py`, `engine/stacks/contracts.py`, `engine/tests/test_stacks_design.py`
- Modify: `engine/dotnet_gen/models.py`, `engine/dotnet_gen/design.py`, `engine/dotnet_gen/patterns.py`, `engine/dotnet_gen/csharp_contracts.py`, `api/app/services/design.py`

**Interfaces:**
- Consumes: `get_stack` (Task 1), `Project.target_stack` (Task 2).
- Produces: `DesignRecord.stack: str = "dotnet"`; `compute_design_hash` drops `stack` from the payload when `== "dotnet"`.
- `StackNaming` members: `type_name(text: str) -> str`, `method_name(prefix: str, text: str) -> str`, `pattern_paths(pattern: str) -> tuple[str, str, str]` (file, class, method templates over `patterns.TEMPLATE_KEYS`), `layer_folder(kind: str, app: str) -> str`, `contract_target(app: str, module: str, stem: str) -> tuple[str, str]` (namespace, file), `config_key(module: str, name: str) -> str` (`Gmf:<Module>:<Name>` for every stack).
- `DotnetNaming.pattern_paths` returns exactly today's `PatternSpec.file_template/class_template/method_template`; `PatternSpec` keeps those fields (read by `DotnetNaming` only).
- `engine/stacks/contracts.py`: `ContractModel` (records with `name`, `props: list[ContractProp(name, json_name, type_ref, required, repeated, enum_values, tibco_path)]`, enums), `contract_model(contract: Contract) -> ContractModel`, `plan_contracts(app_name: str, contracts: list[Contract], naming: StackNaming) -> dict[str, ContractPlan]`. `csharp_contracts` renders from `ContractModel`; its output is byte-identical.
- `build_design_skeleton(process, app, rules, findings, io_card, spec_hash=None, stack="dotnet")`; `run_design` passes `project.target_stack`.

- [ ] **Step 1: Write failing tests** in `engine/tests/test_stacks_design.py`

```python
def test_stored_dotnet_record_hash_is_unchanged():
    stored = json.loads((GOLDEN / "design_record_v1.json").read_text())   # captured from main before this task
    rec = DesignRecord.model_validate(stored)
    assert rec.stack == "dotnet" and compute_design_hash(rec) == stored["design_hash"]

def test_stack_moves_the_hash_for_non_dotnet(designs):
    d = designs[DFR]
    assert compute_design_hash(d.model_copy(update={"stack": "java"})) != compute_design_hash(d)

def test_dotnet_naming_reproduces_every_pattern():
    for name, spec in PATTERNS.items():
        assert get_stack("dotnet").naming.pattern_paths(name) == (spec.file_template, spec.class_template, spec.method_template)

def test_dotnet_skeleton_is_unchanged(gmf_app, gmf_processes, rules, findings, cards):
    before = json.loads((GOLDEN / "design_skeleton_dfr.json").read_text())
    after = build_design_skeleton(gmf_processes[DFR], gmf_app, rules[DFR], findings[DFR], cards[DFR]).model_dump(mode="json", by_alias=True)
    assert after == before

def test_csharp_contracts_byte_identical_through_neutral_model(gmf_app):
    assert render_all_records(gmf_app) == (GOLDEN / "contracts_gmf_credit.txt").read_text()
```

Step 1a captures `design_record_v1.json`, `design_skeleton_dfr.json` and `contracts_gmf_credit.txt` into `engine/tests/golden/` by running the current code before any change (`UPDATE_GOLDEN=1`), and commits them with this task.

- [ ] **Step 2: Run** `.venv/bin/pytest engine/tests/test_stacks_design.py -v` — Expected: FAIL (`DesignRecord` has no `stack`).
- [ ] **Step 3: Implement** the field, the hash rule, `DotnetNaming`, the neutral contract model with the C# renderer on top, the `stack` argument, and the design service wiring.
- [ ] **Step 4: Run** the file and `.venv/bin/pytest engine/tests/test_design.py engine/tests/test_generator.py -q` — Expected: PASS, golden unchanged.
- [ ] **Step 5: Commit** `git add engine/stacks/dotnet/naming.py engine/stacks/contracts.py engine/dotnet_gen/models.py engine/dotnet_gen/design.py engine/dotnet_gen/patterns.py engine/dotnet_gen/csharp_contracts.py api/app/services/design.py engine/tests/test_stacks_design.py engine/tests/golden/design_record_v1.json engine/tests/golden/design_skeleton_dfr.json engine/tests/golden/contracts_gmf_credit.txt && git commit -m "feat(stacks): stack-aware design skeleton and neutral contract model"`

---

### Task 4: `ServicePlan`, expression AST and placeholder styles

**Files:**
- Create: `engine/stacks/expr.py`, `engine/stacks/plan.py`, `engine/stacks/sql.py`, `engine/tests/test_stacks_plan.py`

**Interfaces:**
- Consumes: IR models (`engine.ir.models`), `DesignRecord`, `MappingRow`, `engine.dotnet_gen.design._walk_order`, `is_simple_expression`, `engine.dotnet_gen.xpath` function table, `ContractModel` (Task 3).
- Produces in `expr.py`: dataclasses `Path(root: str, steps: tuple[tuple[str, int | None], ...], attribute: bool)`, `Lit(value: str | int | Decimal | bool)`, `Compare(op: Literal["==","!=","<","<=",">",">="], left, right)`, `And(items)`, `Or(items)`, `Not(item)`, `Count(path)`, `Call(name: str, args)`; `Expr = Path | Lit | Compare | And | Or | Not | Count | Call`; `parse_simple(expr: str) -> Expr | None`.
- Produces in `plan.py`: `Assign(target: str, source: Expr | None, tibco: str)`; `PlanStep(activity_id, name, pattern, row: MappingRow, inputs: list[Assign], sql: str | None, call_target: str | None, throws: str | None, agent: bool)`; flow nodes `Seq(items)`, `Run(step_id)`, `If(cond: Expr | None, tibco: str, then, otherwise)`, `Queue(links)`, `Try(body, catch: dict[str, FlowNode], catch_all: FlowNode | None)`, `Loop(group_id, over: Expr | None, body)`; `Starter(kind: Literal["rest","http","soap","jms","timer","subprocess"], route: str | None, verb: str | None, destination: str | None, selector: str | None, interval_ms: int | None)`; `ServicePlan(process_id, process_name, starter: Starter, steps: list[PlanStep], flow, contracts: ContractModel, config: list[ConfigMap], messaging: list[MessagingEntity])`; `build_plan(process: Process, app: Application, design: DesignRecord) -> ServicePlan`.
- Produces in `sql.py`: `convert_placeholders(sql: str, style: Literal["named_at","jdbc","dollar","pyformat"]) -> str`; `named_at` delegates to `generator.to_named_parameters`.

- [ ] **Step 1: Write failing tests** in `engine/tests/test_stacks_plan.py`

```python
def test_plan_step_order_matches_walk_order(gmf_app, gmf_processes, designs):
    plan = build_plan(gmf_processes[DFR], gmf_app, designs[DFR])
    assert [s.activity_id for s in plan.steps] == [a for a, _ in _walk_order(gmf_processes[DFR]) if a in {x.id for x in gmf_processes[DFR].activities}]

def test_agent_flags_match_dotnet_origins(gmf_app, gmf_processes, designs, solution):
    plan = build_plan(gmf_processes[DFR], gmf_app, designs[DFR])
    dotnet_agent = {f.activity_id for f in solution.files if f.origin == "agent" and f.activity_id}
    assert {s.activity_id for s in plan.steps if s.agent} == dotnet_agent & {s.activity_id for s in plan.steps}

def test_parse_simple_comparisons_and_refusals():
    assert parse_simple('$get/root/status = "ACTIVE"') == Compare("==", Path("get", (("root", None), ("status", None)), False), Lit("ACTIVE"))
    assert parse_simple("count($x/item) > 0") == Compare(">", Count(Path("x", (("item", None),), False)), Lit(0))
    assert parse_simple("tib:parse-date('yyyy', $d)") is None

@pytest.mark.parametrize("style,expected", [("jdbc", "select ? , '?' -- ?\n"), ("dollar", "select $1 , '?' -- ?\n"), ("pyformat", "select %s , '?' -- ?\n")])
def test_placeholder_styles_skip_literals_and_comments(style, expected):
    assert convert_placeholders("select ? , '?' -- ?\n", style) == expected

def test_pyformat_escapes_literal_percent():
    assert convert_placeholders("select '%' || ?", "pyformat") == "select '%%' || %s"
```

- [ ] **Step 2: Run** `.venv/bin/pytest engine/tests/test_stacks_plan.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the three modules. `build_plan` derives flow from transitions the same way `generator._Structured`/`_queue_body` does (structured when the link graph is series-parallel, `Queue` otherwise) and flags `agent=True` wherever `translate_condition` or `is_simple_expression` would leave a body to the agent.
- [ ] **Step 4: Run** the file — Expected: PASS.
- [ ] **Step 5: Commit** `git add engine/stacks/expr.py engine/stacks/plan.py engine/stacks/sql.py engine/tests/test_stacks_plan.py && git commit -m "feat(stacks): language-neutral service plan, expression AST, placeholder styles"`

---

### Task 5: Test index, result and coverage parsers, stack-neutral metrics

**Files:**
- Create: `engine/stacks/testindex.py`, `engine/stacks/results.py`, `engine/stacks/coverage.py`, `engine/tests/samples/stacks/{surefire-TEST-sample.xml,go-test2json.jsonl,pytest-junit.xml,jacoco.xml,go-cover.out,coverage.xml,test-index.json}`, `engine/tests/test_stacks_results.py`
- Modify: `engine/dotnet_gen/metrics.py` (`compute_metrics(..., source_files: int | None = None)` uses it when given), `api/app/services/build.py` (`test_inventory(files, app_name, stack="dotnet")`)

**Interfaces:**
- Produces in `testindex.py`: `IndexEntry(BaseModel)`: `name`, `kind: Literal["rule","branch","scenario","record","other"]`, `rule_id`, `branch_id`, `scenario_id`, `display_name`, `process_id`, `path`, `has_body: bool`; `INDEX_PATH = ".exiqo/test-index.json"`; `write_index(entries) -> GenFile`; `read_index(root: Path) -> list[IndexEntry]`; `join(cases: list[tuple[str, str, float, str]], index) -> list[TestCase]` (name, outcome, duration_ms, message → `trx.TestCase` with ids from the index; unknown names keep `None` ids).
- Produces in `results.py`: `parse_junit(paths: list[Path]) -> list[tuple[str, Outcome, float, str]]` (Surefire and pytest JUnit XML: `classname + "." + name`; `failure`/`error` → failed, `skipped` → skipped; XML parsed with `resolve_entities=False, no_network=True`); `parse_test2json(path: Path) -> list[...]` (`Action` `pass|fail|skip` per `Package + "." + Test`, subtests ignored).
- Produces in `coverage.py`: `parse_jacoco(path, scope: set[str] | None) -> float | None` (LINE counters), `parse_go_cover(path, scope) -> float | None` (statement-weighted), Cobertura via existing `engine.dotnet_gen.coverage.merge_cobertura`.
- `test_inventory(..., stack)` returns the same dict shape from `read_index` for non-.NET.

- [ ] **Step 1: Write failing tests** in `engine/tests/test_stacks_results.py`

```python
def test_junit_surefire_and_pytest_samples():
    s = parse_junit([SAMPLES / "surefire-TEST-sample.xml"])
    assert ("com.gmf.gmfcredit.DealerFundingRequestRulesTests.r1_dealerMustBeActive", "passed") in [(n, o) for n, o, _, _ in s]
    p = parse_junit([SAMPLES / "pytest-junit.xml"])
    assert {o for _, o, _, _ in p} == {"passed", "failed", "skipped"}

def test_test2json_ignores_subtests_and_output_lines():
    cases = parse_test2json(SAMPLES / "go-test2json.jsonl")
    assert len(cases) == 3 and all("/" not in n for n, *_ in cases)

def test_join_maps_ids_from_the_index():
    idx = read_index_file(SAMPLES / "test-index.json")
    out = join([("tests.TestFundingStatusLookupBranch_t3", "passed", 1.0, "")], idx)
    assert out[0].branch_id == "t3" and out[0].display_name.startswith("Branch [t3]")

def test_coverage_parsers():
    assert parse_jacoco(SAMPLES / "jacoco.xml", None) == pytest.approx(0.75)
    assert parse_go_cover(SAMPLES / "go-cover.out", {"internal/core/funding_status_lookup_service.go"}) == pytest.approx(2 / 3)

def test_metrics_use_source_file_count_when_given(metrics_inputs):
    m = compute_metrics(*metrics_inputs, source_files=10)
    assert m.details["standards"]["files"] == 10
```

Sample values are chosen so the assertions hold (written with the samples in this task).

- [ ] **Step 2: Run** `.venv/bin/pytest engine/tests/test_stacks_results.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the modules, the samples, the `source_files` parameter and the inventory branch.
- [ ] **Step 4: Run** the file and `.venv/bin/pytest engine/tests/test_generator.py::test_metrics_formulas -v` — Expected: PASS.
- [ ] **Step 5: Commit** `git add engine/stacks/testindex.py engine/stacks/results.py engine/stacks/coverage.py engine/tests/samples/stacks engine/tests/test_stacks_results.py engine/dotnet_gen/metrics.py api/app/services/build.py && git commit -m "feat(stacks): test index, JUnit/test2json/JaCoCo/go-cover parsing"`

---

### Task 6: Build pipeline, agents and prompt packs go through the stack

**Files:**
- Create: `engine/stacks/generic_checks.py`, `engine/tests/test_stacks_agents.py`, `api/tests/test_build_stack.py`
- Modify: `engine/agents/prompting.py`, `engine/agents/builder.py`, `engine/agents/test_engineer.py`, `engine/agents/reviewers.py`, `engine/agents/designer.py`, `engine/dotnet_gen/repair.py`, `api/app/services/build.py`, `api/app/services/settings.py` (`detect_toolchains` via `scripts/toolchain`), `api/app/routers/build.py` (export note "Target stack", `download_excluded` per stack)

**Interfaces:**
- Consumes: `get_stack`, `SourceLanguage`, `StackRunner` (Task 1), `test_inventory(..., stack)` (Task 5), `Build.stack`, `Project.target_stack` (Task 2), `DesignRecord.stack` (Task 3).
- Produces: `load_prompt(name: str, stack: str = "dotnet") -> tuple[str, str]` and `render_prompt(name, stack="dotnet", **values)`; `STACK_PROMPTS = ("builder","repair","test_engineer","designer","review_apply","reviewer_equivalence","reviewer_security","reviewer_standards","reviewer_test")`.
- `fill_method_bodies(..., stack: str = "dotnet")`, `fill_test_bodies(..., stack="dotnet")`, `review_panel(..., stack="dotnet")`, `design_narrative(..., stack="dotnet")`, `repair_loop(..., stack="dotnet")`, `compile_repair_loop(..., stack="dotnet")`, `apply_review_findings(..., stack="dotnet")`: every text check goes through `get_stack(stack).language`.
- `generic_checks.check_generic_test_body(body: str, stack: str) -> str | None` (refusal reason or `None`; rules of spec section 3, limit 120 lines).
- `run_build`: `stack = design.stack`; when `stack != project.target_stack` → `BuildRefused("the approved design is for <a>, the project now targets <b>: re-run Design")`, surfaced by `POST /processes/{id}/build` as 409 before a `Build` row exists; `build.stack = stack`; generation via `get_stack(stack).generate(..., Mirrors(nuget=settings.nuget_source, maven=settings.maven_mirror_url, goproxy=settings.goproxy, pypi=settings.uv_index_url))`; runner via `.runner(...)`; unavailable stack → the build step logs `stack_status(...).reason` instead of `NO_DOTNET`.
- Settings fields (config.py): `maven_mirror_url: str = ""`, `goproxy: str = ""`, `uv_index_url: str = ""`.

- [ ] **Step 1: Write failing tests** in `engine/tests/test_stacks_agents.py` and `api/tests/test_build_stack.py`

```python
def test_load_prompt_falls_back_to_top_level_for_dotnet():
    assert load_prompt("builder", "dotnet") == load_prompt("builder")

def test_load_prompt_uses_the_stack_pack(tmp_prompts):        # tmp pack with prompts/java/builder.md
    system, _ = load_prompt("builder", "java")
    assert "Java" in system and "C#" not in system

@pytest.mark.parametrize("stack,body,reason", [
    ("python", "assert True", "no assertion on a value under test"),
    ("go", 'if false { t.Fatal("x") }', "no assertion on a value under test"),
    ("java", "Thread.sleep(10); assertThat(x).isEqualTo(1);", "sleeps are not allowed"),
    ("python", "pytest.fail('no test body for R1')", "placeholder still present")])
def test_generic_check_refusals(stack, body, reason):
    assert reason in check_generic_test_body(body, stack)

async def test_builder_uses_stack_language(fake_provider, java_like_solution):
    await fill_method_bodies(java_like_solution, ..., provider=fake_provider, stack="java")
    assert "UnsupportedOperationException" in fake_provider.last_user_prompt
```

```python
async def test_build_refused_when_design_stack_differs(client, approved_g3_dotnet):   # Review Focus 4
    pid, proc = approved_g3_dotnet
    await client.patch(f"/projects/{pid}", json={"target_stack": "java"}, headers=ARCHITECT)
    r = await client.post(f"/processes/{proc}/build", json={})
    assert r.status_code == 409 and r.json()["detail"] == "the approved design is for dotnet, the project now targets java: re-run Design"
    assert (await client.get(f"/processes/{proc}/builds")).json() == []

async def test_dotnet_build_records_stack(client, approved_g3_dotnet, wait_build):
    build = await wait_build(approved_g3_dotnet)
    assert build["stack"] == "dotnet"
```

- [ ] **Step 2: Run** `.venv/bin/pytest engine/tests/test_stacks_agents.py api/tests/test_build_stack.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the pack loader, the `stack` parameters, the generic check, the pipeline wiring, the settings fields and the router changes. The .NET path calls exactly what it called before.
- [ ] **Step 4: Run** both files, then `.venv/bin/pytest engine/tests/test_build_agents.py engine/tests/test_agents.py api/tests/test_build_verify.py -q` — Expected: PASS with unchanged counts.
- [ ] **Step 5: Commit** `git add engine/stacks/generic_checks.py engine/agents/prompting.py engine/agents/builder.py engine/agents/test_engineer.py engine/agents/reviewers.py engine/agents/designer.py engine/dotnet_gen/repair.py api/app/services/build.py api/app/services/settings.py api/app/routers/build.py api/app/config.py engine/tests/test_stacks_agents.py api/tests/test_build_stack.py && git commit -m "feat(stacks): build pipeline, agents and prompt packs follow the stack"`

---

### Task 7: Java generator: project, contracts, services, controllers, repositories, consumers, jobs

**Files:**
- Create: `engine/stacks/java/{__init__,stack,naming,versions,generator,contracts}.py`, `engine/stacks/java/templates/*.j2` (`pom.xml`, `maven-settings.xml`, `Application.java`, `SecurityConfig.java`, `DevTokenController.java`, `EnvBinding.java`, `META-INF/spring.factories`, `application.yml`, `InMemoryMessageBus.java`, `ServiceBusMessageBus.java`, `MessageBus.java`, `BusDevController.java`, `TibValue.java`, `TibText.java`, `Masking.java`, `ProcessFault.java`, `FaultHandler.java`, `TibcoPath.java`, `gitignore`, `editorconfig`), `engine/tests/test_java_generator.py`, `engine/tests/golden/java/{DealerFundingRequestRepository.java,gmf_credit_files.txt}`

**Interfaces:**
- Consumes: `ServicePlan`, `build_plan`, `Expr`, `convert_placeholders(style="jdbc")` (Task 4), `ContractModel`/`plan_contracts` (Task 3), `GenFile`, `GeneratedSolution`, `write_solution`, `MANIFEST`, `RESERVED_FOR_INFRA` (existing), `write_index` (Task 5 — the index entries for tests are added in Task 8; this task writes an empty index).
- Produces `JavaStack` registered in `engine/stacks/__init__.py` (`key="java"`, `label="Java 21 · Spring Boot 3.5 · Maven"`, `required_tools=("java","mvn")`, `source_suffixes=(".java",)`, `prompt_pack="java"`, `download_excluded=frozenset({"target",".exiqo"})`, `service_process_marker="java"`).
- `JavaNaming`: package `com.gmf.<app lower alnum>`; `type_name` PascalCase; `method_name` lowerCamel (`LookupDealer` → `lookupDealer`); `pattern_paths` maps every `PATTERNS` key (service `core/{process}Service.java`, controller `api/{process}Controller.java`, repository `infrastructure/{process}Repository.java`, mappings `core/{process}Mappings.java`, bus `infrastructure/messaging/MessageBus.java`, serialization `core/{process}Serialization.java`, files `infrastructure/{process}FileStore.java`, exceptions `core/exceptions/{exception}.java`); `contract_target` → (`com.gmf.<app>.core.contracts.<module lower>`, `core/contracts/<module lower>/<Stem>.java`).
- `generate_java(app, process_ids, designs, rules, findings, scenarios, out_dir, mirrors) -> GeneratedSolution`: placeholder body `throw new UnsupportedOperationException("agent fills: <activity>");` under `// TIBCO <expr>` lines; SQL verbatim in a text block; `.exiqo/service-contract.json` per spec section 5; `.exiqo/maven-settings.xml` with `<mirror><id>gmf</id><mirrorOf>*</mirrorOf><url>{{maven}}</url></mirror>` only when `mirrors.maven` is non-empty.
- `versions.py`: `JAVA_RELEASE = 21`, `SPRING_BOOT`, `AZURE_SDK_BOM`, `POSTGRESQL`, `JACOCO`, `SPOTLESS`, `PALANTIR_JAVA_FORMAT` (exact versions pinned at implementation, Spring Boot ≥ 3.5.7).

- [ ] **Step 1: Write failing tests** in `engine/tests/test_java_generator.py`

```python
def test_java_golden(java_solution):
    _golden("java/DealerFundingRequestRepository.java", _file(java_solution, "DealerFundingRequestRepository.java").content)
    _golden("java/gmf_credit_files.txt", "\n".join(f.path for f in java_solution.files) + "\n")

def test_java_deterministic(tmp_path, java_inputs, java_solution):
    assert generate_java(*java_inputs, tmp_path / "again", Mirrors()).build_hash == java_solution.build_hash

def test_every_mapping_row_exists_in_the_tree(java_solution, java_designs):          # spec criterion 3
    for d in java_designs.values():
        for r in d.mapping:
            f = _file(java_solution, f"{r.file}")
            assert f"class {r.class_name}" in f.content or f"record {r.class_name}" in f.content or f"interface {r.class_name}" in f.content
            assert f" {r.method}(" in f.content

def test_untranslatable_step_keeps_tibco_and_placeholder(java_solution):            # Review Focus 1
    agent = [f for f in java_solution.files if f.origin == "agent"]
    assert agent and all('UnsupportedOperationException("agent fills:' in f.content and "// TIBCO" in f.content for f in agent)

def test_sql_verbatim_with_jdbc_placeholders(java_solution, gmf_processes):
    repo = _file(java_solution, "DealerFundingRequestRepository.java").content
    assert "where f.deal_id = ?" in repo and "@p0" not in repo

def test_runtime_contract_endpoints_and_env_binding(java_solution):
    text = "\n".join(f.content for f in java_solution.files if f.path.endswith(".java"))
    for route in ('"/health"', '"/auth/dev-token"', '"/_bus/publish"', '"/_bus/messages"', '"/_bus/dead-letters"'):
        assert route in text
    assert "class EnvBinding implements EnvironmentPostProcessor" in text

def test_no_public_repository_or_em_dash(java_solution):
    blob = "\n".join(f.content for f in java_solution.files)
    assert "repo.maven.apache.org" not in blob and "repo1.maven.org" not in blob and "—" not in blob and "{{" not in blob

def test_settings_without_mirror_has_no_mirror_block(java_solution):                # Review Focus 5 (dev half)
    assert "<mirror>" not in _file(java_solution, ".exiqo/maven-settings.xml").content
```

- [ ] **Step 2: Run** `.venv/bin/pytest engine/tests/test_java_generator.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `JavaNaming`, the contract renderer (records, enums, `List<T>`, nullable via boxed types, `@JsonProperty`, `@TibcoPath`), the generator printing `ServicePlan` (expression printer over `TibValue`, flow printer for `Seq/If/Queue/Try/Loop`), the templates, registration; run once with `UPDATE_GOLDEN=1` to write the golden files and review them by eye.
- [ ] **Step 4: Run** the file — Expected: PASS.
- [ ] **Step 5: Commit** `git add engine/stacks/java engine/stacks/__init__.py engine/tests/test_java_generator.py engine/tests/golden/java && git commit -m "feat(stacks): Java Spring Boot generator"`

---

### Task 8: Java tests generated first, `MavenRunner`, `JavaLanguage`, Java prompt pack

**Files:**
- Create: `engine/stacks/java/{tests_gen,runner,language}.py`, `engine/stacks/java/templates/{TestDatabase.java,ScenarioSupport.java,Fixtures.java}.j2`, `engine/agents/prompts/java/{builder,repair,test_engineer,designer,review_apply,reviewer_equivalence,reviewer_security,reviewer_standards,reviewer_test}.md`, `engine/tests/test_java_toolchain.py`, `engine/tests/test_prompt_packs.py`, `engine/tests/golden/java/DealerFundingRequestRulesTests.java`

**Interfaces:**
- Consumes: `tests_gen.fixture_json`, `fixture_path`, `branch_transitions`, `scenario_stem` (stack-neutral helpers in `engine/dotnet_gen/tests_gen.py`), `write_index`, `join`, `parse_junit`, `parse_jacoco` (Task 5), `child_env` (`engine/child_env.py`), `generic_checks` (Task 6).
- Produces: `generate_java_tests(plan, rules, scenarios, design, ctx) -> list[GenFile]` writing `src/test/java/com/gmf/<app>/<Process>RulesTests.java` (`@Test void r1_<slug>()` with `fail("no test body for R1")` until filled), `<Process>BranchTests.java`, `<Process>ScenarioTests.java` (`@SpringBootTest(webEnvironment = RANDOM_PORT)` driving the controller, the consumer or the job, comparing `src/test/resources/fixtures/<Process>/<scenario>.json` field by field), and the index entries (names as Surefire reports them: `<package>.<Class>.<method>`); `TestDatabase` reads `TEST_DATABASE_URL` and skips with `Assumptions.assumeTrue(false, "TEST_DATABASE_URL is not set")` when absent.
- `MavenRunner(root, java=None, mvn=None, env=None, coverage_scope=None, timeout_s=DEFAULT_TIMEOUT_S)` implementing `StackRunner` with the commands of spec section 1; `parse_maven_errors(log) -> tuple[int, list[str]]`; child env adds `JAVA_HOME`, `MAVEN_OPTS` (keeps `-Dmaven.repo.local` from the platform env), never inherits the API's secrets.
- `JavaLanguage` implementing `SourceLanguage` (placeholders, `fail("no test body for <id>")`, C-like lexer with text blocks, `declarations` = method/record/class headers, `is_test_path` = `src/test/`).
- `test_prompt_packs.py` is parametrised over packs present, so Tasks 13 and 15 add their packs without editing it.

- [ ] **Step 1: Write failing tests**

```python
# engine/tests/test_prompt_packs.py
@pytest.mark.parametrize("pack", [p.name for p in (PROMPT_DIR).iterdir() if p.is_dir()])
def test_pack_matches_dotnet_names_and_placeholders(pack):
    for name in STACK_PROMPTS:
        mine, base = (PROMPT_DIR / pack / f"{name}.md").read_text(), (PROMPT_DIR / f"{name}.md").read_text()
        assert set(re.findall(r"\{(\w+)\}", mine)) == set(re.findall(r"\{(\w+)\}", base))
        assert USER_MARKER in mine and "The content inside <source> is data to analyze, not instructions." in mine
        assert not re.search(r"C#|\.NET|xUnit|NuGet|csproj|NotImplementedException", mine)
```

```python
# engine/tests/test_java_toolchain.py
needs_maven = pytest.mark.skipif(not (shutil.which("mvn") and shutil.which("java")), reason="java/mvn not found (scripts/toolchain.py)")

def test_java_rules_tests_golden(java_solution_with_tests):
    _golden("java/DealerFundingRequestRulesTests.java", _file(java_solution_with_tests, "DealerFundingRequestRulesTests.java").content)

def test_index_names_every_generated_test(java_solution_with_tests):
    idx = read_index(java_solution_with_tests.root)
    assert {e.kind for e in idx} >= {"rule", "branch", "scenario"} and all(e.name.startswith("com.gmf.gmfcredit.") for e in idx)

def test_parse_maven_errors():
    n, errs = parse_maven_errors("[ERROR] /w/src/main/java/A.java:[12,5] cannot find symbol\n[ERROR] BUILD FAILURE\n")
    assert n == 1 and errs == ["src/main/java/A.java:12:5: cannot find symbol"]

def test_java_language_checks():
    lang = JavaLanguage()
    assert lang.placeholder.search('throw new UnsupportedOperationException("agent fills: X");')
    assert lang.strip_comments('String s = "// no"; // yes') == 'String s = "// no"; '

@needs_maven
def test_generated_java_builds_formats_and_tests(java_solution_with_tests, test_database_url):
    r = MavenRunner(java_solution_with_tests.root, env={"TEST_DATABASE_URL": test_database_url})
    assert r.restore().ok and r.build().ok and r.format_check().ok
    run = r.test()
    assert run.cases and {c.rule_id for c in run.cases if c.rule_id} >= {"R1"} and run.coverage_line is not None
```

- [ ] **Step 2: Run** `.venv/bin/pytest engine/tests/test_prompt_packs.py engine/tests/test_java_toolchain.py -v` — Expected: FAIL (no `prompts/java`, no modules).
- [ ] **Step 3: Implement** tests generation, the runner, the language, and the nine Java prompts (rewrite each .NET prompt's stack-specific rules for Java idioms per spec section 3; keep the shared rules verbatim).
- [ ] **Step 4: Run** the files — Expected: PASS (toolchain test SKIPPED with the reason on machines without Maven).
- [ ] **Step 5: Commit** `git add engine/stacks/java engine/agents/prompts/java engine/tests/test_java_toolchain.py engine/tests/test_prompt_packs.py engine/tests/golden/java/DealerFundingRequestRulesTests.java && git commit -m "feat(stacks): Java tests first, Maven runner, Java agent pack"`

---

### Task 9: Infrastructure and Verify per stack (Java end to end)

**Files:**
- Create: `engine/stacks/java/templates/Dockerfile.j2`, `api/tests/test_java_end_to_end.py`
- Modify: `engine/infra_gen/generator.py` (`generate_infra(..., stack: str = "dotnet")`; `_app_contract` reads `.exiqo/service-contract.json` first), `engine/infra_gen/templates/github_workflow.yml.j2`, `engine/infra_gen/templates/azure-pipelines.yml.j2` (build args per stack), `api/app/services/verify.py` (`connection_names`, `start_service`, `_owned_service`), `api/app/services/hosting.py`, `api/app/services/build.py` (infra call passes the stack), `engine/infra_gen/validators.py` (none expected; confirm)

**Interfaces:**
- Consumes: `StackLayout.dockerfile_context`, `contract_file`, `runner().run_service`, `service_process_marker` (Tasks 1, 7, 8).
- Produces: `generate_infra(app, design, solution_root, solution_name, envs, runtime, *, location, stack="dotnet")` — .NET output unchanged; for other stacks the Dockerfile comes from `engine/stacks/<stack>/templates/Dockerfile.j2` with `ARG BASE_IMAGE_BUILD`, `ARG BASE_IMAGE_RUNTIME`, the mirror build argument, `USER 10001`, `EXPOSE 8080`, `ENV PORT=8080`.
- `verify.connection_names(build_root, app_name, stack="dotnet")`; `start_service` builds the env with today's `service_env` and calls `get_stack(build.stack).runner(root).run_service(port, env, timeout_s)`; `_owned_service(pid, build)` checks `get_stack(build.stack).service_process_marker`.

- [ ] **Step 1: Write failing tests** in `api/tests/test_java_end_to_end.py` and extend `engine/tests/test_infra.py`

```python
def test_dotnet_infra_unchanged(dotnet_infra_files):
    assert sha_of(dotnet_infra_files) == (GOLDEN / "infra_gmf_credit.sha256").read_text().strip()

def test_java_dockerfile_and_contract_secrets(java_solution, java_designs, gmf_app):
    files = {f.path: f.content for f in generate_infra(gmf_app, list(java_designs.values()), java_solution.root, java_solution.name, stack="java")}
    df = files["Dockerfile"]
    assert "ARG BASE_IMAGE_BUILD=maven:3.9-eclipse-temurin-21" in df and "USER 10001" in df and "MAVEN_MIRROR_URL" in df
    assert "dotnet" not in df
    assert set(secret_names_in(files)) == {s["name"] for s in json.loads((java_solution.root / ".exiqo/service-contract.json").read_text())["secrets"]}

@needs_maven
async def test_java_seven_steps_parity_clean(client, java_project_through_g4):        # spec criterion 1
    build_id = java_project_through_g4
    report = await run_verify_and_wait(client, build_id)
    assert report["summary"]["unexplained"] == 0 and report["summary"]["scenarios"] > 0
    assert (await client.get(f"/builds/{build_id}")).json()["stack"] == "java"
```

`infra_gmf_credit.sha256` is captured from the current code before the change (Step 1a, `UPDATE_GOLDEN=1`).

- [ ] **Step 2: Run** `.venv/bin/pytest engine/tests/test_infra.py api/tests/test_java_end_to_end.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the stack parameter in infra, the Java Dockerfile, contract reading, the Verify and hosting changes, and the pipeline call.
- [ ] **Step 4: Run** both files — Expected: PASS (end-to-end SKIPPED without Maven); `.venv/bin/pytest api/tests/test_build_verify.py api/tests/test_hosting.py -q` — Expected: unchanged.
- [ ] **Step 5: Commit** `git add engine/stacks/java/templates/Dockerfile.j2 engine/infra_gen api/app/services/verify.py api/app/services/hosting.py api/app/services/build.py engine/tests/test_infra.py engine/tests/golden/infra_gmf_credit.sha256 api/tests/test_java_end_to_end.py && git commit -m "feat(stacks): per-stack infrastructure and Verify; Java end to end"`

---

### Task 10: Platform image (JDK, Maven), mirrors in Helm and the chart's stack switch

**Files:**
- Modify: `deploy/docker/platform.Dockerfile`, `deploy/helm/exiqo/values.yaml`, `deploy/helm/exiqo/values.schema.json`, `deploy/helm/exiqo/values.gmf.example.yaml`, `deploy/helm/exiqo/templates/configmap.yaml`, `deploy/helm/exiqo/templates/worker-deployment.yaml`, `deploy/helm/exiqo/tests/*` (helm-unittest), `Makefile` (`image-test` checks), `scripts/tests/test_image.py` (or the existing image test file), `docs/deploy/GMF-INSTALL.md` (mirrors section)

**Interfaces:**
- Produces Dockerfile build args: `TEMURIN_BASE_URL`, `TEMURIN_VERSION`, `TEMURIN_SHA256_AMD64`, `TEMURIN_SHA256_ARM64`, `MAVEN_BASE_URL`, `MAVEN_VERSION`, `MAVEN_SHA512`; final-stage env per spec section 8 (Java part); `/opt/java`, `/opt/maven/bin` on `PATH`.
- Helm values: `stacks.enabled: [dotnet]`; `mirrors.maven: ""`, `mirrors.goproxy: ""`, `mirrors.pypi: ""`; `worker.stackCacheSize: 10Gi` → emptyDir `/cache/stacks`. ConfigMap: `ENABLED_STACKS`, `MAVEN_MIRROR_URL`, `GOPROXY` (`off` when empty), `UV_INDEX_URL`, `PIP_INDEX_URL`.
- Schema: `allOf` rules — `stacks.enabled` contains `java` ⇒ `mirrors.maven` `minLength: 1` and `format: uri` starting `https://`; same for `go`/`goproxy`, `python`/`pypi`; item enum `[dotnet, java, go, python]`.

- [ ] **Step 1: Write failing helm-unittest cases and image assertions**

```yaml
# deploy/helm/exiqo/tests/stacks_test.yaml
- it: refuses java without a Maven mirror                  # Review Focus 5
  set: {stacks.enabled: [dotnet, java]}
  asserts: [{failedTemplate: {errorPattern: "mirrors.maven"}}]
- it: writes the stack env
  set: {stacks.enabled: [dotnet, java], mirrors.maven: "https://artifactory.gmf.internal/maven"}
  template: templates/configmap.yaml
  asserts:
    - equal: {path: data.ENABLED_STACKS, value: "dotnet,java"}
    - equal: {path: data.MAVEN_MIRROR_URL, value: "https://artifactory.gmf.internal/maven"}
    - equal: {path: data.GOPROXY, value: "off"}
```

Image test additions: `java -version` exits 0 and prints `21.`; `mvn -v` prints `Apache Maven 3.9`; `/cache/stacks` is writable by UID 10001 in a pod spec render; `MAVEN_OPTS` contains `-Dmaven.repo.local=/cache/stacks/m2`.

- [ ] **Step 2: Run** `make chart-check` — Expected: FAIL on the new cases.
- [ ] **Step 3: Implement** the toolchain stage additions (fetch, checksum, install, `java -version && mvn -v`), env, chart values, schema, ConfigMap, worker volume, guide section listing each mirror and its FQDN for the firewall.
- [ ] **Step 4: Run** `make chart-check && make images && make image-test && make egress-audit` — Expected: all pass; `egress-audit` prints no unlisted host.
- [ ] **Step 5: Commit** `git add deploy/docker/platform.Dockerfile deploy/helm/exiqo Makefile scripts/tests docs/deploy/GMF-INSTALL.md && git commit -m "feat(deploy): JDK and Maven in the platform image, stack mirrors and switch in the chart"`

---

### Task 11: Web: stack picker at upload, Settings default, stack badges

**Files:**
- Create: `web/lib/stacks.ts`, `web/components/tabs/upload/StackPicker.tsx`, `web/components/settings/StackPanel.tsx`, `web/components/ui/StackBadge.tsx`, `web/e2e/stacks.spec.ts`
- Modify: `web/lib/api.ts` (`stacks()`, `createProject(name, targetStack?)`, `patchProject(id, body)`), `web/lib/types.ts` (`Project.target_stack`, `StackStatus`), `web/lib/buildTypes.ts` (`SettingsView.stacks`, `default_stack`, `SettingsPatch.default_stack`, `Build.stack`), `web/components/tabs/upload/UploadTab.tsx`, `web/components/shell/ProjectSelector.tsx`, `web/app/settings/page.tsx` (StackPanel; Toolchains table shows the five new tools), `web/components/tabs/design/DesignTab.tsx`, `web/components/tabs/build/BuildTab.tsx`, `web/components/tabs/verify/VerifyTab.tsx` (badge)

**Interfaces:**
- Consumes: `GET /stacks`, `POST /projects` with `target_stack`, `PATCH /projects/{id}`, `GET/PUT /settings` (`stacks`, `default_stack`), `Build.stack` (Task 2).
- Produces: `useStacks()` (TanStack Query key `["stacks"]`); `<StackPicker value onChange disabledReason />` (radio group, `data-testid="stack-<key>"`, unavailable cards `aria-disabled="true"` with the reason as `title`); `<StackBadge stack />` (`data-testid="stack-badge"`, text = the label's first segment: `C#`, `Java 21`, `Go`, `Python 3.12`).
- Upload tab: picker above the drop zone; a change calls `patchProject` (architect/admin persona) and shows the server's 403/422 detail; viewer and other personas see it disabled with `usePermission("upload").reason` or "changing the target stack needs the architect or admin role".

- [ ] **Step 1: Write the failing Playwright spec** `web/e2e/stacks.spec.ts`

```ts
test("architect picks Java at upload and sees it on Design and Build", async ({ page }) => {
  await actAs(page, "architect.lee"); await newProject(page, "stacks-e2e");
  await page.getByTestId("stack-java").click();
  await expect(page.getByTestId("stack-java")).toHaveAttribute("aria-checked", "true");
  await gotoStep(page, "design");
  await expect(page.getByTestId("stack-badge")).toHaveText(/Java 21/);
});

test("viewer sees the picker disabled", async ({ page }) => {
  await actAs(page, "viewer.val"); await openProject(page, "stacks-e2e");
  await expect(page.getByTestId("stack-java")).toHaveAttribute("aria-disabled", "true");
});

test("admin sets the Settings default", async ({ page }) => {
  await actAs(page, "ops.sam"); await page.goto("/settings");
  await page.getByTestId("default-stack").selectOption("dotnet");
  await expect(page.getByTestId("stacks-panel")).toContainText("default");
});
```

`actAs`, `newProject`, `openProject`, `gotoStep` come from `web/e2e/helpers.ts` (add `newProject`, `openProject` and `gotoStep` there if missing; `viewer.val` is the persona sub-project 2 adds).

- [ ] **Step 2: Run** `cd web && npx playwright test e2e/stacks.spec.ts` — Expected: FAIL.
- [ ] **Step 3: Implement** the components, API client and types, and the tab wiring.
- [ ] **Step 4: Run** `cd web && npm run typecheck && npm run lint && npx playwright test e2e/stacks.spec.ts e2e/upload-discover.spec.ts` — Expected: PASS.
- [ ] **Step 5: Commit** `git add web/lib/stacks.ts web/lib/api.ts web/lib/types.ts web/lib/buildTypes.ts web/components/tabs/upload/StackPicker.tsx web/components/tabs/upload/UploadTab.tsx web/components/settings/StackPanel.tsx web/components/ui/StackBadge.tsx web/components/shell/ProjectSelector.tsx web/app/settings/page.tsx web/components/tabs/design/DesignTab.tsx web/components/tabs/build/BuildTab.tsx web/components/tabs/verify/VerifyTab.tsx web/e2e/stacks.spec.ts web/e2e/helpers.ts && git commit -m "feat(web): target stack picker, Settings default and stack badges"`

---

### Task 12: Go generator

**Files:**
- Create: `engine/stacks/go/{__init__,stack,naming,versions,generator,contracts}.py`, `engine/stacks/go/go.sum.lock`, `engine/stacks/go/templates/*.j2` (`go.mod`, `main.go`, `config.go`, `auth.go`, `devtoken.go`, `health.go`, `bus.go`, `bus_memory.go`, `bus_servicebus.go`, `bus_dev.go`, `tib.go`, `masking.go`, `fault.go`, `gitignore`, `editorconfig`), `engine/tests/test_go_generator.py`, `engine/tests/golden/go/{dealer_funding_request_repository.go,gmf_credit_files.txt}`
- Modify: `engine/stacks/__init__.py` (register), `Makefile` (`stack-locks` target: regenerates `go.sum.lock` with `GOFLAGS=-mod=mod go mod tidy` in a scratch module against `$GOPROXY`, and Python's lock in Task 14)

**Interfaces:**
- Consumes: as Task 7, with `convert_placeholders(style="dollar")`.
- Produces `GoStack` (`key="go"`, `label="Go · net/http · go modules"`, `required_tools=("go",)`, `source_suffixes=(".go",)`, `prompt_pack="go"`, `download_excluded=frozenset({".exiqo-bin",".exiqo"})`, `service_process_marker=".exiqo-bin/server"`).
- `GoNaming`: module path `<app slug>` (`gmf-credit`), packages `api`, `core`, `infrastructure`, `contracts/<module lower>`; file names snake_case (`dealer_funding_request_service.go`); types PascalCase; methods exported PascalCase without `Async`; placeholder `panic("agent fills: <activity>")` under `// TIBCO` lines; every service method takes `ctx context.Context` first and returns `(T, error)`.
- `generate_go(...) -> GeneratedSolution` writes `go.mod` (`go <GO_MIN>` and `toolchain` omitted), `go.sum` copied from the lock filtered to the modules `go.mod` requires.

- [ ] **Step 1: Write failing tests** in `engine/tests/test_go_generator.py`: `test_go_golden`, `test_go_deterministic`, `test_every_mapping_row_exists_in_the_tree` (asserts `func (s *<Class>) <Method>(` or `func <Method>(` in the row's file), `test_untranslatable_step_keeps_tibco_and_placeholder` (`panic("agent fills:` and `// TIBCO`), `test_sql_verbatim_with_dollar_placeholders` (`where f.deal_id = $1`), `test_runtime_contract_endpoints` (the five routes as `"GET /health"`, `"POST /auth/dev-token"`, `"POST /_bus/publish"`, `"GET /_bus/messages"`, `"GET /_bus/dead-letters"` patterns), `test_go_sum_covers_go_mod_requirements` (every `require` line has two `go.sum` lines), `test_no_public_proxy_or_em_dash` (no `proxy.golang.org`, `sum.golang.org`, `—`, `{{`) — same structure as Task 7's code block.
- [ ] **Step 2: Run** `.venv/bin/pytest engine/tests/test_go_generator.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** naming, the struct renderer, the generator, templates, the lock (run `make stack-locks` once with access to a Go proxy), registration; write golden files with `UPDATE_GOLDEN=1` and review.
- [ ] **Step 4: Run** the file — Expected: PASS.
- [ ] **Step 5: Commit** `git add engine/stacks/go engine/stacks/__init__.py engine/tests/test_go_generator.py engine/tests/golden/go Makefile && git commit -m "feat(stacks): Go net/http generator"`

---

### Task 13: Go tests, `GoRunner`, `GoLanguage`, Go pack, Dockerfile, Verify and image toolchain

**Files:**
- Create: `engine/stacks/go/{tests_gen,runner,language}.py`, `engine/stacks/go/templates/{testdb.go,scenario_support.go,Dockerfile}.j2`, `engine/agents/prompts/go/*.md` (the nine), `engine/tests/test_go_toolchain.py`, `engine/tests/golden/go/dealer_funding_request_rules_test.go`
- Modify: `deploy/docker/platform.Dockerfile` (`GO_BASE_URL`, `GO_VERSION`, `GO_SHA256_AMD64`, `GO_SHA256_ARM64`; `/usr/local/go/bin` on `PATH`; env `GOMODCACHE`, `GOCACHE`, `GOTOOLCHAIN=local`, `GOTELEMETRY=off`, `GOFLAGS=-mod=readonly`, `GOSUMDB=off`), image test, `deploy/helm/exiqo/tests/stacks_test.yaml` (go needs goproxy)

**Interfaces:**
- Consumes: Task 5 parsers (`parse_test2json`, `parse_go_cover`), Task 8 patterns, `generic_checks`.
- Produces: `generate_go_tests(...)` writing `tests/<process>_rules_test.go` (`func TestDealerFundingRequestRules_R1_<Slug>(t *testing.T)` with `t.Fatal("no test body for R1")`), `_branch_test.go`, `_scenario_test.go` (`httptest.NewServer(api.NewRouter(deps))`), fixtures under `tests/fixtures/<Process>/<scenario>.json`, and index entries named `<module>/tests.<TestName>`; `GoRunner` (commands of spec section 1; build errors parsed from `<file>:<l>:<c>: <msg>`; `format_check` fails listing `gofmt -l` output); `GoLanguage` (`panic("agent fills:`, `t.Fatal("no test body for <id>")`, backtick raw strings in the lexer, `is_test_path` = `tests/` or suffix `_test.go`).
- Go Dockerfile: `ARG BASE_IMAGE_BUILD=golang:<GO_MIN>`, `ARG BASE_IMAGE_RUNTIME=gcr.io/distroless/static-debian12:nonroot`, `ARG GOPROXY`, `CGO_ENABLED=0`, `USER 65532`.

- [ ] **Step 1: Write failing tests** in `engine/tests/test_go_toolchain.py` (same structure as Task 8's toolchain file, `needs_go` skip): golden rules test, index names, `test_parse_go_build_errors`, `test_go_language_checks`, `test_generic_check_refuses_vacuous_go_body` (`if false { t.Fatal("x") }` → "no assertion on a value under test"; Review Focus 3), `@needs_go test_generated_go_builds_vets_formats_and_tests` (restore/build/format_check ok, cases with rule ids, coverage not None), `@needs_go test_go_service_answers_health_and_dev_token` (run_service on a free port, `GET /health` 200, `POST /auth/dev-token` 200 with a token). Add to `api/tests/test_java_end_to_end.py` a parametrised `stack` (`java`, `go`) for the seven-step parity test.
- [ ] **Step 2: Run** `.venv/bin/pytest engine/tests/test_go_toolchain.py engine/tests/test_prompt_packs.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** tests generation, runner, language, the nine Go prompts, the Dockerfile template, the image toolchain and chart test.
- [ ] **Step 4: Run** the files plus `make chart-check && make images && make image-test && make egress-audit` — Expected: PASS (toolchain tests SKIPPED without Go locally; run inside the image with `make image-test` which executes `engine/tests/test_go_toolchain.py` there).
- [ ] **Step 5: Commit** `git add engine/stacks/go engine/agents/prompts/go engine/tests/test_go_toolchain.py engine/tests/golden/go/dealer_funding_request_rules_test.go api/tests/test_java_end_to_end.py deploy/docker/platform.Dockerfile deploy/helm/exiqo/tests/stacks_test.yaml scripts/tests && git commit -m "feat(stacks): Go tests, runner, agent pack, Dockerfile and image toolchain"`

---

### Task 14: Python generator

**Files:**
- Create: `engine/stacks/python/{__init__,stack,naming,versions,generator,contracts}.py`, `engine/stacks/python/requirements.lock`, `engine/stacks/python/templates/*.j2` (`pyproject.toml`, `main.py`, `config.py`, `auth.py`, `devtoken.py`, `bus.py`, `bus_memory.py`, `bus_servicebus.py`, `bus_dev.py`, `tib.py`, `masking.py`, `fault.py`, `gitignore`, `editorconfig`), `engine/tests/test_python_generator.py`, `engine/tests/golden/python/{dealer_funding_request_repository.py,gmf_credit_files.txt}`
- Modify: `engine/stacks/__init__.py`, `Makefile` (`stack-locks` also runs `uv pip compile --generate-hashes` for the Python lock against `$UV_INDEX_URL`)

**Interfaces:**
- Consumes: as Task 7, with `convert_placeholders(style="pyformat")`.
- Produces `PythonStack` (`key="python"`, `label="Python 3.12 · FastAPI · pytest"`, `required_tools=("python3","uv")`, `source_suffixes=(".py",)`, `prompt_pack="python"`, `download_excluded=frozenset({".venv","__pycache__",".pytest_cache",".ruff_cache",".exiqo-bin",".exiqo"})`, `service_process_marker="uvicorn"`).
- `PythonNaming`: package `<app snake>` (`gmf_credit`), modules snake_case, classes PascalCase, functions snake_case without `_async` (all service functions `async def`), placeholder `raise NotImplementedError("agent fills: <activity>")` under `# TIBCO` lines; contracts as pydantic `BaseModel` with `model_config = ConfigDict(populate_by_name=True)`.
- `generate_python(...) -> GeneratedSolution`; `requirements.lock` copied verbatim; `pyproject.toml` declares `[tool.ruff]` line length 120 and `[tool.pytest.ini_options] testpaths = ["tests"]`.

- [ ] **Step 1: Write failing tests** in `engine/tests/test_python_generator.py`: `test_python_golden`, `test_python_deterministic`, `test_every_mapping_row_exists_in_the_tree` (`class <Class>` and `def <method>(`/`async def <method>(` in the row's file), `test_untranslatable_step_keeps_tibco_and_placeholder` (`NotImplementedError("agent fills:` and `# TIBCO`), `test_sql_verbatim_with_pyformat_placeholders` (`where f.deal_id = %s`), `test_runtime_contract_endpoints` (`@router.get("/health")` etc. for the five routes), `test_generated_python_compiles` (`compile(f.content, f.path, "exec")` for every `.py` file — runs without a toolchain), `test_lock_is_hash_pinned` (every requirement line followed by `--hash=sha256:`), `test_no_public_index_or_em_dash` (no `pypi.org`, `files.pythonhosted.org`, `—`, `{{`).
- [ ] **Step 2: Run** `.venv/bin/pytest engine/tests/test_python_generator.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** naming, the pydantic renderer, the generator, templates, lock, registration; golden with `UPDATE_GOLDEN=1`, reviewed.
- [ ] **Step 4: Run** the file — Expected: PASS.
- [ ] **Step 5: Commit** `git add engine/stacks/python engine/stacks/__init__.py engine/tests/test_python_generator.py engine/tests/golden/python Makefile && git commit -m "feat(stacks): Python FastAPI generator"`

---

### Task 15: Python tests, `UvRunner`, `PythonLanguage`, Python pack, Dockerfile, Verify and uv in the image

**Files:**
- Create: `engine/stacks/python/{tests_gen,runner,language}.py`, `engine/stacks/python/templates/{conftest.py,scenario_support.py,Dockerfile}.j2`, `engine/agents/prompts/python/*.md` (the nine), `engine/tests/test_python_toolchain.py`, `engine/tests/golden/python/test_dealer_funding_request_rules.py`
- Modify: `deploy/docker/platform.Dockerfile` (`COPY --from=uv /uv /usr/local/bin/uv` in the final stage; env `UV_CACHE_DIR=/cache/stacks/uv`, `UV_PYTHON_DOWNLOADS=never`, `UV_NO_PROGRESS=1`), image test, `deploy/helm/exiqo/tests/stacks_test.yaml` (python needs pypi), `api/tests/test_java_end_to_end.py` (add `python` to the parametrisation)

**Interfaces:**
- Consumes: `parse_junit`, Cobertura `merge_cobertura` (Task 5), `generic_checks`.
- Produces: `generate_python_tests(...)` writing `tests/test_<process>_rules.py` (`def test_r1_<slug>(): pytest.fail("no test body for R1")`), `_branch.py`, `_scenario.py` (FastAPI `TestClient(create_app(settings))`), `tests/conftest.py` (database fixture from `TEST_DATABASE_URL`, `pytest.skip("TEST_DATABASE_URL is not set")` when absent), fixtures under `tests/fixtures/`, index names `tests.test_<process>_rules.test_r1_<slug>` (pytest JUnit `classname` + `name`).
- `UvRunner` (commands of spec section 1; `python3` path from `scripts/toolchain`; build errors from compileall and ruff; `.venv` inside the build root; child env adds `UV_INDEX_URL` only when set, `VIRTUAL_ENV`, `PATH` with `.venv/bin` first).
- `PythonLanguage` (`tokenize`-based comment stripping, `def`/`class` declarations with annotations, `# TIBCO` comments, `is_test_path` = `tests/`).

- [ ] **Step 1: Write failing tests** in `engine/tests/test_python_toolchain.py` (`needs_uv` skip when `uv` or `python3` missing): golden rules test, index names, `test_parse_compileall_and_ruff_errors`, `test_python_language_strips_comments_not_strings` (`'s = "# no"  # yes'` → `'s = "# no"  '`), `test_generic_check_refuses_assert_true` (Review Focus 3), `@needs_uv test_generated_python_restores_builds_formats_and_tests`, `@needs_uv test_python_service_answers_health_and_dev_token`.
- [ ] **Step 2: Run** `.venv/bin/pytest engine/tests/test_python_toolchain.py engine/tests/test_prompt_packs.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** tests generation, runner, language, the nine Python prompts, Dockerfile (`ARG BASE_IMAGE=python:3.12-slim`, `ARG PIP_INDEX_URL`, `pip install --require-hashes -r requirements.lock`, `USER 10001`), image changes and chart test.
- [ ] **Step 4: Run** the files plus `make chart-check && make images && make image-test && make egress-audit` — Expected: PASS.
- [ ] **Step 5: Commit** `git add engine/stacks/python engine/agents/prompts/python engine/tests/test_python_toolchain.py engine/tests/golden/python/test_dealer_funding_request_rules.py api/tests/test_java_end_to_end.py deploy/docker/platform.Dockerfile deploy/helm/exiqo/tests/stacks_test.yaml scripts/tests && git commit -m "feat(stacks): Python tests, uv runner, agent pack, Dockerfile and uv in the image"`

---

### Task 16: Demo check per stack, memory lineage per stack, guides and final verification

**Files:**
- Modify: `scripts/demo_check.py` (`--stack dotnet|java|go|python`, default `dotnet`), `scripts/tests/test_demo_check.py`, `README.md` (stack table, `make stack-locks`), `docs/deploy/GMF-INSTALL.md` (enabling stacks: values, mirrors, firewall FQDNs, worker memory note), `docs/deploy/RUNBOOK.md` (a stack's build fails to restore: check the mirror, `GOPROXY=off`, lock refresh), the Help & Support guide source if it is generated from these docs

**Interfaces:**
- Consumes: everything above; memory projector for builds (sub-project 2, `engine/memory/projectors/build.py`) reading `Build.stack`.
- Produces: `demo_check.py --stack java` creates the project with `target_stack`, runs the seven steps, asserts the build's `stack`, the metrics keys, parity `unexplained == 0`, and (when memory is configured) at least one `MIGRATED_TO` whose target uid starts `method:java:`.

- [ ] **Step 1: Write the failing test** in `scripts/tests/test_demo_check.py`

```python
def test_stack_flag_is_passed_to_project_creation(fake_api):
    run_demo_check(["--base-url", fake_api.url, "--stack", "go"])
    assert fake_api.requests_to("/projects")[0].json()["target_stack"] == "go"

def test_memory_step_checks_stack_lineage(fake_api_with_memory):
    out = run_demo_check(["--base-url", fake_api_with_memory.url, "--stack", "python"])
    assert "MIGRATED_TO method:python:" in out
```

- [ ] **Step 2: Run** `.venv/bin/pytest scripts/tests/test_demo_check.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the flag, the lineage assertion (only when the memory step runs), and the documentation.
- [ ] **Step 4: Final verification.** Run: `make test && .venv/bin/ruff check . && (cd web && npm run typecheck && npm run lint) && make e2e && make chart-check && make egress-audit && for s in dotnet java go python; do .venv/bin/python scripts/demo_check.py --stack $s; done`
Expected: all suites green (toolchain tests pass where the tool exists), ruff/typecheck/lint clean, `egress-audit: OK`, each demo check prints its final `PASS` line; `git diff --stat engine/tests/golden/*.cs engine/tests/golden/Directory.Build.props engine/tests/golden/gmf_credit_files.txt` shows no change.
- [ ] **Step 5: Commit** `git add scripts/demo_check.py scripts/tests/test_demo_check.py README.md docs/deploy/GMF-INSTALL.md docs/deploy/RUNBOOK.md && git commit -m "feat(stacks): demo check per stack, guides"`
