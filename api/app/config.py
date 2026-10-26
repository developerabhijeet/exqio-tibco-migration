"""API settings, read from the environment (and ``.env`` when present)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .secrets import FileSecretsSource

REPO_ROOT = Path(__file__).resolve().parents[2]

ProviderChoice = Literal["auto", "fake", "claude", "azure"]
ResolvedProvider = Literal["fake", "claude", "azure", "none"]


class DeployEnvironment(BaseModel):
    """One Azure target the Deploy step may reach (no secret value: only where it lives)."""

    name: str
    tenant: str
    subscription: str
    client_id: str
    auth: Literal["workload_identity", "keyvault_sp"]
    key_vault_uri: str = ""
    secret_name: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # ``<NAME>_FILE`` (a mounted secret) beats every other source for the secret fields.
        return (
            FileSecretsSource(settings_cls),
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    # LLM provider: auto picks Claude when its token is present, else Azure, else none (NullProvider)
    provider: ProviderChoice = "auto"
    claude_code_oauth_token: str = ""
    model_strong: str = "claude-opus-5-5"
    model_standard: str = "claude-sonnet-5-5"
    model_efficient: str = "claude-haiku-4-5"
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = ""
    azure_openai_deployment_strong: str = ""
    azure_openai_deployment_standard: str = ""
    azure_openai_deployment_efficient: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://exiqo:exiqo@localhost:5432/exiqo"
    test_database_url: str = "postgresql://exiqo:exiqo@localhost:5432/exiqo_test"

    # Storage and secrets
    data_dir: Path = Path("./data")
    app_secret: str = ""  # Fernet key for stored Azure credentials
    # Parity scenario database. Unset: one database per project, ``exiqo_scn_<project>_test`` on the
    # DATABASE_URL server (created when missing). Set: every project uses this one (its name must
    # end with ``_test``: its ``gmf`` schema is dropped and reseeded).
    scenario_database_url: str = ""

    # Routing: inclusive complexity-score bands per tier (Settings screen); unset uses the defaults
    # strong 8 to 10, standard 4 to 7, efficient 0 to 3.
    routing_bands: dict[str, tuple[int, int]] | None = None

    # Observability
    langfuse_enabled: bool = False
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Install: which process this is, how callers sign in, and where jobs and the graph live.
    # The defaults are local development: one process, dev personas, local credentials.
    exiqo_role: Literal["api", "worker", "all"] = "all"
    auth_mode: Literal["dev", "oidc"] = "dev"
    deploy_credential_mode: Literal["workload_identity", "local"] = "local"
    api_base_path: str = ""
    nuget_source: str = ""  # GMF NuGet mirror URL for generated solutions; empty = NuGet's own configuration
    tf_cli_config_file: str = ""  # Terraform CLI config (provider network mirror) for the validators
    # terraform init in the infra validation downloads providers. False skips init and module validate
    # (recorded as skipped, never as passed): for e2e and machines with no registry or mirror.
    infra_validate_init: bool = True
    terraform_init_timeout_s: int = 300
    internal_token: str = ""  # bearer for the worker's cluster-internal listener
    internal_port: int = 9100
    worker_host: str = ""  # pod IP the worker advertises; empty means 127.0.0.1
    job_stale_seconds: int = 90
    job_heartbeat_seconds: int = 10
    job_max_attempts: int = 2
    job_drain_seconds: int = 570  # on SIGTERM; inside the worker's 600 s termination grace period
    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    neo4j_ready_grace_seconds: int = 900
    neo4j_gates_readiness: bool = True
    memory_ingest_max_attempts: int = 10
    memory_exact_scan_max: int = 50000
    brand_name: str = "ExiQO"
    support_contact: str = ""
    azure_openai_auth: Literal["key", "managed_identity"] = "key"
    oidc_tenant_id: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = ""
    session_secret: str = ""
    # role key -> Entra group object ids and/or app role values (JSON); see api/app/auth/roles.py
    role_groups: dict[str, list[str]] = Field(default_factory=dict)
    deploy_environments: list[DeployEnvironment] = Field(default_factory=list)  # JSON
    image_digests: dict[str, str] = Field(default_factory=dict)
    egress_allowlist: list[str] = Field(default_factory=list)

    # Build toolchain. Off skips restore, build, format and test, and with them the compile repair
    # loop: the solution is still generated, filled, reviewed and stored, and the three toolchain
    # steps are reported as not run with that reason. For iterating on the generator or on the
    # screens without paying for a repair loop on every run. G4 still refuses a build whose compile
    # was never verified, because it was never verified.
    build_toolchain: bool = True

    # Runtime
    job_workers: int = 2
    cors_origins: str = "http://localhost:3000"  # comma separated
    upload_max_bytes: int = 2 * 1024 * 1024 * 1024  # total uncompressed size of one upload
    # Server folders a JSON {"path"} upload may read (comma separated or a JSON list);
    # unset means DATA_DIR and <repo>/samples.
    upload_allowed_roots: Annotated[list[Path] | None, NoDecode] = None

    @field_validator("upload_allowed_roots", mode="before")
    @classmethod
    def _split_roots(cls, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.startswith("["):
                return json.loads(text)
            return [part.strip() for part in text.split(",") if part.strip()]
        return value

    @property
    def builds_dir(self) -> Path:
        return self.data_dir.resolve() / "builds"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir.resolve() / "uploads"

    @property
    def allowed_upload_roots(self) -> list[Path]:
        roots = self.upload_allowed_roots
        if roots is None:
            roots = [self.data_dir, REPO_ROOT / "samples"]
        return [Path(r).expanduser().resolve() for r in roots]

    def resolved_provider(self) -> ResolvedProvider:
        """The provider the API will use: an explicit choice, or the ``auto`` fallback chain."""
        if self.provider != "auto":
            return self.provider
        if self.claude_code_oauth_token:
            return "claude"
        if self.azure_openai_configured():
            return "azure"
        return "none"

    def azure_openai_configured(self) -> bool:
        """An endpoint and a way to sign in: the key, or the pod's managed identity."""
        return bool(self.azure_openai_endpoint
                    and (self.azure_openai_api_key or self.azure_openai_auth == "managed_identity"))
