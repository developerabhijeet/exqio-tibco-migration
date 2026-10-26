"""The ``connector.github.push`` job: push a build to an ``exiqo/`` branch and open (or update) its
pull request; the branch and the pull request are recorded as links of the process and audited.

Payload ``{build_id, repo, branch, open_pr, base, actor}`` (no credential: the token is read from
its mounted file when the job runs); result ``{branch, commit, pull_request}``.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from api.app.connectors.audit import audit_connector
from api.app.connectors.base import ConnectorError
from api.app.connectors.github import GitHubConnector
from api.app.connectors.links import upsert_link
from api.app.connectors.registry import connector_for
from api.app.models import Build, Process
from api.app.services.build_tree import DOWNLOAD_EXCLUDED, export_note

if TYPE_CHECKING:
    from api.app.jobs.registry import WorkerServices
    from api.app.jobs.runner import JobContext

PUSH_KIND = "connector.github.push"
EXPORT_NOTE = "EXPORTED_FROM.md"


def _pr_body(build: Build, proc: Process, commit: str, brand: str) -> str:
    return (f"{brand} Migrate build `{build.id}` of the TIBCO process **{proc.name}** "
            f"(`{proc.source_path}`).\n\n"
            f"- Commit: `{commit}`\n- Build status: {build.status}\n"
            f"- Build hash: `{build.build_hash or 'not recorded'}`\n\n"
            "Provenance is in `EXPORTED_FROM.md`. Approval is the G4 gate record in "
            f"{brand} Migrate, not this pull request; review and merge it as any other change.\n")


async def push_job(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    p = ctx.payload
    repo, branch, actor = p["repo"], p["branch"], p.get("actor") or "system"
    async with w.sessionmaker() as s:
        build = await s.get(Build, p["build_id"])
        if build is None:
            raise RuntimeError(f"build {p['build_id']} not found")
        proc = await s.get(Process, build.process_id)
        if proc is None:
            raise RuntimeError(f"the process of build {build.id} is gone")
        gh = await connector_for(s, w.settings, "github")
    if not isinstance(gh, GitHubConnector):
        raise TypeError(f"the GitHub connector is served by {type(gh).__name__}")
    gh.ready()  # off or no credential since the request was accepted: fail before any request
    if not build.output_dir or not Path(build.output_dir).is_dir():
        raise RuntimeError("the build has no solution on disk")
    where = {"project_id": proc.project_id, "process_id": proc.id}

    async def audit(op: str, outcome: Literal["ok", "failed", "refused"], target: str, detail: dict[str, Any]) -> None:
        async with w.sessionmaker() as s:
            await audit_connector(s, actor, "github", op, target=target, outcome=outcome, detail=detail,
                                  **where)
            await s.commit()

    await ctx.stage("push")
    target = f"{repo}@{branch}"
    try:
        base = p.get("base") or await gh.default_branch(repo)
        await ctx.log(f"pushing build {build.id[:8]} to {repo} branch {branch} (on top of {base})")
        commit = await gh.push_build(
            Path(build.output_dir), repo, branch,
            f"{proc.name}: build {build.id[:8]} from {w.settings.brand_name} Migrate",
            base_branch=base, extra_files={EXPORT_NOTE: export_note(build, proc).encode()},
            excluded=DOWNLOAD_EXCLUDED)
    except (ConnectorError, ValueError) as exc:
        await audit("push", "failed", target, {"build_id": build.id, "message": str(exc)})
        raise
    await ctx.log(f"pushed commit {commit[:12]} to {branch}")
    async with w.sessionmaker() as s:
        await upsert_link(s, **where, kind="github", object_type="branch", local_ref=build.id,
                          remote_id=branch, remote_url=f"{gh.base_url}/{repo}/tree/{branch}", created_by=actor)
        await audit_connector(s, actor, "github", "push", target=target, outcome="ok",
                              detail={"build_id": build.id, "repo": repo, "branch": branch, "base": base,
                                      "commit": commit}, **where)
        await s.commit()

    pull: dict[str, Any] | None = None
    if p.get("open_pr", True):
        await ctx.stage("pull-request")
        try:
            pull = await gh.open_pull_request(repo, branch, base, f"{proc.name}: build {build.id[:8]}",
                                              _pr_body(build, proc, commit, w.settings.brand_name))
        except (ConnectorError, ValueError) as exc:
            await audit("pull-request", "failed", target, {"build_id": build.id, "message": str(exc)})
            raise
        await ctx.log(f"pull request #{pull['number']}: {pull['url']}")
        async with w.sessionmaker() as s:
            await upsert_link(s, **where, kind="github", object_type="pull_request", local_ref=build.id,
                              remote_id=str(pull["number"]), remote_url=pull["url"], created_by=actor)
            await audit_connector(s, actor, "github", "pull-request", target=pull["url"], outcome="ok",
                                  detail={"build_id": build.id, "repo": repo, "number": pull["number"],
                                          "head": branch, "base": base}, **where)
            await s.commit()
    return {"branch": branch, "commit": commit, "pull_request": pull}
