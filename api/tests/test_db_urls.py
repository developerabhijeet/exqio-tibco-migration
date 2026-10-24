"""One ``DATABASE_URL`` for both drivers: asyncpg's ``ssl`` and libpq's ``sslmode`` are translated.

Deployments store ``postgresql+asyncpg://...?ssl=require``; libpq rejects ``ssl`` ("invalid URI
query parameter"), which once broke the heartbeat thread, the knowledge store and the scenario
database. The local Postgres has no TLS, so the end-to-end checks use ``ssl=disable``.
"""
from __future__ import annotations

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from api.app.db import asyncpg_url, make_engine, psycopg_url
from api.app.jobs.runner import HEARTBEAT_CONNECT
from api.app.services.knowledge import Knowledge
from api.tests.conftest import TEST_DSN

AZ = "postgresql+asyncpg://exiqoadmin:p%40ss@gmf-exiqo-pg.postgres.database.azure.com:5432/exiqo"


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"])
def test_psycopg_url_translates_every_asyncpg_ssl_mode(mode):
    assert psycopg_url(f"{AZ}?ssl={mode}") == (
        f"postgresql://exiqoadmin:p%40ss@gmf-exiqo-pg.postgres.database.azure.com:5432/exiqo"
        f"?sslmode={mode}")


@pytest.mark.parametrize(("value", "mode"), [("true", "require"), ("True", "require"),
                                              ("false", "disable"), ("REQUIRE", "require")])
def test_psycopg_url_maps_boolean_ssl(value, mode):
    assert psycopg_url(f"{AZ}?ssl={value}").endswith(f"?sslmode={mode}")


def test_psycopg_url_keeps_other_parameters_and_plain_urls():
    assert psycopg_url(f"{AZ}?application_name=exiqo&ssl=require&connect_timeout=5").endswith(
        "/exiqo?application_name=exiqo&sslmode=require&connect_timeout=5")
    assert psycopg_url("postgresql://u:p@h/db") == "postgresql://u:p@h/db"
    assert psycopg_url("postgres://u:p@h/db?sslmode=require") == "postgresql://u:p@h/db?sslmode=require"


def test_psycopg_url_rejects_an_unknown_ssl_mode():
    with pytest.raises(ValueError, match="ssl='strict'"):
        psycopg_url(f"{AZ}?ssl=strict")


def test_libpq_accepts_the_terraform_url():
    """The exact shape deploy/azure writes into Key Vault parses as a libpq conninfo."""
    info = conninfo_to_dict(psycopg_url(f"{AZ}?ssl=require"))
    assert info["sslmode"] == "require" and info["dbname"] == "exiqo" and info["password"] == "p@ss"
    assert "ssl" not in info


def test_asyncpg_url_translates_libpq_sslmode():
    assert asyncpg_url("postgresql://u:p@h:5432/db?sslmode=verify-full&x=1") == (
        "postgresql+asyncpg://u:p@h:5432/db?ssl=verify-full&x=1")
    assert asyncpg_url(f"{AZ}?ssl=require") == f"{AZ}?ssl=require"  # already asyncpg
    assert psycopg_url(asyncpg_url("postgresql://u:p@h/db?sslmode=prefer")) == (
        "postgresql://u:p@h/db?sslmode=prefer")


def _with(dsn: str, param: str) -> str:
    return dsn + ("&" if "?" in dsn else "?") + param


def _require_db() -> None:
    try:
        psycopg.connect(TEST_DSN, connect_timeout=3).close()
    except psycopg.Error as exc:
        pytest.skip(f"Postgres is not available: {exc}")


def test_psycopg_connects_with_an_asyncpg_ssl_url():
    """The heartbeat thread's connection (same keyword arguments) from an asyncpg URL with ``ssl``."""
    _require_db()
    url = psycopg_url(_with(asyncpg_url(TEST_DSN), "ssl=disable"))
    with psycopg.connect(url, **HEARTBEAT_CONNECT) as conn:
        assert conn.execute("SHOW statement_timeout").fetchone() == ("5s",)
        assert conn.execute("SELECT 1").fetchone() == (1,)


def test_knowledge_store_connects_with_an_asyncpg_ssl_url():
    _require_db()
    knowledge = Knowledge(psycopg_url(_with(asyncpg_url(TEST_DSN), "ssl=prefer")))
    knowledge._call(lambda store: store.connection().execute("SELECT 1").fetchone())


async def test_asyncpg_engine_connects_with_a_libpq_sslmode_url():
    _require_db()
    from sqlalchemy import text
    engine = make_engine(asyncpg_url(_with(TEST_DSN, "sslmode=disable")))
    try:
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar() == 1
    finally:
        await engine.dispose()


def test_scenario_dsn_keeps_tls_from_an_asyncpg_url(tmp_path):
    from api.app.config import Settings
    from api.app.services.verify import scenario_dsn
    settings = Settings(database_url=f"{AZ}?ssl=require", data_dir=tmp_path, _env_file=None)
    info = conninfo_to_dict(scenario_dsn(settings, "p1"))
    assert (info["dbname"], info["sslmode"]) == ("exiqo_scn_p1_test", "require")
