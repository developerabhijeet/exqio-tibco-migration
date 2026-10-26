"""The ``connector.github.push`` job: push a build to an ``exiqo/`` branch and open (or update) its
pull request; the branch and the pull request are recorded as links of the process and audited.

Payload ``{build_id, repo, branch, open_pr, base, actor}`` (no credential: the token is read from
its mounted file when the job runs); result ``{branch, commit, pull_request}`` (plus ``message`` when no pull request
could be opened: the repository was empty).

One push of a build to a branch at a time: the route queues it once (``dedupe_key``) and refuses
it while it runs, and the job holds the advisory lock ``github:<build>:<repo>:<branch>`` around the
push and its link upserts (a second job waits, then runs after the first).
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from api.app.connectors.audit import audit_connector
from api.app.connectors.base import ConnectorDisabled, ConnectorNotConfigured
from api.app.connectors.gitcli import auth_header_value
from api.app.connectors.github import GitHubConnector
from api.app.connectors.http import header_secrets, redact
from api.app.connectors.links import upsert_link
from api.app.connectors.registry import connector_for
from api.app.jobs.locks import try_advisory_lock
from api.app.jobs.registry import RetryLater
from api.app.models import Build, Process
from api.app.services.build_tree import DOWNLOAD_EXCLUDED, export_note

if TYPE_CHECKING:
    from api.app.jobs.registry import WorkerServices
    from api.app.jobs.runner import JobContext

PUSH_KIND = "connector.github.push"
EXPORT_NOTE = "EXPORTED_FROM.md"
LOCK_PREFIX = "github:"
LOCK_BUSY_DELAY_S = 2.0


def dedupe_key(build_id: str, repo: str, branch: str) -> str:
    """The queue's dedupe key and the job's advisory lock name: one push per build and branch."""
    return f"{LOCK_PREFIX}{build_id}:{repo}:{branch}"


def _pr_body(build: Build, proc: Process, commit: str, brand: str) -> str:
    return (f"{brand} Migrate build `{build.id}` of the TIBCO process **{proc.name}** "
            f"(`{proc.source_path}`).\n\n"
            f"- Commit: `{commit}`\n- Build status: {build.status}\n"
            f"- Build hash: `{build.build_hash or 'not recorded'}`\n\n"
            "Provenance is in `EXPORTED_FROM.md`. Approval is the G4 gate record in "
            f"{brand} Migrate, not this pull request; review and merge it as any other change.\n")


class Refused(Exception):
    """The job cannot run as asked and nothing was sent: audited ``refused``."""


def _secrets(gh: GitHubConnector | None) -> list[str]:
    """The token's forms, to redact from an audit message (none when it cannot be read now)."""
    if gh is None:
        return []
    try:
        token = gh.credential()
    except ConnectorNotConfigured:
        return []
    return [*header_secrets({"a": f"Bearer {token}"}), *header_secrets({"a": auth_header_value(token)})]


async def push_job(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    """One push of a build to a branch at a time (advisory lock): a second job waits for the first."""
    p = ctx.payload
    async with try_advisory_lock(w.sessionmaker, dedupe_key(p["build_id"], p["repo"], p["branch"])) as locked:
        if not locked:
            raise RetryLater(LOCK_BUSY_DELAY_S, "another push of this build to this branch is running",
                             count_attempt=False)
        return await _push(ctx, w)


async def _push(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    """Every outcome is audited: ``refused`` (off, no credential, nothing to push), ``failed``
    (any error, its redacted text) or ``ok`` per step; an error is then raised to fail the job."""
    p = ctx.payload
    repo, branch, actor = p["repo"], p["branch"], p.get("actor") or "system"
    target = f"{repo}@{branch}"
    state: dict[str, Any] = {"op": "push", "where": {"project_id": None, "process_id": None}, "gh": None}

    async def audit(outcome: Literal["ok", "failed", "refused"], audit_target: str, detail: dict[str, Any],
                    session: AsyncSession | None = None) -> None:
        async def write(s: AsyncSession) -> None:
            await audit_connector(s, actor, "github", state["op"], target=audit_target, outcome=outcome,
                                  detail={"build_id": p["build_id"], **detail}, **state["where"])

        if session is not None:
            await write(session)
            return
        async with w.sessionmaker() as s:
            await write(s)
            await s.commit()

    try:
        async with w.sessionmaker() as s:
            build = await s.get(Build, p["build_id"])
            if build is None:
                raise Refused(f"build {p['build_id']} not found")
            proc = await s.get(Process, build.process_id)
            if proc is None:
                raise Refused(f"the process of build {build.id} is gone")
            state["where"] = {"project_id": proc.project_id, "process_id": proc.id}
            gh = await connector_for(s, w.settings, "github")
        if not isinstance(gh, GitHubConnector):
            raise TypeError(f"the GitHub connector is served by {type(gh).__name__}")
        state["gh"] = gh
        gh.ready()  # off or no credential since the request was accepted: before any request
        if not build.output_dir or not Path(build.output_dir).is_dir():
            raise Refused("the build has no solution on disk")
        where = state["where"]

        await ctx.stage("push")
        base, base_exists = await gh.base_of(repo, p.get("base"))
        if base_exists:
            await ctx.log(f"pushing build {build.id[:8]} to {repo} branch {branch} (on top of {base})")
        else:
            await ctx.log(f"{repo} is empty: build {build.id[:8]} becomes its first commit, on {branch}")
        commit = await gh.push_build(
            Path(build.output_dir), repo, branch,
            f"{proc.name}: build {build.id[:8]} from {w.settings.brand_name} Migrate",
            base_branch=base if base_exists else None,
            extra_files={EXPORT_NOTE: export_note(build, proc).encode()},
            excluded=DOWNLOAD_EXCLUDED)  # SP5: the stack's download_excluded
        await ctx.log(f"pushed commit {commit[:12]} to {branch}")
        async with w.sessionmaker() as s:
            await upsert_link(s, **where, kind="github", object_type="branch", local_ref=build.id,
                              remote_id=branch, remote_url=f"{gh.base_url}/{repo}/tree/{branch}",
                              created_by=actor)
            await audit("ok", target, {"repo": repo, "branch": branch, "base": base if base_exists else None,
                                       "commit": commit}, s)
            await s.commit()

        out: dict[str, Any] = {"branch": branch, "commit": commit, "pull_request": None}
        if not p.get("open_pr", True):
            return out
        state["op"] = "pull-request"
        if not base_exists:
            out["message"] = (f"no pull request: {repo} had no {base} branch to merge into; "
                              f"{branch} is its first commit")
            await ctx.log(out["message"])
            await audit("refused", target, {"message": out["message"]})
            return out
        await ctx.stage("pull-request")
        pull = await gh.open_pull_request(repo, branch, base, f"{proc.name}: build {build.id[:8]}",
                                          _pr_body(build, proc, commit, w.settings.brand_name))
        shown = pull["url"] or f"{repo}#{pull['number']}"  # no link when GitHub's was not on our host
        await ctx.log(f"pull request #{pull['number']}: {shown}")
        async with w.sessionmaker() as s:
            await upsert_link(s, **where, kind="github", object_type="pull_request", local_ref=build.id,
                              remote_id=str(pull["number"]), remote_url=pull["url"], created_by=actor)
            await audit("ok", shown, {"repo": repo, "number": pull["number"], "head": branch,
                                            "base": base}, s)
            await s.commit()
        out["pull_request"] = pull
        return out
    except (ConnectorDisabled, ConnectorNotConfigured, Refused) as exc:
        await audit("refused", target, {"message": str(exc)})
        raise
    except Exception as exc:
        await audit("failed", target, {"message": redact(str(exc) or type(exc).__name__, _secrets(state["gh"]))})
        raise
