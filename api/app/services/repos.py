"""A GitHub repository as a source (sub-project 6, spec section 4).

A project connects ``owner/name`` and a branch, as an ``estate`` (TIBCO sources) or a ``build`` (an
exported build of one process, with hand edits). Every request to GitHub goes through sub-project
7's ``GitHubConnector``: REST through its allowlisting ``ConnectorClient``, the clone through its
hermetic ``git`` (``gitcli``), each audited as a connector call.

``source.fetch`` clones the branch shallowly into ``DATA_DIR/repos/<repo id>/<commit>``, checks its
size against ``upload_max_bytes`` and classifies it by content:

- ``EXIQO_BASELINE.json`` at the root (or under one top folder): an edit set against the build it
  names, in an iteration ``kind="repo"`` with ``source = {repo_id, branch, commit}``;
- else, for an ``estate`` repository: an ``Upload`` (``source_kind="repo"``, ``.git`` left out)
  and a queued scan;
- else (a ``build`` repository without a manifest): nothing to import.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from api.app.config import Settings
from api.app.connectors.audit import audit_connector
from api.app.connectors.base import ConnectorDisabled, ConnectorError, ConnectorNotConfigured
from api.app.connectors.gitcli import NO_HOME
from api.app.connectors.github import GitHubConnector, slug, valid_branch, valid_repo
from api.app.connectors.links import upsert_link
from api.app.connectors.registry import connector_for
from api.app.db import new_id
from api.app.jobs.locks import try_advisory_lock
from api.app.jobs.runner import enqueue
from api.app.models import Build, File, Process, ProjectRepo, Upload
from api.app.services import edits as edits_svc
from api.app.services import gates as gate_svc
from api.app.services import github_push as github_push_svc
from api.app.services import uploads
from api.app.services.build_tree import DOWNLOAD_EXCLUDED, export_note
from engine.child_env import child_env

if TYPE_CHECKING:
    from api.app.jobs.registry import WorkerServices
    from api.app.jobs.runner import JobContext

FETCH_KIND = "source.fetch"
Purpose = Literal["estate", "build"]
PURPOSES: tuple[str, ...] = ("estate", "build")
REPO_PATTERN = r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"
_REPO = re.compile(REPO_PATTERN)
GIT_SKIP = frozenset({".git"})
NOTHING = "no EXIQO_BASELINE.json on {branch}: nothing to import into a build repository"


class RepoRefused(ValueError):
    """A repository or branch name that is refused before anything is sent (HTTP 422)."""


# ------------------------------------------------------------------ names
def check_repo(repo: str) -> tuple[str, str]:
    """``(owner, name)`` of ``owner/name``; ``RepoRefused`` for anything else."""
    if not _REPO.fullmatch(repo or "") or not valid_repo(repo):
        raise RepoRefused(f"not a repository name (owner/name): {repo[:200]!r}")
    owner, name = repo.split("/")
    return owner, name


def _check_ref_format(branch: str) -> bool:
    """``git check-ref-format --branch``, with nothing of the host's git configuration."""
    env = {**child_env(), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "HOME": NO_HOME,
           "GIT_TERMINAL_PROMPT": "0"}
    try:
        done = subprocess.run(["git", "check-ref-format", "--branch", branch], env=env, cwd=tempfile.gettempdir(),
                              capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def check_branch(branch: str) -> str:
    if not valid_branch(branch or "") or not _check_ref_format(branch):
        raise RepoRefused(f"not a branch name: {(branch or '')[:200]!r}")
    return branch


def full_name(row: ProjectRepo) -> str:
    return f"{row.owner}/{row.name}"


# ------------------------------------------------------------------ the connector
async def github(session: AsyncSession, settings: Settings) -> GitHubConnector:
    """The GitHub connector, ready (``ConnectorDisabled`` / ``ConnectorNotConfigured`` otherwise)."""
    gh = await connector_for(session, settings, "github")
    if not isinstance(gh, GitHubConnector):
        raise TypeError(f"the GitHub connector is served by {type(gh).__name__}")
    gh.ready()
    return gh


async def _audited(session: AsyncSession, actor: str, op: str, target: str, project_id: str, process_id: str | None,
                   call, **detail: Any) -> Any:
    """``await call()``, audited as a connector call (``ok``, ``refused`` or ``failed``; the
    message is the connector's own, already redacted). The caller commits."""
    try:
        out = await call()
    except (ConnectorDisabled, ConnectorNotConfigured) as exc:
        await audit_connector(session, actor, "github", op, project_id=project_id, process_id=process_id,
                              target=target, outcome="refused", detail={**detail, "message": str(exc)})
        raise
    except ConnectorError as exc:
        await audit_connector(session, actor, "github", op, project_id=project_id, process_id=process_id,
                              target=target, outcome="failed", detail={**detail, "message": exc.message})
        raise
    except Exception as exc:  # e.g. a clone over the size limit: refused after the transfer
        await audit_connector(session, actor, "github", op, project_id=project_id, process_id=process_id,
                              target=target, outcome="failed",
                              detail={**detail, "message": str(exc)[:500] or type(exc).__name__})
        raise
    await audit_connector(session, actor, "github", op, project_id=project_id, process_id=process_id,
                          target=target, outcome="ok", detail=detail)
    return out


# ------------------------------------------------------------------ connect
async def connect_repo(session: AsyncSession, settings: Settings, project_id: str, repo: str, branch: str,
                       purpose: Purpose, process_id: str | None, actor: str) -> ProjectRepo:
    """Check the names, that the connector is on and that it can read ``branch`` (``head``), and
    store the connection. Flushes; the caller commits and queues ``source.fetch``."""
    owner, name = check_repo(repo)
    check_branch(branch)
    if purpose not in PURPOSES:
        raise RepoRefused(f"purpose must be one of: {', '.join(PURPOSES)}")
    if purpose == "build":
        if not process_id:
            raise RepoRefused("a build repository names its process (process_id)")
        process = await session.get(Process, process_id)
        if process is None or process.project_id != project_id:
            raise LookupError("process not found")
    else:
        process_id = None
    gh = await github(session, settings)
    await _audited(session, actor, "head", f"{repo}@{branch}", project_id, process_id,
                   lambda: gh.head(repo, branch), repo=repo, branch=branch)
    row = ProjectRepo(project_id=project_id, owner=owner, name=name, default_branch=branch, purpose=purpose,
                      process_id=process_id, last_commit=None, connected_by=actor)
    session.add(row)
    await session.flush()
    gate_svc.audit(session, None, actor, "repo connected", project_id=project_id,
                   detail={"repo_id": row.id, "repo": repo, "branch": branch, "purpose": purpose,
                           "process_id": process_id})
    return row


def fetch_payload(row: ProjectRepo, branch: str, actor: str) -> dict[str, Any]:
    return {"repo_id": row.id, "project_id": row.project_id, "branch": branch, "actor": actor,
            "dedupe_key": f"repo:{row.id}:{branch}"}


async def queue_fetch(sessionmaker, row: ProjectRepo, branch: str, actor: str) -> str | None:
    return await enqueue(sessionmaker, FETCH_KIND, fetch_payload(row, branch, actor), project_id=row.project_id,
                         process_id=row.process_id)


async def remote_head(session: AsyncSession, settings: Settings, row: ProjectRepo, branch: str, actor: str) -> str:
    gh = await github(session, settings)
    repo = full_name(row)
    return await _audited(session, actor, "head", f"{repo}@{branch}", row.project_id, row.process_id,
                          lambda: gh.head(repo, branch), repo=repo, branch=branch)


async def branches(session: AsyncSession, settings: Settings, row: ProjectRepo) -> list[str]:
    gh = await github(session, settings)
    return await gh.list_branches(full_name(row))


# ------------------------------------------------------------------ fetch
def repos_dir(settings: Settings) -> Path:
    return settings.data_dir.resolve() / "repos"


def tree_size(root: Path) -> int:
    """Bytes of the regular files under ``root``, ``.git`` and symbolic links left out."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in GIT_SKIP and not (here / d).is_symlink()]
        for fn in filenames:
            p = here / fn
            if not p.is_symlink() and p.is_file():
                total += p.stat().st_size
    return total


def manifest_root(root: Path) -> Path | None:
    """Where ``EXIQO_BASELINE.json`` lies: ``root`` or its one top folder (``.git`` aside)."""
    if (root / edits_svc.BASELINE_NAME).is_file():
        return root
    entries = [p for p in root.iterdir() if p.name not in GIT_SKIP]
    if len(entries) == 1 and entries[0].is_dir() and not entries[0].is_symlink() \
            and (entries[0] / edits_svc.BASELINE_NAME).is_file():
        return entries[0]
    return None


async def _clone(gh: GitHubConnector, settings: Settings, row: ProjectRepo, branch: str) -> tuple[str, Path]:
    """Clone into a scratch folder of the repo, then move it to ``<commit>``; refused when larger
    than ``upload_max_bytes`` (nothing is kept then)."""
    base = repos_dir(settings) / row.id
    base.mkdir(parents=True, exist_ok=True)
    scratch = base / f".clone-{uuid.uuid4().hex}"
    try:
        commit = await gh.clone(full_name(row), branch, scratch)
        size = await asyncio.to_thread(tree_size, scratch)
        if size > settings.upload_max_bytes:
            raise uploads.UploadTooLarge(f"{full_name(row)}@{branch} is larger than the configured upload limit "
                                         f"({size} > {settings.upload_max_bytes} bytes)")
        dest = base / commit
        if dest.exists():
            shutil.rmtree(dest)
        scratch.rename(dest)
        return commit, dest
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


async def _upload_from(session: AsyncSession, settings: Settings, row: ProjectRepo, src: Path, branch: str,
                       commit: str) -> Upload:
    upload_id = new_id()
    dest = settings.uploads_dir / row.project_id / upload_id
    try:
        await asyncio.to_thread(uploads.copy_folder, src, dest, settings.upload_max_bytes, GIT_SKIP)
        scan_root = uploads.single_root(dest)
        entries = await asyncio.to_thread(uploads.inventory, dest / scan_root if scan_root else dest)
    except BaseException:
        uploads.remove_tree(dest)
        raise
    up = Upload(id=upload_id, project_id=row.project_id, source_kind="repo",
                source_name=f"{full_name(row)}@{branch}:{commit[:12]}", root_dir=str(dest), scan_root=scan_root,
                file_count=len(entries), total_bytes=sum(e["size"] for e in entries))
    session.add(up)
    await session.flush()
    session.add_all(File(project_id=row.project_id, upload_id=upload_id, **e) for e in entries)
    return up


async def fetch(ctx: JobContext, w: WorkerServices) -> dict[str, Any]:
    """The ``source.fetch`` job: clone, size-check, classify. ``{"kind": "edit_set"|"upload"|"none",
    "id", "commit"}``."""
    p = ctx.payload
    branch, actor = check_branch(p["branch"]), p.get("actor") or "system"
    async with w.sessionmaker() as s:
        row = await s.get(ProjectRepo, p["repo_id"])
        if row is None:
            raise ValueError(f"repository {p['repo_id']} is not connected")
        gh = await github(s, w.settings)
    target = f"{full_name(row)}@{branch}"
    await ctx.stage("clone")
    async with w.sessionmaker() as s:
        try:
            commit, tree = await _audited(s, actor, "clone", target, row.project_id, row.process_id,
                                          lambda: _clone(gh, w.settings, row, branch), repo=full_name(row),
                                          branch=branch)
        finally:
            await s.commit()  # the connector audit line, whatever happened
    await ctx.log(f"cloned {target} at {commit[:12]}")

    await ctx.stage("classify")
    try:
        out = await _classify(w, row.id, tree, branch, commit, actor, ctx)
    except BaseException:
        shutil.rmtree(tree, ignore_errors=True)
        raise
    if out["kind"] == "upload":
        await enqueue(w.sessionmaker, "scan", {"project_id": row.project_id, "upload_id": out["id"]},
                      project_id=row.project_id)
    if out["kind"] != "edit_set":  # an edit set's root_dir is the clone: it stays
        shutil.rmtree(tree, ignore_errors=True)
    return out


async def _classify(w: WorkerServices, repo_id: str, tree: Path, branch: str, commit: str, actor: str,
                    ctx: JobContext) -> dict[str, Any]:
    target = f"@{branch}"
    async with w.sessionmaker() as s:
        row = await s.get(ProjectRepo, repo_id)
        target = f"{full_name(row)}{target}"
        source = {"repo_id": row.id, "branch": branch, "commit": commit}
        found = manifest_root(tree)
        if found is not None:
            es = await edits_svc.create_edit_set_from_tree(s, w.settings, row.project_id, found, actor, source)
            out: dict[str, Any] = {"kind": "edit_set", "id": es.id, "commit": commit}
            await ctx.log(f"{edits_svc.BASELINE_NAME} names build {es.baseline_build_id}: edit set {es.id} with "
                          f"{len(es.files or [])} changed file(s)")
        elif row.purpose == "estate":
            up = await _upload_from(s, w.settings, row, tree, branch, commit)
            out = {"kind": "upload", "id": up.id, "commit": commit}
            await ctx.log(f"upload {up.id}: {up.file_count} file(s) from {target}; scan queued")
        else:
            out = {"kind": "none", "id": None, "commit": commit, "message": NOTHING.format(branch=branch)}
            await ctx.log(out["message"])
        row.last_commit = commit
        gate_svc.audit(s, None, actor, "repo pulled", project_id=row.project_id,
                       detail={"repo_id": row.id, "repo": full_name(row), "branch": branch, "commit": commit,
                               "result": out["kind"], "id": out["id"]})
        await s.commit()
    return out


# ------------------------------------------------------------------ push an iteration's build
ESTATE_TAKES_NO_BUILD = "an estate repository takes no build: connect a build repository"
OTHER_PROCESS = "this repository is connected to {name}"


class PushRefused(ValueError):
    """The build cannot go to this repository (HTTP 422)."""


class PushBusy(RuntimeError):
    """Another push of this build to this branch is running (HTTP 409)."""


def iteration_branch(process_name: str, n: int) -> str:
    return f"exiqo/{slug(process_name)}/iteration-{n}"


async def push_build(session: AsyncSession, sessionmaker, settings: Settings, row: ProjectRepo, build_id: str,
                     open_pr: bool, actor: str) -> dict[str, Any]:
    """Push ``build_id`` (a build of the repository's process) to ``exiqo/<process slug>/iteration-<n>``
    on top of the connected branch, with ``EXPORTED_FROM.md`` and ``EXIQO_BASELINE.json``, and open
    (or update) its pull request into that branch. ``{"branch", "commit", "pull_request"}``. Every
    GitHub call is audited as a connector call; the caller commits."""
    if row.purpose != "build":
        raise PushRefused(ESTATE_TAKES_NO_BUILD)
    build = await session.get(Build, build_id)
    if build is None:
        raise LookupError("build not found")
    owner = await session.get(Process, row.process_id) if row.process_id else None
    if build.process_id != row.process_id:
        raise PushRefused(OTHER_PROCESS.format(name=owner.name if owner else row.process_id))
    if build.status in ("queued", "running"):
        raise PushRefused("the build has not finished")
    if not build.output_dir or not Path(build.output_dir).is_dir():
        raise PushRefused("the build has no solution on disk")
    proc = owner
    repo, base = full_name(row), row.default_branch
    branch = iteration_branch(proc.name, build.iteration)
    target = f"{repo}@{branch}"
    gh = await github(session, settings)
    root = Path(build.output_dir)
    extras = {github_push_svc.EXPORT_NOTE: export_note(build, proc).encode(),
              edits_svc.BASELINE_NAME: edits_svc.baseline_file(build, proc, root, DOWNLOAD_EXCLUDED)}
    async with try_advisory_lock(sessionmaker, github_push_svc.dedupe_key(build.id, repo, branch)) as locked:
        if not locked:
            raise PushBusy("another push of this build to this branch is running")
        base_name, base_exists = await _audited(session, actor, "repository", f"{repo}@{base}", row.project_id,
                                                row.process_id, lambda: gh.base_of(repo, base), repo=repo)
        message = f"{proc.name}: iteration {build.iteration}, build {build.id[:8]} from {settings.brand_name} Migrate"
        commit = await _audited(session, actor, "push", target, row.project_id, row.process_id,
                                lambda: gh.push_build(root, repo, branch, message,
                                                      base_branch=base_name if base_exists else None,
                                                      extra_files=extras, excluded=DOWNLOAD_EXCLUDED),
                                repo=repo, branch=branch, build_id=build.id, iteration=build.iteration)
    await upsert_link(session, project_id=row.project_id, process_id=row.process_id, kind="github",
                      object_type="branch", local_ref=build.id, remote_id=branch,
                      remote_url=f"{gh.base_url}/{repo}/tree/{branch}", created_by=actor)
    out: dict[str, Any] = {"branch": branch, "commit": commit, "pull_request": None}
    if open_pr and base_exists:
        body = github_push_svc._pr_body(build, proc, commit, settings.brand_name)
        pull = await _audited(session, actor, "pull-request", target, row.project_id, row.process_id,
                              lambda: gh.open_pull_request(repo, branch, base_name,
                                                           f"{proc.name}: iteration {build.iteration}", body),
                              repo=repo, head=branch, base=base_name)
        await upsert_link(session, project_id=row.project_id, process_id=row.process_id, kind="github",
                          object_type="pull_request", local_ref=build.id, remote_id=str(pull["number"]),
                          remote_url=pull["url"], created_by=actor)
        out["pull_request"] = {"number": pull["number"], "url": pull["url"]}
    gate_svc.audit(session, None, actor, "repo pushed", project_id=row.project_id,
                   detail={"repo_id": row.id, "repo": repo, "branch": branch, "commit": commit, "build_id": build.id,
                           "iteration": build.iteration})
    return out
