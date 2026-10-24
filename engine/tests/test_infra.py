"""Tests for infrastructure generation and validation (Task 16)."""
from __future__ import annotations

import re
import stat
from pathlib import Path

import pytest

from engine.analysis.static_rules import run_static_rules
from engine.domains import graph as graph_mod
from engine.dotnet_gen import design as design_mod
from engine.dotnet_gen import generator as dotnet_generator
from engine.dotnet_gen.models import ConfigMap, MessagingEntity
from engine.infra_gen import generator, validators
from engine.tibco_parser.scanner import scan_folder

REPO_ROOT = Path(__file__).resolve().parents[2]
GMF = REPO_ROOT / "samples" / "synthetic-gmf"
APP = "gmf-credit"
SOLUTION = "GmfCredit"
ENVS = ("dev", "test", "prod")
MODULES = (
    "resource_group", "container_registry", "container_app", "aks_workload", "postgres_flexible",
    "service_bus", "key_vault", "monitoring", "identity",
)
LIVE_MODULES = tuple(m for m in MODULES if m != "aks_workload")


@pytest.fixture(scope="module")
def estate():
    apps = scan_folder(GMF).applications
    graph = graph_mod.build_graph(apps)
    app = next(a for a in apps if a.id == "gmf.credit")
    process = next(p for p in app.processes if p.name == "DealerFundingRequest")
    findings = run_static_rules(process, app.contracts, app.substvars)
    record = design_mod.build_design_skeleton(
        process, app, [], findings, graph_mod.io_card(process, app, graph)
    )
    return app, record


@pytest.fixture(scope="module")
def generated(estate, tmp_path_factory):
    app, record = estate
    root = tmp_path_factory.mktemp("solution")
    files = generator.generate_infra(app, record, root, SOLUTION)
    return root, files


def _env_names(hcl_text: str) -> set[str]:
    """Names set in the ``env_vars = { ... }`` block of a generated unit."""
    block = hcl_text[hcl_text.index("env_vars = {"):]
    block = block[: block.index("\n  }\n")]
    return set(re.findall(r"^\s{4}([A-Za-z_][A-Za-z0-9_]*)\s+=", block, re.MULTILINE))


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


# --------------------------------------------------------------------- files
def test_infra_file_set(generated):
    root, files = generated
    expected = {
        "Dockerfile", ".dockerignore", "docker-compose.override.yml", "azure-pipelines.yml",
        f".github/workflows/{APP}.yml", "infra/live/root.hcl",
        "infra/bootstrap/tfstate.sh",
    }
    for module in MODULES:
        for name in ("main.tf", "variables.tf", "outputs.tf"):
            expected.add(f"infra/modules/{module}/{name}")
    for env in ENVS:
        for module in LIVE_MODULES:
            expected.add(f"infra/live/{env}/{APP}/{module}/terragrunt.hcl")
    paths = {f.path for f in files}
    assert expected <= paths, sorted(expected - paths)
    assert not any("aks_workload" in p for p in paths if p.startswith("infra/live"))
    for rel in paths:
        assert (root / rel).is_file(), rel
    assert len(paths) == len(files)
    assert all(f.origin == "code" for f in files)


def test_generation_is_deterministic(estate, tmp_path):
    app, record = estate
    a = generator.generate_infra(app, record, tmp_path / "a", SOLUTION)
    b = generator.generate_infra(app, record, tmp_path / "b", SOLUTION)
    assert [(f.path, f.content) for f in a] == [(f.path, f.content) for f in b]


def test_aks_runtime_swaps_live_module(estate, tmp_path):
    app, record = estate
    files = generator.generate_infra(app, record, tmp_path, SOLUTION, runtime="aks")
    paths = {f.path for f in files}
    assert f"infra/live/dev/{APP}/aks_workload/terragrunt.hcl" in paths
    assert f"infra/live/dev/{APP}/container_app/terragrunt.hcl" not in paths
    assert "infra/modules/container_app/main.tf" in paths


def test_single_env(estate, tmp_path):
    app, record = estate
    files = generator.generate_infra(app, record, tmp_path, SOLUTION, envs=("dev",))
    paths = {f.path for f in files}
    assert f"infra/live/dev/{APP}/identity/terragrunt.hcl" in paths
    assert not any(p.startswith("infra/live/prod") for p in paths)


# --------------------------------------------------------------- service bus
def test_service_bus_entities_from_design(generated):
    root, _ = generated
    hcl = _read(root, f"infra/live/dev/{APP}/service_bus/terragrunt.hcl")
    inputs = hcl[hcl.index("inputs = {"):]
    assert '"gmf.dealer.funding.request"' in inputs
    assert '"gmf.funding.events"' in inputs
    queues = inputs[inputs.index("queues"):inputs.index("topics")]
    assert "gmf.dealer.funding.request" in queues and "gmf.funding.events" not in queues
    assert 'include "root"' in hcl
    assert 'source = "../../../../modules/service_bus"' in hcl
    assert 'config_path = "../resource_group"' in hcl


def test_service_bus_subscription_per_consuming_process(estate, tmp_path):
    app, record = estate
    consuming = record.model_copy(update={"messaging": [
        MessagingEntity(kind="topic", name="gmf.funding.events", direction="in"),
        MessagingEntity(kind="subscription", name="gmf.funding.events/DealerFundingRequest",
                        direction="in"),
        MessagingEntity(kind="queue", name="gmf.dealer.funding.request", direction="in"),
    ]})
    generator.generate_infra(app, consuming, tmp_path, SOLUTION)
    hcl = _read(tmp_path, f"infra/live/dev/{APP}/service_bus/terragrunt.hcl")
    subs = hcl[hcl.index("subscriptions"):]
    assert '"DealerFundingRequest"' in subs
    assert 'topic = "gmf.funding.events"' in subs


# ----------------------------------------------------------------- key vault
def test_key_vault_secret_names_sanitised(estate, generated):
    root, _ = generated
    _, record = estate
    secrets = [c.key for c in record.config if c.secret]
    assert secrets, "fixture must have secret config"
    hcl = _read(root, f"infra/live/dev/{APP}/key_vault/terragrunt.hcl")
    names = re.findall(r'"([A-Za-z0-9-]+)"', hcl[hcl.index("secret_names"):])
    assert "ConnectionStrings--GMFFundingDB" in names
    assert "Gmf--DealerFunding--GmfDbUrl" in names
    # JMS and JDBC credential rows the generated code does not read are not provisioned
    assert not any("Password" in n for n in names)
    for n in names:
        assert re.fullmatch(r"[A-Za-z0-9-]{1,127}", n)
    # non-secret keys never reach Key Vault
    assert "EmsUser" not in hcl


def test_container_app_env_and_ingress(generated):
    root, _ = generated
    hcl = _read(root, f"infra/live/dev/{APP}/container_app/terragrunt.hcl")
    assert "ingress_enabled" in hcl and "target_port" in hcl and "8080" in hcl
    env = _env_names(hcl)
    assert {"Messaging__Mode", "Messaging__ServiceBus__FullyQualifiedNamespace", "KeyVault__Uri"} <= env
    assert 'Messaging__Mode' in hcl and '"ServiceBus"' in hcl
    # the app reads secrets itself with the managed identity: no secret env vars, no Key Vault references
    assert "secrets = [" not in hcl and "key_vault_secret" not in hcl
    assert not any("Password" in n or "GmfDbUrl" in n for n in env)
    for dep in ("key_vault", "identity", "monitoring", "service_bus"):
        assert f"dependency.{dep}.outputs" in hcl or dep == "monitoring"
    assert 'dependency "monitoring"' in hcl


def test_postgres_databases_from_jdbc_resources(generated):
    root, _ = generated
    hcl = _read(root, f"infra/live/dev/{APP}/postgres_flexible/terragrunt.hcl")
    assert '"gmf_funding_db"' in hcl


def test_dependencies_follow_brief(generated):
    root, _ = generated
    rg = _read(root, f"infra/live/dev/{APP}/resource_group/terragrunt.hcl")
    assert "dependency " not in rg
    identity = _read(root, f"infra/live/dev/{APP}/identity/terragrunt.hcl")
    for dep in ("resource_group", "container_registry", "key_vault", "service_bus"):
        assert f'dependency "{dep}"' in identity
    app_hcl = _read(root, f"infra/live/dev/{APP}/container_app/terragrunt.hcl")
    for dep in ("resource_group", "identity", "monitoring", "service_bus", "key_vault"):
        assert f'dependency "{dep}"' in app_hcl
    assert "mock_outputs" in app_hcl


def test_root_terragrunt(generated):
    root, _ = generated
    hcl = _read(root, "infra/live/root.hcl")
    assert not (root / "infra/live/terragrunt.hcl").exists()
    unit = _read(root, f"infra/live/dev/{APP}/resource_group/terragrunt.hcl")
    assert 'find_in_parent_folders("root.hcl")' in unit
    assert 'remote_state {' in hcl and 'backend = "azurerm"' in hcl
    assert '"${local.app}/${local.env}/${local.module}.tfstate"' in hcl
    assert 'generate "provider"' in hcl
    assert "location" in hcl and "tags" in hcl


def test_module_templates_pin_azurerm(generated):
    root, _ = generated
    for module in MODULES:
        main = _read(root, f"infra/modules/{module}/main.tf")
        if module == "aks_workload":
            assert "kubernetes" in main
        assert 'version = "~> 4.69"' in main, module
        assert "hashicorp/azurerm" in main
    identity = _read(root, "infra/modules/identity/main.tf")
    for role in ("AcrPull", "Key Vault Secrets User", "Azure Service Bus Data Owner"):
        assert role in identity


# ----------------------------------------------------- docker and pipelines
def test_dockerfile_targets_net10(generated):
    root, _ = generated
    text = _read(root, "Dockerfile")
    assert "mcr.microsoft.com/dotnet/sdk:10.0" in text
    assert "mcr.microsoft.com/dotnet/aspnet:10.0" in text
    assert "ASPNETCORE_URLS=http://+:8080" in text
    assert 'ENTRYPOINT ["dotnet", "GmfCredit.Api.dll"]' in text
    assert re.search(r"^USER\s+\S+", text, re.MULTILINE) and "USER root" not in text
    assert "EXPOSE 8080" in text
    assert text.count("FROM ") == 2
    assert "ARG CONFIGURATION=Release" in text and "--configuration ${CONFIGURATION}" in text
    ignore = _read(root, ".dockerignore")
    assert "**/bin" in ignore and "**/obj" in ignore and "infra" in ignore


def test_compose_override(generated):
    root, _ = generated
    text = _read(root, "docker-compose.override.yml")
    assert "postgres:16" in text
    assert "Messaging__Mode: InMemory" in text
    assert "ConnectionStrings__GMFFundingDB" in text
    assert '"8080:8080"' in text
    assert "CONFIGURATION: Debug" in text and "Auth__Mode: Development" in text


def test_pipelines(generated):
    root, _ = generated
    gh = _read(root, f".github/workflows/{APP}.yml")
    assert "id-token: write" in gh and "azure/login@v2" in gh
    assert "terragrunt" in gh and "pull_request" in gh
    assert gh.count("environment: ") >= len(ENVS)
    assert "pull-requests: write" not in gh
    guard = "if: github.ref == 'refs/heads/main' && github.event_name != 'pull_request'"
    assert gh.count(guard) == 1 + len(ENVS)  # image job and one apply job per environment
    assert "needs:" in gh
    ado = _read(root, "azure-pipelines.yml")
    for needle in ("DotNetCoreCLI", "AzureCLI", "terragrunt", "stage: Plan", "stage: Apply_prod"):
        assert needle in ado, needle
    assert "publishTestResults: false" in ado and "--logger \"trx" in ado and "Agent.TempDirectory" in ado
    assert "refs/heads/main" in ado
    # both parse as YAML
    import yaml

    assert yaml.safe_load(gh)["jobs"]
    assert yaml.safe_load(ado)["stages"]
    assert yaml.safe_load(_read(root, "docker-compose.override.yml"))["services"]["postgres"]


def test_no_em_dashes(generated):
    root, files = generated
    for f in files:
        assert chr(0x2014) not in (root / f.path).read_text(encoding="utf-8"), f.path


# ---------------------------------------------------------------- validators
def _fake_tool(tmp_path: Path, name: str, script: str) -> str:
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_validate_infra_reports_missing_tools(generated):
    root, _ = generated
    result = validators.validate_infra(root, None, None)
    assert result.ok is False
    assert result.fmt_ok is None and result.hclfmt_ok is None and result.modules == []
    assert result.tools_present == {"terraform": False, "terragrunt": False}
    assert any("terraform" in n for n in result.notes)


def test_validate_infra_registry_unreachable_is_not_ok(generated, tmp_path):
    root, _ = generated
    tf = _fake_tool(
        tmp_path, "terraform",
        'case "$1" in\n'
        "  fmt) exit 0;;\n"
        '  init) echo "could not connect to registry.opentofu.org: Forbidden"; exit 1;;\n'
        "  *) exit 0;;\n"
        "esac\n",
    )
    result = validators.validate_infra(root, tf, None)
    assert result.fmt_ok is True
    assert len(result.modules) == len(MODULES)
    assert all(m.init_ok is False and m.validate_ok is None for m in result.modules)
    assert all(m.registry_unreachable for m in result.modules)
    assert result.registry_unreachable is True
    assert result.ok is False
    # the generated tree is not polluted by init artefacts
    assert not list((root / "infra").rglob(".terraform"))


def test_validate_infra_fmt_failure(generated, tmp_path):
    root, _ = generated
    tf = _fake_tool(tmp_path, "terraform", 'echo "main.tf"; exit 3\n')
    result = validators.validate_infra(root, tf, None)
    assert result.fmt_ok is False and result.ok is False


def test_validate_infra_scrubs_child_environment(generated, tmp_path, monkeypatch):
    secrets = {"CLAUDE_CODE_OAUTH_TOKEN": "tok", "AZURE_OPENAI_API_KEY": "key", "APP_SECRET": "s3cret",
               "LANGFUSE_SECRET_KEY": "lf", "DATABASE_URL": "postgresql://u:p@db/x"}
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TF_CLI_CONFIG_FILE", "/etc/tf.rc")
    root, _ = generated
    dump = tmp_path / "terraform.env"
    tf = _fake_tool(tmp_path, "terraform", f'/usr/bin/env > "{dump}"\nexit 0\n')
    validators.validate_infra(root, tf, None, env={"TEST_DATABASE_URL": "postgresql://t/x"})
    child = dict(line.split("=", 1) for line in dump.read_text().splitlines() if "=" in line)
    assert not set(secrets) & set(child)
    assert child["TEST_DATABASE_URL"] == "postgresql://t/x"
    assert child["TF_CLI_CONFIG_FILE"] == "/etc/tf.rc" and child["TF_IN_AUTOMATION"] == "1"


def _real_tools():
    import toolchain  # scripts/toolchain.py, on sys.path via conftest

    info = toolchain.detect()
    return info["terraform"]["path"], info["terragrunt"]["path"]


def test_validate_infra_formatting(generated):
    terraform, terragrunt = _real_tools()
    if not terraform or not terragrunt:
        pytest.skip("terraform/tofu or terragrunt is not installed")
    root, _ = generated
    result = validators.validate_infra(root, terraform, terragrunt)
    assert result.tools_present == {"terraform": True, "terragrunt": True}
    assert result.fmt_ok is True, result.notes
    assert result.hclfmt_ok is True, result.notes


def test_terragrunt_renders_live_units_offline(generated):
    """Every dev unit parses and evaluates (includes, dependencies with mock outputs, get_env)."""
    import json
    import subprocess

    _, terragrunt = _real_tools()
    if not terragrunt:
        pytest.skip("terragrunt is not installed")
    probe = subprocess.run([terragrunt, "render", "--help"], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        pytest.skip("this terragrunt has no `render` command (needs 1.x)")
    root, _ = generated
    for module in LIVE_MODULES:
        unit = root / f"infra/live/dev/{APP}/{module}"
        proc = subprocess.run([terragrunt, "render", "--format", "json"], cwd=unit,
                              capture_output=True, text=True, timeout=120, check=False)
        assert proc.returncode == 0, f"{module}: {proc.stderr[-800:]}"
        cfg = json.loads(proc.stdout)
        assert cfg["terraform"]["source"].endswith(f"modules/{module}")
        assert cfg["remote_state"]["config"]["key"] == f"{APP}/dev/{module}.tfstate"
        assert cfg["inputs"]["location"]
        assert cfg["inputs"]["tags"]["environment"] == "dev"


def test_validate_infra_passes(generated):
    terraform, terragrunt = _real_tools()
    if not terraform or not terragrunt:
        pytest.skip("terraform/tofu or terragrunt is not installed")
    root, _ = generated
    result = validators.validate_infra(root, terraform, terragrunt)
    if result.registry_unreachable:
        pytest.skip("terraform registry unreachable")
    for m in result.modules:
        assert m.init_ok, f"{m.name}: {m.log}"
        assert m.validate_ok, f"{m.name}: {m.log}"
    assert result.ok, result.notes


# ------------------------------------------- against a really generated solution
PROCESS = "dealerfunding.DealerFundingRequest"


@pytest.fixture(scope="module")
def solution(estate, tmp_path_factory):
    """The .NET solution from Task 14's generator with the infrastructure generated beside it."""
    app, _ = estate
    root = tmp_path_factory.mktemp("full") / SOLUTION
    assert "samples" not in root.parts
    dotnet_generator.generate_solution(app, [PROCESS], {}, {}, {}, [], root)
    record = _record_for(app)
    files = generator.generate_infra(app, record, root, SOLUTION)
    return root, files


def _record_for(app):
    graph = graph_mod.build_graph([app])
    process = next(p for p in app.processes if p.id == PROCESS)
    findings = run_static_rules(process, app.contracts, app.substvars)
    return design_mod.build_design_skeleton(process, app, [], findings, graph_mod.io_card(process, app, graph))


def test_dockerfile_copy_sources_exist_in_generated_solution(solution):
    root, _ = solution
    text = _read(root, "Dockerfile")
    copies = [ln for ln in text.splitlines() if ln.startswith("COPY ") and "--from" not in ln]
    assert len(copies) >= 5
    checked = 0
    for line in copies:
        *sources, dest = line.split()[1:]
        for src in sources:
            if src == ".":
                continue
            matches = list(root.glob(src))
            assert matches, f"COPY source {src!r} matches nothing in the generated solution"
            checked += 1
            if "*" not in src:
                assert dest.endswith("/"), line
    layout = dotnet_generator.solution_layout(SOLUTION)
    assert layout["api_project"] in text and layout["api_dll"] in text
    assert (root / layout["api_project"]).is_file()
    assert f"src/{SOLUTION}.Api/{SOLUTION}.Api.csproj" in text
    assert checked >= 5


def test_env_vars_match_app_configuration(solution):
    """Every configuration key the deployment sets is read by the generated code."""
    root, _ = solution
    sources = "\n".join(p.read_text(encoding="utf-8") for p in (root / "src").rglob("*.cs"))
    host_or_sdk = {"ASPNETCORE_ENVIRONMENT", "AZURE_CLIENT_ID"}
    for runtime_module in ("container_app",):
        env = _env_names(_read(root, f"infra/live/dev/{APP}/{runtime_module}/terragrunt.hcl"))
        assert {"Messaging__Mode", "Messaging__ServiceBus__FullyQualifiedNamespace", "KeyVault__Uri"} <= env
        for name in sorted(env - host_or_sdk):
            key = name.replace("__", ":")
            assert f'"{key}"' in sources, f"{name} is set by the infrastructure but {key} is never read"
    # secrets: exactly what the app announces in Program.cs, none invented
    program = _read(root, f"src/{SOLUTION}.Api/Program.cs")
    announced = sorted(re.findall(r"Key Vault secret (\S+) ->", program))
    hcl = _read(root, f"infra/live/dev/{APP}/key_vault/terragrunt.hcl")
    provisioned = sorted(re.findall(r'"([A-Za-z0-9-]+)"', hcl[hcl.index("secret_names"):]))
    assert provisioned == announced and announced


def test_aks_uses_same_configuration_keys(estate, tmp_path):
    app, record = estate
    aks = tmp_path / "aks"
    dotnet_generator.generate_solution(app, [PROCESS], {}, {}, {}, [], aks)
    generator.generate_infra(app, record, aks, SOLUTION, runtime="aks")
    env = _env_names(_read(aks, f"infra/live/dev/{APP}/aks_workload/terragrunt.hcl"))
    assert env == {"ASPNETCORE_ENVIRONMENT", "KeyVault__Uri", "Messaging__Mode",
                   "Messaging__ServiceBus__FullyQualifiedNamespace"}
    main = _read(aks, "infra/modules/aks_workload/main.tf")
    assert "workload identity" in main.lower() and "read_only_root_filesystem" in main and "AcrPull" in main


def test_service_bus_connection_string_is_not_a_database(estate, tmp_path):
    app, record = estate
    extra = record.model_copy(update={"config": [
        *record.config,
        ConfigMap(substvar="resource:GMFEMSConnection.jdbc_url", key="ConnectionStrings:ServiceBus", secret=True),
    ]})
    generator.generate_infra(app, extra, tmp_path, SOLUTION)
    pg = _read(tmp_path, f"infra/live/dev/{APP}/postgres_flexible/terragrunt.hcl")
    assert "servicebus" not in pg.lower()
    kv = _read(tmp_path, f"infra/live/dev/{APP}/key_vault/terragrunt.hcl")
    assert "ConnectionStrings--ServiceBus" not in kv
    assert "ConnectionStrings__ServiceBus" not in _read(tmp_path, "docker-compose.override.yml")


def test_regeneration_removes_stale_units(estate, tmp_path):
    app, record = estate
    generator.generate_infra(app, record, tmp_path, SOLUTION)
    assert (tmp_path / f"infra/live/prod/{APP}/container_app/terragrunt.hcl").is_file()
    other = tmp_path / "infra/live/dev/other-app/resource_group/terragrunt.hcl"
    other.parent.mkdir(parents=True)
    other.write_text("# another application\n", encoding="utf-8")
    generator.generate_infra(app, record, tmp_path, SOLUTION, envs=("dev",), runtime="aks")
    assert not (tmp_path / "infra/live/prod").exists()
    assert not (tmp_path / "infra/live/test").exists()
    assert not (tmp_path / f"infra/live/dev/{APP}/container_app").exists()
    assert (tmp_path / f"infra/live/dev/{APP}/aks_workload/terragrunt.hcl").is_file()
    assert other.is_file()  # other applications are not touched


def test_tfstate_bootstrap_matches_root(generated):
    root, _ = generated
    script = _read(root, "infra/bootstrap/tfstate.sh")
    hcl = _read(root, "infra/live/root.hcl")
    for name in re.findall(r'"(stgmfcredittfstate\w+|rg-gmf-credit-tfstate)"', hcl):
        assert name in script
    assert "--allow-shared-key-access false" in script and "set -euo pipefail" in script


def test_rbac_propagation_waits(generated):
    root, _ = generated
    assert "time_sleep" in _read(root, "infra/modules/key_vault/main.tf")
    assert "time_sleep" in _read(root, "infra/modules/identity/main.tf")


def test_bare_forbidden_is_not_registry_unreachable():
    rx = validators._REGISTRY_FAILURE
    assert not rx.search("Error: Forbidden: 403 from the module source")
    assert rx.search("could not connect to registry.opentofu.org: Forbidden")
    assert rx.search("Failed to query available provider packages")


# ------------------------------------------------------- the exported bundle runs on its own
# A reviewer who downloads a build gets a folder, not this platform. What tells them how to start it
# has to travel with the folder, name the real project paths, and set the configuration the API
# needs, or the bundle is a pile of C# and the Swagger UI is unreachable.
LOCAL_RUN_FILES = ("run-local.sh", "run-local.ps1", "RUN_LOCALLY.md")


def test_the_bundle_carries_the_scripts_that_start_it(generated):
    root, files = generated
    paths = {f.path for f in files}
    for rel in LOCAL_RUN_FILES:
        assert rel in paths, rel
        assert (root / rel).is_file(), rel


def test_run_local_sh_is_valid_bash(generated):
    """A script nobody can run is worse than no script: it is checked here, not on a reviewer's laptop."""
    import subprocess
    root, _ = generated
    done = subprocess.run(["bash", "-n", str(root / "run-local.sh")], check=False,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("script", ["run-local.sh", "run-local.ps1"])
def test_the_scripts_start_the_api_with_the_configuration_it_needs(generated, script):
    root, _ = generated
    text = _read(root, script)
    # Without each of these the API either refuses to start or answers 401 to every call.
    for token in ("ASPNETCORE_ENVIRONMENT", "Development", "Auth__Mode", "Messaging__Mode", "InMemory",
                  "KeyVault__Uri"):
        assert token in text, f"{script} does not set {token}"
    # The paths are the generated solution's own, not a guess.
    layout = dotnet_generator.solution_layout(SOLUTION)
    assert layout["solution"] in text and layout["api_project"] in text
    # Both routes a reviewer needs, and the wait that makes the printed URL true.
    assert "/health" in text and "/auth/dev-token" in text
    assert f"localhost:{generator.TARGET_PORT}" in text
    # The compose file it drives is the one the generator wrote beside it.
    assert "docker-compose.override.yml" in text


@pytest.mark.parametrize("script", ["run-local.sh", "run-local.ps1"])
def test_the_scripts_carry_every_connection_string_the_solution_reads(generated, script):
    """A missing connection string surfaces as a start-up failure in the container log, which is the
    hardest place for a reviewer to read it."""
    root, _ = generated
    text = _read(root, script)
    compose = _read(root, "docker-compose.override.yml")
    names = set(re.findall(r"ConnectionStrings__(\w+)", compose))
    assert names, "the compose file names no connection string, so this test proves nothing"
    for name in names:
        assert f"ConnectionStrings__{name}" in text, f"{script} does not set ConnectionStrings__{name}"


def test_run_locally_names_the_swagger_url_and_both_ways_to_start(generated):
    root, _ = generated
    text = _read(root, "RUN_LOCALLY.md")
    port = generator.TARGET_PORT
    assert f"http://localhost:{port}/swagger" in text
    assert "bash run-local.sh" in text and "run-local.ps1" in text
    assert "bash run-local.sh --dotnet" in text
    assert f"dotnet test {dotnet_generator.solution_layout(SOLUTION)['solution']}" in text
    # The local credentials are explained as local, so nobody reads them as a leaked secret.
    assert "local-only" in text and "Key Vault" in text


def test_the_dotnet_generator_leaves_the_local_run_files_to_the_infrastructure_generator(generated):
    """Both generators write into one folder. The dotnet one prunes what it no longer writes, so a
    path it does not reserve would be deleted on the next regeneration."""
    for rel in (*LOCAL_RUN_FILES, "docker-compose.override.yml"):
        assert rel in dotnet_generator.RESERVED_FOR_INFRA, rel


@pytest.mark.parametrize("flag, missing", [([], "docker"), (["--dotnet"], "dotnet")])
def test_run_local_sh_says_what_is_missing_instead_of_hanging(generated, tmp_path, flag, missing):
    """Run with neither tool on PATH. A reviewer without Docker has to be told that in one line,
    not left watching a script wait for a health check that can never answer."""
    import subprocess
    root, _ = generated
    empty = tmp_path / "empty-path"
    empty.mkdir()
    done = subprocess.run(
        ["bash", str(root / "run-local.sh"), *flag],
        check=False, capture_output=True, text=True, timeout=60,
        env={"PATH": f"{empty}:/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert done.returncode == 1, done.stdout + done.stderr
    assert f"{missing} is not on PATH" in done.stderr
