"""Memory administration: ``python -m api.app.memory_admin <command>``.

- ``migrate``: queue the one-time ``memory.migrate`` job (the Postgres ``knowledge`` schema into
  Neo4j, then retired; ``services.memory_migrate``).
- ``reembed``: queue ``memory.reembed``, which re-embeds every project's memory with the active
  embedding backend and then records that backend (after the backend changed).
  ``--project <id>`` only refreshes one project's vectors under the backend the graph already
  records (refused while it records another).
- ``reingest --project <id>``: queue one ``memory.ingest`` per scope of the project (estate; per
  process analysis, design, latest build, verify, deploy and each gate; every ``memory_events``
  row; seed), each with ingest key ``reingest:<uuid>:<scope>``.

``migrate`` and ``reembed`` take ``--wait`` to follow the job to its end, at most ``--timeout``
seconds (default 1800). Exit status: 0 done (or queued), 1 the job failed, 2 a usage error
(``NEO4J_URI`` empty, unknown project), 3 the wait timed out. The workers run the jobs.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.app.config import Settings
from api.app.db import make_engine, make_sessionmaker
from api.app.jobs import handlers  # noqa: F401  registers the job kinds
from api.app.jobs.runner import enqueue
from api.app.models import Build, Gate, Job, MemoryEvent, Plan, Process, Project
from api.app.services import evidence as evidence_svc
from api.app.services.memory_ingest import enqueue_ingest
from api.app.services.memory_migrate import MIGRATE_KIND, REEMBED_KIND

ADMIN_ACTOR = "memory_admin"
POLL_S = 1.0
WAIT_TIMEOUT_S = 1800.0


class UnknownProject(ValueError):
    pass


def _key(scope: str) -> str:
    return f"reingest:{uuid.uuid4().hex}:{scope}"


async def reingest(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings,
                   project_id: str) -> list[str]:
    """Queue one ingest per scope of the project; the ids of the jobs queued (a scope whose
    ingest is already queued is covered by it)."""
    queued: list[str] = []

    async def one(phase: str, scope: str, source: dict[str, Any]) -> None:
        job_id = await enqueue_ingest(sessionmaker, settings, project_id=project_id, phase=phase,  # type: ignore[arg-type]
                                      scope=scope, source=source, actor=ADMIN_ACTOR,
                                      ingest_key=_key(scope))
        if job_id is not None:
            queued.append(job_id)

    async with sessionmaker() as s:
        if await s.get(Project, project_id) is None:
            raise UnknownProject(f"project {project_id!r} not found")
        procs = (await s.execute(select(Process).where(Process.project_id == project_id)
                                 .order_by(Process.ir_id))).scalars().all()
        plan: list[tuple[str, str, dict[str, Any]]] = [("discover", "estate", {})]
        for p in procs:
            src = {"process_id": p.id}
            plan += [("analysis", f"analysis:{p.id}", src), ("design", f"design:{p.id}", src)]
            build = await evidence_svc.latest_build(s, p.id)
            if build is not None:
                plan += [("build", f"build:{p.id}", {**src, "build_id": build.id}),
                         ("verify", f"verify:{p.id}", {**src, "build_id": build.id})]
            plan_id = await s.scalar(select(Plan.id).join(Build, Build.id == Plan.build_id)
                                     .where(Build.process_id == p.id)
                                     .order_by(Plan.created_at.desc(), Plan.id.desc()).limit(1))
            if plan_id is not None:
                plan.append(("deploy", f"deploy:{p.id}", {**src, "plan_id": plan_id}))
            for gate in (await s.execute(select(Gate.gate).where(Gate.process_id == p.id)
                                         .order_by(Gate.gate))).scalars():
                plan.append(("gate", f"gate:{p.ir_id}:{gate}", {**src, "gate": gate}))  # R29: IR id
        events = (await s.execute(select(MemoryEvent).where(MemoryEvent.project_id == project_id)
                                  .order_by(MemoryEvent.created_at, MemoryEvent.id))).scalars().all()
        for e in events:
            source = {"event_id": e.id}
            if isinstance((e.context or {}).get("process_id"), str):
                source["process_id"] = e.context["process_id"]
            plan.append((e.kind, f"{e.kind}:{e.id}", source))
    plan.append(("seed", "seed", {}))
    for phase, scope, source in plan:
        await one(phase, scope, source)
    return queued


async def _wait(sessionmaker: async_sessionmaker[AsyncSession], job_id: str, timeout: float) -> Job:
    """The job once it is ``done`` or ``failed``; ``TimeoutError`` after ``timeout`` seconds."""
    async with asyncio.timeout(timeout):
        return await _poll(sessionmaker, job_id)


async def _poll(sessionmaker: async_sessionmaker[AsyncSession], job_id: str) -> Job:
    while True:
        async with sessionmaker() as s:
            job = await s.get(Job, job_id)
        if job is not None and job.status in ("done", "failed"):
            return job
        await asyncio.sleep(POLL_S)


async def _queued_job(sessionmaker: async_sessionmaker[AsyncSession], kind: str, key: str) -> str | None:
    async with sessionmaker() as s:
        return await s.scalar(select(Job.id).where(Job.kind == kind, Job.status == "queued",
                                                   Job.payload["dedupe_key"].astext == key).limit(1))


async def run(args: argparse.Namespace, settings: Settings) -> int:
    if not settings.neo4j_uri:
        print("memory is not configured: set NEO4J_URI", file=sys.stderr)
        return 2
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    try:
        if args.command == "reingest":
            try:
                ids = await reingest(sessionmaker, settings, args.project)
            except UnknownProject as exc:
                print(str(exc), file=sys.stderr)
                return 2
            print(f"queued {len(ids)} memory ingests for project {args.project}")
            return 0
        project_id = getattr(args, "project", None)
        if args.command == "migrate":
            kind, payload, key = MIGRATE_KIND, {}, MIGRATE_KIND
        else:
            if project_id:
                async with sessionmaker() as s:
                    if await s.get(Project, project_id) is None:
                        print(f"project {project_id!r} not found", file=sys.stderr)
                        return 2
            kind, payload = REEMBED_KIND, ({"project_id": project_id} if project_id else {})
            key = f"{REEMBED_KIND}|{project_id or '*'}"
        job_id = await enqueue(sessionmaker, kind, payload, project_id=project_id, dedupe_key=key)
        if job_id is None:
            job_id = await _queued_job(sessionmaker, kind, key)
            print(f"{kind} is already queued: job {job_id}")
        else:
            print(f"queued {kind}: job {job_id}")
        if not args.wait or job_id is None:
            return 0
        try:
            job = await _wait(sessionmaker, job_id, args.timeout)
        except TimeoutError:
            print(f"{kind} job {job_id} still running after {args.timeout:g} s", file=sys.stderr)
            return 3
        print(f"{kind} {job.status}: {job.result if job.status == 'done' else job.error}")
        return 0 if job.status == "done" else 1
    finally:
        await engine.dispose()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m api.app.memory_admin",
                                description="Memory (Neo4j) administration.")
    sub = p.add_subparsers(dest="command", required=True)
    m = sub.add_parser("migrate", help="copy the Postgres knowledge schema into Neo4j, then retire it")
    r = sub.add_parser("reembed", help="re-embed every project's memory with the active backend")
    r.add_argument("--project", help="only refresh this project's vectors (backend unchanged)")
    for cmd in (m, r):
        cmd.add_argument("--wait", action="store_true", help="follow the job to its end")
        cmd.add_argument("--timeout", type=float, default=WAIT_TIMEOUT_S,
                         help="seconds --wait waits at most (default %(default)g)")
    i = sub.add_parser("reingest", help="queue an ingest of every scope of a project")
    i.add_argument("--project", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parser().parse_args(argv), Settings()))


if __name__ == "__main__":
    raise SystemExit(main())
