"""Tests for infrastructure generation and validation (Task 16)."""
from __future__ import annotations

import re
import stat
from pathlib import Path

import pytest

from engine.analysis.static_rules import run_static_rules
from engine.domains import graph as graph_mod
from engine.dotnet_gen import design as design_mod
from engine.dotnet_gen.models import MessagingEntity
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


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


# --------------------------------------------------------------------- files
def test_infra_file_set(generated):
    root, files = generated
    expected = {
        "Dockerfile", ".dockerignore", "docker-compose.override.yml", "azure-pipelines.yml",
        f".github/workflows/{APP}.yml", "infra/live/terragrunt.hcl",
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
    for n in names:
        assert re.fullmatch(r"[A-Za-z0-9-]{1,127}", n)
    # non-secret keys never reach Key Vault
    assert "EmsUser" not in hcl


def test_container_app_env_secrets_and_ingress(generated):
    root, _ = generated
    hcl = _read(root, f"infra/live/dev/{APP}/container_app/terragrunt.hcl")
    assert "ingress_enabled" in hcl and "target_port" in hcl and "8080" in hcl
    assert "Gmf__DealerFunding__GmfDbUrl" in hcl  # secret env var from Key Vault
    assert "Gmf__DealerFunding__EmsUser" in hcl  # plain env var (non-secret config)
    assert "dependency.key_vault.outputs" in hcl
    assert "dependency.identity.outputs" in hcl
    assert "dependency.monitoring.outputs" in hcl
    assert "dependency.service_bus.outputs" in hcl


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
    hcl = _read(root, "infra/live/terragrunt.hcl")
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
        assert 'version = "~> 4.0"' in main, module
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
    ignore = _read(root, ".dockerignore")
    assert "**/bin" in ignore and "**/obj" in ignore and "infra" in ignore


def test_compose_override(generated):
    root, _ = generated
    text = _read(root, "docker-compose.override.yml")
    assert "postgres:16" in text
    assert "Messaging__Mode: InMemory" in text
    assert "ConnectionStrings__GMFFundingDB" in text
    assert '"8080:8080"' in text


def test_pipelines(generated):
    root, _ = generated
    gh = _read(root, f".github/workflows/{APP}.yml")
    assert "id-token: write" in gh and "azure/login@v2" in gh
    assert "terragrunt" in gh and "pull_request" in gh
    assert gh.count("environment: ") >= len(ENVS)
    assert "needs:" in gh
    ado = _read(root, "azure-pipelines.yml")
    for needle in ("DotNetCoreCLI", "AzureCLI", "terragrunt", "stage: Plan", "stage: Apply_prod"):
        assert needle in ado, needle
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
