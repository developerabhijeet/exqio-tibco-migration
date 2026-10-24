"""Postgres advisory locks: cross-process mutual exclusion that replaces in-process asyncio locks.

Worker pods run jobs the API pods used to run, so "one scan per project", "one analysis per
process" and "one scenario-database user at a time" must hold across processes. A lock here is a
session-level ``pg_advisory_lock(hashtext(name))`` on a dedicated connection held for the block;
it is released in ``finally`` (also on cancellation), and a connection whose unlock failed is
invalidated so a held lock never returns to the pool.

Lock names: ``scan``, ``analyse:<process_id>``, ``scenario``.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

log = logging.getLogger(__name__)

# Services take this instead of an ``asyncio.Lock``: call it for each ``async with lock():``.
ScenarioLock = Callable[[], AbstractAsyncContextManager[None]]


@asynccontextmanager
async def advisory_lock(sessionmaker: async_sessionmaker[AsyncSession], name: str) -> AsyncIterator[None]:
    """Hold the named advisory lock for the block; waits while another holder has it."""
    engine: AsyncEngine = sessionmaker.kw["bind"]
    # AUTOCOMMIT keeps this one pooled connection checked out and transaction-free for the block,
    # so the unlock runs on the connection that holds the lock.
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("SELECT pg_advisory_lock(hashtext(:n))"), {"n": name})
        try:
            yield
        finally:
            try:
                await asyncio.shield(conn.execute(text("SELECT pg_advisory_unlock(hashtext(:n))"), {"n": name}))
            except BaseException:
                log.exception("advisory lock %r: unlock failed, discarding the connection", name)
                await asyncio.shield(conn.invalidate())


def scenario_lock(sessionmaker: async_sessionmaker[AsyncSession]) -> ScenarioLock:
    """The ``scenario`` lock as a callable the services use as ``async with lock():``."""
    return lambda: advisory_lock(sessionmaker, "scenario")
