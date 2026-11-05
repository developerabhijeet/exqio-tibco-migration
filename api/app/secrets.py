"""File-mounted secrets: ``<NAME>_FILE`` names a file whose content is the value of ``<NAME>``.

Kubernetes and the Key Vault CSI driver mount secrets as files, so a pod spec never carries the
value. The source applies to the listed secret fields only, and a file beats a plain variable.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

SECRET_FILE_FIELDS = (
    "database_url",
    "test_database_url",
    "app_secret",
    "neo4j_password",
    "azure_openai_api_key",
    "claude_code_oauth_token",
    "oidc_client_secret",
    "session_secret",
    "internal_token",
    "langfuse_secret_key",
    "github_token",
    "atlassian_token",
    "atlassian_user",
    "figma_token",
)


def read_secret_file(path: str) -> str:
    """The file's content with one trailing newline removed (editors and ``echo`` add it)."""
    text = Path(path).read_text(encoding="utf-8")
    return text.removesuffix("\n")


class FileSecretsSource(PydanticBaseSettingsSource):
    """Reads ``<FIELD>_FILE`` from the environment for each secret field."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        var = f"{field_name.upper()}_FILE"
        path = os.environ.get(var, "")
        if not path:
            return None, field_name, False
        if not Path(path).is_file():
            raise ValueError(f"{var} points at {path}, which does not exist")
        return read_secret_file(path), field_name, False

    def __call__(self) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for name in SECRET_FILE_FIELDS:
            value, key, _ = self.get_field_value(self.settings_cls.model_fields[name], name)
            if value is not None:
                found[key] = value
        return found
