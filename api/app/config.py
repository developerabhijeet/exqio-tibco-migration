"""API settings, read from the environment (and ``.env`` when present)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # Observability
    langfuse_enabled: bool = False
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Runtime
    job_workers: int = 2
    cors_origins: str = "http://localhost:3000"  # comma separated
    upload_max_bytes: int = 2 * 1024 * 1024 * 1024  # total uncompressed size of one upload

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir.resolve() / "uploads"

    def resolved_provider(self) -> ResolvedProvider:
        """The provider the API will use: an explicit choice, or the ``auto`` fallback chain."""
        if self.provider != "auto":
            return self.provider
        if self.claude_code_oauth_token:
            return "claude"
        if self.azure_openai_endpoint and self.azure_openai_api_key:
            return "azure"
        return "none"
