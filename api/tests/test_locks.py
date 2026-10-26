"""Postgres advisory locks replace the in-process asyncio locks."""
from __future__ import annotations

import asyncio

import pytest

from api.app.jobs.locks import advisory_lock


async def test_advisory_lock_excludes_second_holder(app):
    order = []

    async def hold(tag, delay):
        async with advisory_lock(app.state.sessionmaker, "scenario"):
            order.append(f"{tag}-in")
            await asyncio.sleep(delay)
            order.append(f"{tag}-out")

    await asyncio.gather(hold("a", 0.3), hold("b", 0))
    assert order in (["a-in", "a-out", "b-in", "b-out"], ["b-in", "b-out", "a-in", "a-out"])


async def test_different_names_do_not_block(app):
    async with advisory_lock(app.state.sessionmaker, "analyse:p1"):
        async with asyncio.timeout(2):
            async with advisory_lock(app.state.sessionmaker, "analyse:p2"):
                pass


async def test_released_on_error_and_cancellation(app):
    sm = app.state.sessionmaker
    with pytest.raises(RuntimeError):
        async with advisory_lock(sm, "scan"):
            raise RuntimeError("boom")

    async def holder():
        async with advisory_lock(sm, "scan"):
            await asyncio.sleep(30)

    task = asyncio.create_task(holder())
    await asyncio.sleep(0.2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    async with asyncio.timeout(2):
        async with advisory_lock(sm, "scan"):
            pass


# pg_locks is cluster-wide: other checkouts run their suites against their own databases on the
# same server, and their advisory locks must not count against this one.
OWN_ADVISORY_LOCKS = ("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND database = "
                      "(SELECT oid FROM pg_database WHERE datname = current_database())")


async def test_no_advisory_lock_left_behind(app):
    from sqlalchemy import text

    sm = app.state.sessionmaker
    for _ in range(5):  # a leaked lock would sit on a pooled connection and block the next use
        async with advisory_lock(sm, "scenario"), sm() as s:
            await s.execute(text("SELECT 1"))
            await s.commit()
    async with sm() as s:
        held = await s.scalar(text(OWN_ADVISORY_LOCKS))
    assert held == 0


async def test_cancelled_waiter_leaves_no_lock(app):
    from sqlalchemy import text

    sm = app.state.sessionmaker
    release = asyncio.Event()

    async def holder():
        async with advisory_lock(sm, "scenario"):
            await release.wait()

    async def waiter():
        async with advisory_lock(sm, "scenario"):
            pass

    h = asyncio.create_task(holder())
    await asyncio.sleep(0.2)
    w = asyncio.create_task(waiter())
    await asyncio.sleep(0.2)  # blocked inside pg_advisory_lock
    w.cancel()
    await asyncio.gather(w, return_exceptions=True)
    release.set()
    await h
    await asyncio.sleep(0.2)
    async with sm() as s:
        held = await s.scalar(text(OWN_ADVISORY_LOCKS))
    assert held == 0
    async with asyncio.timeout(2):
        async with advisory_lock(sm, "scenario"):
            pass
