"""SQLAlchemy 2 async engine, sessions and the declarative base.

Every API table lives in the Postgres schema ``api`` so it never collides with the engine's
``gmf`` (parity seed) or ``knowledge`` schemas. Alembic (``api/alembic``) owns the schema in
deployments; tests may call :func:`reset_schema`.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, MetaData, func, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "api"

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING)


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


def fk(target: str, ondelete: str = "CASCADE", **kw: Any) -> ForeignKey:
    """Foreign key to ``<table>.<column>`` in the ``api`` schema."""
    return ForeignKey(f"{SCHEMA}.{target}", ondelete=ondelete, **kw)


class IdMixin:
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)


class CreatedMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# TLS is spelled differently by the two drivers: asyncpg (through SQLAlchemy, which passes the URL's
# query to ``asyncpg.connect`` as keyword arguments) takes ``ssl=<mode>``, libpq (psycopg) takes
# ``sslmode=<mode>`` and rejects ``ssl`` as an invalid URI query parameter. Deployments store one
# ``DATABASE_URL`` (``postgresql+asyncpg://...?ssl=require``) for both, so each conversion renames it.
SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")
_SSL_BOOL = {"true": "require", "1": "require", "on": "require", "yes": "require",
             "false": "disable", "0": "disable", "off": "disable", "no": "disable"}


def _rename_ssl(url: str, src: str, dst: str) -> str:
    """Rename query parameter ``src`` to ``dst`` (values mapped to a libpq ssl mode); the rest of
    the URL, other parameters included, is kept byte for byte."""
    base, sep, query = url.partition("?")
    if not sep:
        return url
    parts = []
    for item in query.split("&"):
        key, eq, value = item.partition("=")
        if key == src and eq:
            mode = value.lower()
            mode = _SSL_BOOL.get(mode, mode)
            if mode not in SSL_MODES:
                raise ValueError(f"database URL: {src}={value!r} is not one of {', '.join(SSL_MODES)}")
            item = f"{dst}={mode}"
        parts.append(item)
    return base + "?" + "&".join(parts)


def asyncpg_url(url: str) -> str:
    """``postgresql://`` (or ``postgres://``, ``postgresql+psycopg://``) as an asyncpg URL; a libpq
    ``sslmode`` becomes asyncpg's ``ssl``."""
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgresql+psycopg2://",
                   "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return _rename_ssl("postgresql+asyncpg://" + url[len(prefix):], "sslmode", "ssl")
    return url


def psycopg_url(url: str) -> str:
    """The same database as a plain ``postgresql://`` URL (psycopg, the knowledge store); asyncpg's
    ``ssl`` becomes libpq's ``sslmode``."""
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgresql+psycopg2://",
                   "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return _rename_ssl("postgresql://" + url[len(prefix):], "ssl", "sslmode")
    return url


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(asyncpg_url(url), pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def reset_schema(engine: AsyncEngine) -> None:
    """Drop and recreate schema ``api`` with every table (tests and throwaway databases only)."""
    from api.app import models  # noqa: F401  register every table on Base.metadata

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        await conn.run_sync(Base.metadata.create_all)
