"""Alembic environment for the API tables: async engine, everything in schema ``api``.

The URL is ``DATABASE_URL`` (via ``api.app.config.Settings``), converted to asyncpg. The schema
is created before the version table, which also lives in ``api``.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Connection

from api.app import models  # noqa: F401  register every table
from api.app.config import Settings
from api.app.db import SCHEMA, Base, make_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or Settings().database_url


def _include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    if type_ == "schema":
        return name == SCHEMA
    return True


def _configure(connection: Connection | None = None, **kw) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, include_schemas=True,
                      include_name=_include_name, version_table_schema=SCHEMA, compare_type=True, **kw)


def run_migrations_offline() -> None:
    _configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    connection.commit()
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = make_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
