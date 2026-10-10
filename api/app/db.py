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


def asyncpg_url(url: str) -> str:
    """``postgresql://`` (or ``postgres://``, ``postgresql+psycopg://``) as an asyncpg URL."""
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgresql+psycopg2://",
                   "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
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
