"""One ``LISTEN job_events`` connection per API process, fanned out to SSE subscribers.

Jobs run in worker processes (or the in-process worker in role ``all``), so an API replica cannot
hear a job's events in memory. Every event insert sends ``pg_notify('job_events', '<job_id>:<seq>')``
in the same transaction; this listener receives them and hands the ``seq`` to the queues of that
job's subscribers, which read the events back from ``job_events`` in order.

The connection is plain asyncpg (SQLAlchemy has no LISTEN API) and reconnects with backoff when it
drops. Notifications sent while it is down are lost, which is why the SSE stream keeps its 500 ms
database poll: a dropped listener makes streams slower, never wrong.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncpg

log = logging.getLogger(__name__)

CHANNEL = "job_events"
BACKOFF_MAX_SECONDS = 10.0
PING_SECONDS = 30.0


class JobListener:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn  # a plain postgresql:// URL (api.app.db.psycopg_url)
        self._subscribers: dict[str, set[asyncio.Queue[int]]] = {}
        self._conn: asyncpg.Connection | None = None
        self._task: asyncio.Task[None] | None = None
        self._dropped = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._conn is not None and not self._conn.is_closed()

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Connect now if the database is there (so the first streams get notifications), then
        keep the connection up in the background; never raises for a missing database."""
        try:
            await self._connect()
        except Exception as exc:  # noqa: BLE001  the background loop keeps trying
            log.warning("job listener: not connected yet (%s); retrying in the background", exc)
        self._task = asyncio.create_task(self._keep_connected(), name="job-listener")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self._close()

    async def _connect(self) -> None:
        conn = await asyncpg.connect(self.dsn)
        try:
            await conn.add_listener(CHANNEL, self._on_notify)
        except BaseException:
            await conn.close()
            raise
        self._dropped.clear()
        conn.add_termination_listener(lambda _c: self._dropped.set())
        self._conn = conn

    async def _close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None and not conn.is_closed():
            try:
                await conn.close(timeout=2)
            except Exception:  # noqa: BLE001  closing a broken connection
                conn.terminate()

    async def _keep_connected(self) -> None:
        backoff = 0.5
        while True:
            if self.connected:
                await self._wait_dropped()
                log.warning("job listener: connection lost; reconnecting")
                await self._close()
                backoff = 0.5
            try:
                await self._connect()
            except Exception as exc:  # noqa: BLE001
                log.warning("job listener: reconnect failed (%s); next try in %.1f s", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)

    async def _wait_dropped(self) -> None:
        """Return once the connection is gone: closed by the server, or (a silently dead TCP
        connection never reports closing) failing a ping every ``PING_SECONDS``."""
        while True:
            try:
                await asyncio.wait_for(self._dropped.wait(), timeout=PING_SECONDS)
                return
            except TimeoutError:
                pass
            conn = self._conn
            if conn is None:
                return
            try:
                await conn.execute("SELECT 1", timeout=5)
            except Exception:  # noqa: BLE001
                return

    # ------------------------------------------------------------- pub/sub
    def _on_notify(self, _conn: Any, _pid: int, _channel: str, payload: str) -> None:
        job_id, _, seq = payload.rpartition(":")
        try:
            n = int(seq)
        except ValueError:
            log.warning("job listener: ignoring malformed payload %r", payload)
            return
        for q in list(self._subscribers.get(job_id, ())):
            q.put_nowait(n)

    def subscribe(self, job_id: str) -> asyncio.Queue[int]:
        """A queue that receives the ``seq`` of each new event of ``job_id``."""
        q: asyncio.Queue[int] = asyncio.Queue()
        self._subscribers.setdefault(job_id, set()).add(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue[int]) -> None:
        subs = self._subscribers.get(job_id)
        if subs is not None:
            subs.discard(q)
            if not subs:
                self._subscribers.pop(job_id, None)
