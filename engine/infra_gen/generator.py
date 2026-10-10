"""Infrastructure generation (design spec section 8).

``generate_infra`` writes, beside the .NET solution, the Terraform modules, the Terragrunt live tree,
the Dockerfile, the local compose file and the CI/CD pipelines. Everything is derived from the IR
application and the design record by code; nothing here calls a model.

Layout (relative to the solution root)::

    infra/modules/<module>/{main,variables,outputs}.tf     nine modules, copied from templates/
    infra/live/root.hcl                                    root: azurerm backend, provider, common inputs
    infra/bootstrap/tfstate.sh                             creates the state storage root.hcl points at
    infra/live/<env>/<app>/<module>/terragrunt.hcl         one unit per module and environment
    Dockerfile, .dockerignore, docker-compose.override.yml
    .github/workflows/<app>.yml, azure-pipelines.yml

Design record to infrastructure:
  * Service Bus queues, topics and subscriptions come from ``design.messaging``;
  * Key Vault secret names are the secrets the generated app reads (the ``Key Vault secret NAME -> KEY``
    lines of its Program.cs) when the solution is already in ``solution_root``, else the design record's
    ``secret=True`` configuration (colons become ``--``, the .NET Key Vault convention);
  * the app reads secrets itself (``AddAzureKeyVault`` with the managed identity), so the container gets
    no secret environment variables and no Key Vault references. Its environment holds exactly the
    configuration keys the app reads: ``Messaging__Mode``, ``Messaging__ServiceBus__FullyQualifiedNamespace``
    and ``KeyVault__Uri``, plus the host/SDK variables ``ASPNETCORE_ENVIRONMENT`` and ``AZURE_CLIENT_ID``
    (selects the user-assigned identity), plus design config keys present in the app's appsettings.json;
  * ingress is enabled when the application has a REST, HTTP or SOAP starter;
  * Postgres databases come from the JDBC resources the design uses (``ConnectionStrings:<Resource>`` of a JDBC resource; ``ConnectionStrings:ServiceBus`` is not a database).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel

from engine.dotnet_gen.generator import GenFile, solution_layout
from engine.dotnet_gen.models import DesignRecord
from engine.infra_gen import hcl
from engine.ir.models import Application

TEMPLATES = Path(__file__).resolve().parent / "templates"
MODULES = (
    "resource_group", "container_registry", "container_app", "aks_workload", "postgres_flexible",
    "service_bus", "key_vault", "monitoring", "identity",
)
DEFAULT_ENVS = ("dev", "test", "prod")
DEFAULT_LOCATION = "eastus2"
TERRAGRUNT_VERSION = "v1.1.6"
TERRAFORM_VERSION = "1.13.0"
TARGET_PORT = 8080
_API_STARTERS = ("rest", "http", "soap")

# Modules the live tree instantiates for each runtime; aks_workload replaces container_app on AKS.
_COMMON_MODULES = (
    "resource_group", "monitoring", "container_registry", "service_bus", "key_vault",
    "postgres_flexible", "identity",
)
_RUNTIME_MODULE = {"container_app": "container_app", "aks": "aks_workload"}

# Cross-module wiring. The brief's "identity -> key_vault" would be a cycle, because the identity
# module grants roles scoped to the vault, so the vault stays independent and identity depends on it.
DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "resource_group": (),
    "monitoring": ("resource_group",),
    "container_registry": ("resource_group",),
    "service_bus": ("resource_group",),
    "key_vault": ("resource_group",),
    "postgres_flexible": ("resource_group",),
    "identity": ("resource_group", "container_registry", "key_vault", "service_bus"),
    "container_app": ("resource_group", "monitoring", "container_registry", "service_bus",
                      "key_vault", "identity"),
    "aks_workload": ("resource_group", "container_registry", "service_bus", "key_vault", "identity"),
}

_SUB = "/subscriptions/00000000-0000-0000-0000-000000000000"
_MOCK_RG_ID = f"{_SUB}/resourceGroups/mock-rg"


def _mock_outputs(dep: str, secret_names: Sequence[str]) -> dict[str, Any]:
    """Placeholder outputs so ``plan`` and ``validate`` work before a dependency is applied."""
    prov = f"{_MOCK_RG_ID}/providers"
    if dep == "resource_group":
        return {"name": "mock-rg", "id": _MOCK_RG_ID, "location": DEFAULT_LOCATION}
    if dep == "container_registry":
        return {"id": f"{prov}/Microsoft.ContainerRegistry/registries/mockacr", "name": "mockacr",
                "login_server": "mockacr.azurecr.io"}
    if dep == "monitoring":
        return {
            "log_analytics_workspace_id": f"{prov}/Microsoft.OperationalInsights/workspaces/mock-log",
            "app_insights_id": f"{prov}/Microsoft.Insights/components/mock-appi",
            "app_insights_connection_string": "InstrumentationKey=00000000-0000-0000-0000-000000000000",
        }
    if dep == "service_bus":
        return {"namespace_id": f"{prov}/Microsoft.ServiceBus/namespaces/mock-sb",
                "namespace_name": "mock-sb", "fqdn": "mock-sb.servicebus.windows.net"}
    if dep == "key_vault":
        return {
            "id": f"{prov}/Microsoft.KeyVault/vaults/mock-kv", "name": "mock-kv",
            "vault_uri": "https://mock-kv.vault.azure.net/",
            "secret_ids": {n: f"https://mock-kv.vault.azure.net/secrets/{n}" for n in secret_names},
        }
    if dep == "identity":
        return {"id": f"{prov}/Microsoft.ManagedIdentity/userAssignedIdentities/mock-id",
                "principal_id": "00000000-0000-0000-0000-000000000001",
                "client_id": "00000000-0000-0000-0000-000000000002",
                "tenant_id": "00000000-0000-0000-0000-000000000003"}
    raise KeyError(dep)


class EnvProfile(BaseModel):
    acr_sku: str
    sb_sku: str
    sb_capacity: int
    kv_purge_protection: bool
    kv_soft_delete_days: int
    log_retention_days: int
    pg_sku: str
    pg_storage_mb: int
    pg_backup_days: int
    min_replicas: int
    max_replicas: int
    cpu: float
    memory: str


_PROFILES: dict[str, EnvProfile] = {
    "dev": EnvProfile(acr_sku="Basic", sb_sku="Standard", sb_capacity=0, kv_purge_protection=False,
                      kv_soft_delete_days=7, log_retention_days=30, pg_sku="B_Standard_B1ms",
                      pg_storage_mb=32768, pg_backup_days=7, min_replicas=1, max_replicas=2,
                      cpu=0.5, memory="1Gi"),
    "test": EnvProfile(acr_sku="Basic", sb_sku="Standard", sb_capacity=0, kv_purge_protection=False,
                       kv_soft_delete_days=7, log_retention_days=30, pg_sku="B_Standard_B2s",
                       pg_storage_mb=32768, pg_backup_days=7, min_replicas=1, max_replicas=3,
                       cpu=0.5, memory="1Gi"),
    "prod": EnvProfile(acr_sku="Standard", sb_sku="Premium", sb_capacity=1, kv_purge_protection=True,
                       kv_soft_delete_days=90, log_retention_days=90, pg_sku="GP_Standard_D2s_v3",
                       pg_storage_mb=131072, pg_backup_days=35, min_replicas=2, max_replicas=10,
                       cpu=1.0, memory="2Gi"),
}


# ---------------------------------------------------------------- naming
def slugify(text: str) -> str:
    """``gmf.credit`` -> ``gmf-credit``: lowercase letters, digits and single dashes."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "app"


def short_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:4]


def _fit(base: str, suffix: str, limit: int, sep: str = "-") -> str:
    """``base`` + ``sep`` + ``suffix`` within ``limit`` characters, trimming the base first."""
    tail = f"{sep}{suffix}" if suffix else ""
    room = max(limit - len(tail), 1)
    return base[:room].rstrip("-_") + tail


def secret_name(key: str) -> str:
    """Key Vault secret name for a configuration key: letters, digits and dashes only.

    ``:`` becomes ``--`` (what the .NET Key Vault configuration provider maps back to ``:``).
    """
    name = key.replace(":", "--")
    name = re.sub(r"[^A-Za-z0-9-]+", "-", name)
    name = re.sub(r"-{3,}", "--", name).strip("-")
    return (name or "secret")[:127].rstrip("-")


def env_var_name(key: str) -> str:
    """``Gmf:DealerFunding:Key`` -> ``Gmf__DealerFunding__Key`` (the .NET environment variable form)."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", key.replace(":", "__"))


def database_name(resource: str) -> str:
    """``GMFFundingDB`` -> ``gmf_funding_db`` (lowercase snake case, valid unquoted in Postgres)."""
    parts = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", resource)
    name = re.sub(r"[^a-z0-9_]+", "_", parts.lower()).strip("_")
    return (f"db_{name}" if name[:1].isdigit() else name)[:63] or "app"


class _Names(BaseModel):
    rg: str
    acr: str
    kv: str
    sb: str
    psql: str
    log: str
    appi: str
    identity: str
    cae: str
    ca: str


def _names(app_slug: str, env: str, app_id: str) -> _Names:
    e = slugify(env)
    h = short_hash(app_id, env)
    flat = app_slug.replace("-", "")
    return _Names(
        rg=_fit(f"rg-{app_slug}-{e}", "", 90),
        acr=_fit(f"acr{flat}{e}", h, 50, sep=""),
        kv=_fit(f"kv-{app_slug}-{e}", h, 24),
        sb=_fit(f"sb-{app_slug}-{e}", h, 50),
        psql=_fit(f"psql-{app_slug}-{e}", h, 63),
        log=_fit(f"log-{app_slug}-{e}", "", 63),
        appi=_fit(f"appi-{app_slug}-{e}", "", 260),
        identity=_fit(f"id-{app_slug}-{e}", "", 128),
        cae=_fit(f"cae-{app_slug}-{e}", "", 60),
        ca=_fit(f"ca-{app_slug}-{e}", "", 32),
    )


# ---------------------------------------------------- design record to inputs
class _Facts(BaseModel):
    """Everything the live inputs need, distilled from the application and the design record(s)."""

    app_id: str
    app_slug: str
    solution: str
    queues: list[str]
    topics: list[str]
    subscriptions: list[tuple[str, str]]  # (topic, subscription)
    inbound_queues: list[str]
    secret_names: list[str]  # Key Vault secret names the app reads
    plain_env: dict[str, str]  # design config keys the app's appsettings.json carries
    databases: list[str]
    connection_strings: list[tuple[str, str]]  # (resource name, database)
    ingress: bool


def _dedupe(items: Sequence[Any]) -> list[Any]:
    return list(dict.fromkeys(items))


def _as_list(design: DesignRecord | Sequence[DesignRecord]) -> list[DesignRecord]:
    return [design] if isinstance(design, DesignRecord) else list(design)


def _sub_name(raw: str) -> str:
    """Subscription entity names are letters, digits, dots, dashes and underscores (50 at most)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-.")[:50] or "subscription"


_KV_SECRET_LINE = re.compile(r"^\s*//\s+Key Vault secret (\S+) -> (\S+)\s*$", re.MULTILINE)


def _flatten(node: Any, prefix: str = "") -> dict[str, str]:
    """appsettings.json as ``Section:Key`` -> text for every leaf."""
    if isinstance(node, dict):
        out: dict[str, str] = {}
        for k, v in node.items():
            out.update(_flatten(v, f"{prefix}:{k}" if prefix else k))
        return out
    return {prefix: json.dumps(node) if not isinstance(node, str) else node}


def _app_contract(root: Path, solution: str) -> tuple[list[str] | None, dict[str, str] | None]:
    """What the generated app reads, when the solution is already in ``root``.

    Returns (Key Vault secret names listed in Program.cs, flattened appsettings.json); ``None`` for
    a part that is not there, in which case the design record is the source.
    """
    api = root / solution_layout(solution)["projects"]["api"]["folder"]
    secrets: list[str] | None = None
    settings: dict[str, str] | None = None
    program = api / "Program.cs"
    if program.is_file():
        secrets = [m.group(1) for m in _KV_SECRET_LINE.finditer(program.read_text(encoding="utf-8"))]
    appsettings = api / "appsettings.json"
    if appsettings.is_file():
        try:
            settings = _flatten(json.loads(appsettings.read_text(encoding="utf-8")))
        except ValueError:
            settings = None
    return secrets, settings


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _facts(app: Application, designs: list[DesignRecord], solution: str, root: Path) -> _Facts:
    queues: list[str] = []
    topics: list[str] = []
    subs: list[tuple[str, str]] = []
    inbound: list[str] = []
    for d in designs:
        for e in d.messaging:
            if e.kind == "queue":
                queues.append(e.name)
                if e.direction == "in":
                    inbound.append(e.name)
            elif e.kind == "topic":
                topics.append(e.name)
            elif "/" in e.name:
                topic, _, sub = e.name.rpartition("/")
                topics.append(topic)
                subs.append((topic, _sub_name(sub)))
    app_secrets, app_settings = _app_contract(root, solution)
    # Secrets: the app's own list when generated, else the design's. Service Bus is reached with the
    # managed identity, so a ConnectionStrings:ServiceBus entry is never a secret to provision.
    design_secrets: dict[str, str] = {}  # key -> kv name
    plain: dict[str, str] = {}
    for d in designs:
        for c in d.config:
            if c.secret:
                if c.key == "ConnectionStrings:ServiceBus":
                    continue
                if c.substvar.startswith("resource:") and not c.key.startswith("ConnectionStrings:"):
                    continue  # JMS and JDBC credentials the generated code does not read
                design_secrets.setdefault(c.key, secret_name(c.key))
            elif app_settings is not None and c.key in app_settings:
                plain.setdefault(env_var_name(c.key), app_settings[c.key])
    secret_names = _dedupe(sorted(app_secrets if app_secrets is not None else design_secrets.values()))
    secret_keys = {n: n.replace("--", ":") for n in secret_names}
    # Databases: ConnectionStrings:<Resource> that names a JDBC resource of the application.
    jdbc = {_alnum(r.name): r.name for r in app.resources if r.kind == "jdbc"}
    conn: list[tuple[str, str]] = []
    for key in sorted(secret_keys.values()):
        if key.startswith("ConnectionStrings:"):
            res = key.split(":", 1)[1]
            if _alnum(res) in jdbc:
                conn.append((res, database_name(res)))
    if not conn:
        conn = [(r.name, database_name(r.name)) for r in app.resources if r.kind == "jdbc"]
    databases = _dedupe([db for _, db in conn]) or [database_name(app.id.replace(".", "_"))]
    return _Facts(
        app_id=app.id,
        app_slug=slugify(app.id),
        solution=solution,
        queues=_dedupe(queues),
        topics=_dedupe(topics),
        subscriptions=_dedupe(subs),
        inbound_queues=_dedupe(inbound),
        secret_names=secret_names,
        plain_env=dict(sorted(plain.items())),
        databases=databases,
        connection_strings=_dedupe(conn),
        ingress=any(p.starter and p.starter.kind in _API_STARTERS for p in app.processes),
    )


def _ref(dep: str, output: str) -> hcl.Expr:
    return hcl.Expr(f"dependency.{dep}.outputs.{output}")


def _app_env(f: _Facts) -> dict[str, Any]:
    """Environment of the deployed app: exactly the configuration keys it reads (see module docstring)."""
    env: dict[str, Any] = {
        "ASPNETCORE_ENVIRONMENT": "Production",
        "KeyVault__Uri": _ref("key_vault", "vault_uri"),
        "Messaging__Mode": "ServiceBus",
        "Messaging__ServiceBus__FullyQualifiedNamespace": _ref("service_bus", "fqdn"),
    }
    env.update(f.plain_env)
    return env


def _module_inputs(module: str, env: str, f: _Facts, n: _Names, p: EnvProfile) -> dict[str, Any]:
    rg = _ref("resource_group", "name")
    if module == "resource_group":
        return {"name": n.rg}
    if module == "monitoring":
        return {"log_analytics_name": n.log, "app_insights_name": n.appi, "resource_group_name": rg,
                "retention_in_days": p.log_retention_days}
    if module == "container_registry":
        return {"name": n.acr, "resource_group_name": rg, "sku": p.acr_sku}
    if module == "service_bus":
        out: dict[str, Any] = {"name": n.sb, "resource_group_name": rg, "sku": p.sb_sku}
        if p.sb_sku == "Premium":
            out["capacity"] = p.sb_capacity
        out["queues"] = [{"name": q} for q in f.queues]
        out["topics"] = [{"name": t} for t in f.topics]
        out["subscriptions"] = [{"name": s, "topic": t} for t, s in f.subscriptions]
        return out
    if module == "key_vault":
        return {"name": n.kv, "resource_group_name": rg,
                "purge_protection_enabled": p.kv_purge_protection,
                "soft_delete_retention_days": p.kv_soft_delete_days,
                "secret_names": f.secret_names}
    if module == "postgres_flexible":
        return {"name": n.psql, "resource_group_name": rg, "sku_name": p.pg_sku,
                "storage_mb": p.pg_storage_mb, "backup_retention_days": p.pg_backup_days,
                "administrator_password": hcl.Expr('get_env("PG_ADMIN_PASSWORD", "")'),
                "database_names": f.databases}
    if module == "identity":
        return {"name": n.identity, "resource_group_name": rg,
                "container_registry_id": _ref("container_registry", "id"),
                "key_vault_id": _ref("key_vault", "id"),
                "service_bus_namespace_id": _ref("service_bus", "namespace_id")}
    image = hcl.interp(
        f'${{dependency.container_registry.outputs.login_server}}/{f.app_slug}:${{get_env("IMAGE_TAG", "latest")}}'
    )
    if module == "container_app":
        # AZURE_CLIENT_ID selects the user-assigned identity for DefaultAzureCredential (Azure SDK variable).
        env_vars = {**_app_env(f), "AZURE_CLIENT_ID": _ref("identity", "client_id")}
        out = {
            "name": n.ca,
            "environment_name": n.cae,
            "resource_group_name": rg,
            "log_analytics_workspace_id": _ref("monitoring", "log_analytics_workspace_id"),
            "image": image,
            "registry_login_server": _ref("container_registry", "login_server"),
            "identity_id": _ref("identity", "id"),
            "ingress_enabled": f.ingress,
            "external_ingress": True,
            "target_port": TARGET_PORT,
            "cpu": p.cpu,
            "memory": p.memory,
            "min_replicas": max(p.min_replicas, 1 if f.inbound_queues else 0),
            "max_replicas": p.max_replicas,
            "env_vars": dict(sorted(env_vars.items())),
        }
        if f.inbound_queues:
            out["service_bus_namespace_name"] = _ref("service_bus", "namespace_name")
            out["scale_queues"] = [{"queue_name": q, "message_count": 10} for q in f.inbound_queues]
        return out
    # aks_workload: same configuration keys; the workload identity webhook injects AZURE_CLIENT_ID.
    return {
        "name": f.app_slug,
        "cluster_name": hcl.Expr(f'get_env("AKS_CLUSTER_NAME", "aks-{f.app_slug}-{env}")'),
        "cluster_resource_group_name": hcl.Expr(
            f'get_env("AKS_CLUSTER_RESOURCE_GROUP", "rg-aks-{slugify(env)}")'),
        "identity_id": _ref("identity", "id"),
        "identity_client_id": _ref("identity", "client_id"),
        "container_registry_id": _ref("container_registry", "id"),
        "namespace": f.app_slug,
        "service_account_name": f.app_slug,
        "image": image,
        "replicas": max(p.min_replicas, 1),
        "ingress_enabled": f.ingress,
        "external_ingress": True,
        "target_port": TARGET_PORT,
        "env_vars": _app_env(f),
    }


def _dependency_block(dep: str, secret_names: Sequence[str]) -> str:
    body = (
        f'dependency "{dep}" {{\n'
        f'  config_path = "../{dep}"\n\n'
        f"  mock_outputs = {hcl.value(_mock_outputs(dep, secret_names), 1)}\n\n"
        '  mock_outputs_allowed_terraform_commands = ["init", "validate", "plan", "show"]\n'
        "  mock_outputs_merge_with_state           = true\n"
        "}"
    )
    return body


# ------------------------------------------------------------------ rendering
def _jinja() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        variable_start_string="[[", variable_end_string="]]",
        block_start_string="[%", block_end_string="%]",
        comment_start_string="[#", comment_end_string="#]",
        trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True,
        undefined=StrictUndefined, autoescape=False,
    )


def _ensure_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _prune_stale(root: Path, app_slug: str, keep: set[str]) -> None:
    """Remove live units and local files an earlier run wrote for this app but this run does not.

    Covers environments dropped from ``envs`` and the module swapped out by a changed ``runtime``.
    Other applications' units and every file outside the generated set are left alone.
    """
    live = root / "infra" / "live"
    stale = [p for p in live.glob(f"*/{app_slug}/**/terragrunt.hcl")] if live.is_dir() else []
    stale.append(root / "infra" / "local" / "initdb" / "01-databases.sql")
    for path in stale:
        if path.is_file() and path.relative_to(root).as_posix() not in keep:
            path.unlink()
            for parent in path.parents:
                if parent == root or any(parent.iterdir()):
                    break
                parent.rmdir()


def generate_infra(
    app: Application,
    design: DesignRecord | Sequence[DesignRecord],
    solution_root: Path,
    solution_name: str,
    envs: Sequence[str] = DEFAULT_ENVS,
    runtime: Literal["container_app", "aks"] = "container_app",
    *,
    location: str = DEFAULT_LOCATION,
) -> list[GenFile]:
    """Generate and write the infrastructure assets under ``solution_root``.

    ``design`` is one design record or several (one per migrated process of the application);
    their messaging and configuration are merged. ``solution_name`` is the PascalCase solution
    (``GmfCredit``): paths and the image entry point follow ``dotnet_gen.generator.solution_layout``
    (``src/GmfCredit.Api/GmfCredit.Api.csproj``, ``GmfCredit.Api.dll``). When the generated solution is
    already in ``solution_root`` its Program.cs and appsettings.json decide which secrets and plain
    keys the deployment carries. Returns the files written, sorted by path; units of this application
    that an earlier run wrote for other environments or the other runtime are removed.
    """
    if runtime not in _RUNTIME_MODULE:
        raise ValueError(f"unknown runtime {runtime!r}")
    root = Path(solution_root)
    designs = _as_list(design)
    facts = _facts(app, designs, solution_name, root)
    layout = solution_layout(solution_name)
    jenv = _jinja()
    files: dict[str, str] = {}

    # Terraform modules: static, copied verbatim.
    for module in MODULES:
        for name in ("main.tf", "variables.tf", "outputs.tf"):
            src = TEMPLATES / "modules" / module / name
            files[f"infra/modules/{module}/{name}"] = src.read_text(encoding="utf-8")

    # Terragrunt live tree.
    h = short_hash(app.id)
    flat = facts.app_slug.replace("-", "")
    state = {
        "resource_group": f"rg-{facts.app_slug}-tfstate",
        "storage_account": _fit(f"st{flat}tfstate", h, 24, sep=""),
        "container": "tfstate",
    }
    files["infra/live/root.hcl"] = jenv.get_template("live/root.hcl.j2").render(
        app_id=app.id, app_slug=facts.app_slug, location=location, state=state)
    files["infra/bootstrap/tfstate.sh"] = jenv.get_template("tfstate.sh.j2").render(
        app_id=app.id, location=location, state=state)
    live_modules = (*_COMMON_MODULES, _RUNTIME_MODULE[runtime])
    unit_template = jenv.get_template("live/env_module.hcl.j2")
    env_names = _dedupe([slugify(e) for e in envs])
    for env in env_names:
        profile = _PROFILES.get(env, _PROFILES["test"])
        names = _names(facts.app_slug, env, app.id)
        for module in live_modules:
            deps = "\n\n".join(_dependency_block(d, facts.secret_names) for d in DEPENDENCIES[module])
            inputs = hcl.attrs(_module_inputs(module, env, facts, names, profile), 1, spaced=True)
            files[f"infra/live/{env}/{facts.app_slug}/{module}/terragrunt.hcl"] = unit_template.render(
                app_id=app.id, env=env, module=module, dependencies=deps, inputs=inputs)
    files["infra/.gitignore"] = (
        "# Generated by ExiQO Migrate\n.terraform/\n.terragrunt-cache/\n*.tfstate\n*.tfstate.*\n"
        "crash.log\n*.tfvars\n"
    )

    # Container image and local run.
    projects = layout["projects"]
    restore = [projects[k] for k in ("core", "infrastructure", "api")]
    files["Dockerfile"] = jenv.get_template("Dockerfile.j2").render(
        solution=solution_name, sln=layout["solution"], restore_projects=restore,
        api_csproj=layout["api_project"], api_dll=layout["api_dll"])
    files[".dockerignore"] = (TEMPLATES / "dockerignore").read_text(encoding="utf-8")
    files["docker-compose.override.yml"] = jenv.get_template("compose.override.yml.j2").render(
        solution=solution_name, databases=facts.databases, connection_strings=facts.connection_strings)
    if len(facts.databases) > 1:
        files["infra/local/initdb/01-databases.sql"] = "".join(
            f"CREATE DATABASE {db};\n" for db in facts.databases[1:])

    # Pipelines.
    pipeline_envs = []
    for i, env in enumerate(env_names):
        prev = env_names[i - 1] if i else None
        pipeline_envs.append({
            "name": env,
            "acr": _names(facts.app_slug, env, app.id).acr,
            "needs_gh": f"[apply-{prev}]" if prev else "[image]",
            "needs_ado": f"Apply_{prev}" if prev else "Image",
        })
    ctx = {
        "app_id": app.id, "app_slug": facts.app_slug, "sln": layout["solution"],
        "image_name": facts.app_slug, "envs": pipeline_envs,
        "terraform_version": TERRAFORM_VERSION, "terragrunt_version": TERRAGRUNT_VERSION,
        "tfstate_script": "infra/bootstrap/tfstate.sh",
    }
    files[f".github/workflows/{facts.app_slug}.yml"] = jenv.get_template(
        "github_workflow.yml.j2").render(**ctx)
    files["azure-pipelines.yml"] = jenv.get_template("azure-pipelines.yml.j2").render(**ctx)

    _prune_stale(root, facts.app_slug, set(files))
    out: list[GenFile] = []
    for rel in sorted(files):
        content = _ensure_newline(files[rel])
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        out.append(GenFile(path=rel, content=content))
    return out


__all__ = [
    "DEPENDENCIES",
    "MODULES",
    "GenFile",
    "database_name",
    "env_var_name",
    "generate_infra",
    "secret_name",
    "slugify",
]
