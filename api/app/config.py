"""API settings, read from the environment (and ``.env`` when present)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

ProviderChoice = Literal["auto", "fake", "claude", "azure"]
ResolvedProvider = Literal["fake", "claude", "azure", "none"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
        if self.azure_openai_endpoint and self.azure_openai_api_key:
            return "azure"
        return "none"
