"""Settings: install fields and ``<NAME>_FILE`` secrets (mounted files win over plain env)."""
from __future__ import annotations

import pytest

from api.app.config import Settings
from api.app.secrets import read_secret_file


def test_file_secret_wins_over_env(tmp_path, monkeypatch):
    f = tmp_path / "db"
    f.write_text("postgresql+asyncpg://u:p@h/db\n")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://env/db")
    monkeypatch.setenv("DATABASE_URL_FILE", str(f))
    assert Settings(_env_file=None).database_url == "postgresql+asyncpg://u:p@h/db"


def test_missing_secret_file_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_SECRET_FILE", str(tmp_path / "absent"))
    with pytest.raises(ValueError, match="APP_SECRET_FILE"):
        Settings(_env_file=None)


def test_deploy_environments_parse(monkeypatch):
    monkeypatch.setenv(
        "DEPLOY_ENVIRONMENTS",
        '[{"name":"dev","tenant":"t","subscription":"s","client_id":"c",'
        '"auth":"workload_identity"}]',
    )
    assert Settings(_env_file=None).deploy_environments[0].auth == "workload_identity"


def test_role_groups_parse(monkeypatch):
    monkeypatch.setenv("ROLE_GROUPS", '{"engineer":["11111111-1111-1111-1111-111111111111"]}')
    got = Settings(_env_file=None).role_groups["engineer"]
    assert got == ["11111111-1111-1111-1111-111111111111"]


def test_defaults_keep_local_dev(monkeypatch):
    for key in ("EXIQO_ROLE", "AUTH_MODE", "DEPLOY_CREDENTIAL_MODE"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)
    assert (s.exiqo_role, s.auth_mode, s.deploy_credential_mode) == ("all", "dev", "local")
    assert (s.internal_port, s.job_stale_seconds, s.job_heartbeat_seconds) == (9100, 90, 10)
    assert (s.job_max_attempts, s.brand_name, s.neo4j_user) == (2, "ExiQO", "neo4j")


def test_every_listed_secret_reads_its_file(tmp_path, monkeypatch):
    names = [
        "database_url", "test_database_url", "app_secret", "neo4j_password",
        "azure_openai_api_key", "claude_code_oauth_token", "oidc_client_secret",
        "session_secret", "internal_token", "langfuse_secret_key",
    ]
    for name in names:
        f = tmp_path / name
        f.write_text(f"value-of-{name}\n")
        monkeypatch.setenv(f"{name.upper()}_FILE", str(f))
    s = Settings(_env_file=None)
    for name in names:
        assert getattr(s, name) == f"value-of-{name}"


def test_file_source_ignores_other_fields(tmp_path, monkeypatch):
    f = tmp_path / "x"
    f.write_text("nope")
    monkeypatch.setenv("BRAND_NAME_FILE", str(f))
    assert Settings(_env_file=None).brand_name == "ExiQO"


def test_empty_file_var_is_unset(monkeypatch):
    monkeypatch.setenv("APP_SECRET_FILE", "")
    monkeypatch.setenv("APP_SECRET", "plain")
    assert Settings(_env_file=None).app_secret == "plain"


def test_read_secret_file_strips_one_trailing_newline(tmp_path):
    f = tmp_path / "s"
    f.write_text("abc\n\n")
    assert read_secret_file(str(f)) == "abc\n"
    f.write_text("abc")
    assert read_secret_file(str(f)) == "abc"
