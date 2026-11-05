"""Settings → Connectors (install ceiling, admin layer, connection tests) and connector links.

The writes and the test need ``admin`` in both auth modes (``AdminDep``): this router is not
behind the settings router's ``require_admin_writes``."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import select

from api.app.auth.roles import Principal, dev_principal
from api.app.connectors.audit import audit_connector
from api.app.connectors.base import (
    KINDS,
    ConnectionTest,
    ConnectorDisabled,
    ConnectorError,
    ConnectorKind,
    ConnectorNotConfigured,
)
from api.app.connectors.config import LAST_TEST_KEY, STORED_KEY, load_install, validate_app
from api.app.connectors.figma import (
    FigmaConnector,
    FileNotAllowed,
    Frame,
    valid_file_key,
    valid_node_id,
)
from api.app.connectors.github import (
    PUSH_REFUSED,
    default_push_branch,
    valid_branch,
    valid_push_branch,
    valid_repo,
)
from api.app.connectors.http import header_secrets, redact
from api.app.connectors.links import links_for
from api.app.connectors.registry import connector_for, connectors_view, enabled_flags
from api.app.db import utcnow
from api.app.deps import (
    AdminDep,
    RunnerDep,
    SessionDep,
    SettingsDep,
    current_principal,
    require_roles,
)
from api.app.models import AuditLog, Build, Job, Process, Setting
from api.app.models.connector import ConnectorLink
from api.app.schemas.connector import (
    ConfluencePublishIn,
    ConnectorsPatch,
    FigmaFramesIn,
    FrameLinkOut,
    GithubPushIn,
    JiraIssuesIn,
    JobAccepted,
    LinkOut,
)
from api.app.services.confluence_publish import (
    NO_DESIGN,
    NO_SPACE,
    NO_URL,
    PUBLISH_KIND,
    page_link,
)
from api.app.services.confluence_publish import dedupe_key as confluence_dedupe_key
from api.app.services.evidence import latest_design
from api.app.services.figma_sync import SYNC_KIND as FIGMA_SYNC_KIND
from api.app.services.figma_sync import dedupe_key as figma_dedupe_key
from api.app.services.figma_sync import frame_links, remove_frame_link
from api.app.services.github_push import PUSH_KIND
from api.app.services.github_push import dedupe_key as push_dedupe_key
from api.app.services.jira_issues import (
    ISSUES_KIND,
    NO_PROJECT,
    bad_project_key,
    check_items,
    dedupe_key,
    issue_source,
)
from api.app.services.jira_issues import NO_URL as NO_JIRA_URL
from api.app.services.settings import load_stored

log = logging.getLogger(__name__)
router = APIRouter(tags=["connectors"])

TEST_TIMEOUT_S = 15.0


async def _is_admin(request: Request, x_user: str | None) -> bool:
    """Whether the caller holds ``admin`` (read routes: no principal simply means not admin)."""
    if request.app.state.settings.auth_mode == "oidc":
        principal = await current_principal(request)
    else:
        principal = dev_principal(x_user.strip()) if x_user and x_user.strip() else None
    return principal is not None and "admin" in principal.roles


async def _put_setting(session: SessionDep, key: str, value: Any, user: str) -> None:
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value, updated_by=user))
    else:
        row.value, row.updated_by = value, user


async def _refused(session: SessionDep, principal: Principal, kind: str, op: str, proc: Process, *,
                   status: int, message: str, target: str, detail: dict[str, Any] | None = None) -> HTTPException:
    """Audit a refusal before anything is queued (``refused``, ruling X20) and return the error to
    raise: every connector route answers its 409 and 422 refusals (after the role check) this way."""
    await audit_connector(session, principal.actor, kind, op, project_id=proc.project_id, process_id=proc.id,
                          target=target, outcome="refused", detail={**(detail or {}), "message": message})
    await session.commit()
    return HTTPException(status_code=status, detail=message)


@router.get("/settings/connectors")
async def get_connector_settings(request: Request, session: SessionDep, settings: SettingsDep,
                                 x_user: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    return connectors_view(settings, await load_stored(session), admin=await _is_admin(request, x_user))


@router.put("/settings/connectors")
async def put_connector_settings(body: ConnectorsPatch, session: SessionDep, settings: SettingsDep,
                                 principal: AdminDep) -> dict[str, Any]:
    stored = await load_stored(session)
    install = load_install(settings)
    current_all = dict(stored.get(STORED_KEY) or {})
    changed: dict[str, list[str]] = {}
    for kind in KINDS:
        patch_model = getattr(body, kind)
        if patch_model is None:
            continue
        patch = patch_model.model_dump(exclude_none=True)
        current = current_all.get(kind) or {}
        try:
            app = validate_app(kind, install[kind], patch, current,
                               allow_insecure_loopback=settings.connectors_allow_insecure_loopback)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        value = app.model_dump()
        fields = sorted(k for k in patch if current.get(k, None) != value.get(k))
        if fields:
            changed[kind] = fields
        current_all[kind] = value
    if changed:
        await _put_setting(session, STORED_KEY, current_all, principal.actor)
        # field names only: this model has no secret, but values are not the audit's business
        session.add(AuditLog(actor=principal.actor, actor_kind="user", action="connector settings changed",
                             detail={"fields": changed}))
        await session.commit()
        stored = await load_stored(session)
    return connectors_view(settings, stored, admin=True)


@router.get("/connectors")
async def get_connectors(session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    """Which connectors are on, for any signed-in user, so the UI knows which actions to show."""
    return enabled_flags(settings, await load_stored(session))


@router.post("/connectors/{kind}/test")
async def test_connector(kind: ConnectorKind, session: SessionDep, settings: SettingsDep,
                         principal: AdminDep) -> ConnectionTest:
    """Run the connector's connection test now (15 s at most); audited, and kept as its last test."""
    connector = await connector_for(session, settings, kind)
    try:
        connector.ready()
    except (ConnectorDisabled, ConnectorNotConfigured) as exc:
        await audit_connector(session, principal.actor, kind, "test", target=kind, outcome="refused",
                              detail={"message": str(exc)})
        await session.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    secrets = header_secrets(connector.auth_header())
    started = time.monotonic()
    try:
        async with asyncio.timeout(TEST_TIMEOUT_S):
            result = await connector.test()
    except TimeoutError:
        result = ConnectionTest(ok=False, message=f"the connection test timed out after {int(TEST_TIMEOUT_S)} s")
    except ConnectorError as exc:
        result = ConnectionTest(ok=False, message=redact(str(exc), secrets))
    except Exception as exc:  # noqa: BLE001  a connector bug still answers, is audited and recorded
        log.warning("connection test of %s failed unexpectedly: %s", kind,
                    redact(f"{type(exc).__name__}: {exc}", secrets))
        result = ConnectionTest(ok=False, message=redact(f"the connection test failed: {type(exc).__name__}: "
                                                         f"{exc}", secrets))
    if not result.latency_ms:
        result.latency_ms = int((time.monotonic() - started) * 1000)
    result.message = redact(result.message, secrets)
    stored = await load_stored(session)
    last = dict(stored.get(LAST_TEST_KEY) or {})
    last[kind] = {"result": result.model_dump(), "at": utcnow().isoformat(), "actor": principal.actor}
    await _put_setting(session, LAST_TEST_KEY, last, principal.actor)
    await audit_connector(session, principal.actor, kind, "test", target=result.site or kind,
                          outcome="ok" if result.ok else "failed",
                          detail={"latency_ms": result.latency_ms, "account": result.account,
                                  "message": result.message})
    await session.commit()
    return result


@router.get("/processes/{process_id}/links")
async def process_links(process_id: str, session: SessionDep) -> list[LinkOut]:
    if await session.get(Process, process_id) is None:
        raise HTTPException(status_code=404, detail=f"process {process_id} not found")
    return [LinkOut.model_validate(link) for link in await links_for(session, process_id)]


PushRoles = Annotated[Principal, Depends(require_roles("engineer", "admin"))]
UNFINISHED = frozenset({"queued", "running"})


@router.post("/builds/{build_id}/github/push", status_code=202)
async def push_build_to_github(build_id: str, body: GithubPushIn, session: SessionDep, settings: SettingsDep,
                               runner: RunnerDep, principal: PushRoles) -> JobAccepted:
    """Queue ``connector.github.push``: 422 for a repository or branch name refused, 409 when GitHub
    is off or has no credential, the build has not finished or has no solution on disk (each
    audited ``refused``; no request is made), or the same push is already queued or running."""
    build = await session.get(Build, build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="build not found")
    proc = await session.get(Process, build.process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail="the build's process is gone")
    target = f"{body.repo}@{body.branch or ''}"

    async def refuse(status: int, message: str) -> HTTPException:
        return await _refused(session, principal, "github", "push", proc, status=status, message=message,
                              target=target, detail={"build_id": build.id})

    if not valid_repo(body.repo):
        raise await refuse(422, f"repo: not owner/name: {body.repo!r}")
    branch = (body.branch or "").strip() or default_push_branch(proc.name, build.id)
    target = f"{body.repo}@{branch}"
    if not valid_push_branch(branch):
        raise await refuse(422, PUSH_REFUSED)
    base = (body.base or "").strip() or None
    if base is not None and not valid_branch(base):
        raise await refuse(422, f"base: not a branch name: {base!r}")
    connector = await connector_for(session, settings, "github")
    try:
        connector.ready()
    except (ConnectorDisabled, ConnectorNotConfigured) as exc:
        raise await refuse(409, str(exc)) from exc
    if build.status in UNFINISHED:
        raise await refuse(409, "the build has not finished")
    if not build.output_dir or not Path(build.output_dir).is_dir():
        raise await refuse(409, "the build has no solution on disk")
    key = push_dedupe_key(build.id, body.repo, branch)
    running = await session.scalar(select(Job.id).where(Job.kind == PUSH_KIND, Job.status == "running",
                                                        Job.payload["dedupe_key"].astext == key).limit(1))
    if running is not None:
        raise HTTPException(status_code=409, detail=f"this build is already being pushed to {branch}")
    job_id = await runner.submit(PUSH_KIND, {"build_id": build.id, "repo": body.repo, "branch": branch,
                                             "open_pr": body.open_pr, "base": base, "actor": principal.actor,
                                             "dedupe_key": key},
                                 project_id=proc.project_id, process_id=proc.id)
    if job_id is None:  # a queued push of this build to this branch will push the same tree
        raise HTTPException(status_code=409, detail=f"a push of this build to {branch} is already queued")
    return JobAccepted(job_id=job_id)


PublishRoles = Annotated[Principal, Depends(require_roles("architect", "admin"))]


@router.post("/processes/{process_id}/confluence/publish", status_code=202)
async def publish_to_confluence(process_id: str, session: SessionDep, settings: SettingsDep, runner: RunnerDep,
                                principal: PublishRoles, body: ConfluencePublishIn | None = None) -> JobAccepted:
    """Queue ``connector.confluence.publish``: 409 when Atlassian is off, has no credential, no
    Confluence URL or space key, or the process has no design record (each audited ``refused``;
    no request is made). ``create_again`` makes a new page when the linked one was deleted."""
    create_again = bool(body and body.create_again)
    proc = await session.get(Process, process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail=f"process {process_id} not found")
    connector = await connector_for(session, settings, "atlassian")

    async def refuse(message: str) -> HTTPException:
        return await _refused(session, principal, "confluence", "publish", proc, status=409, message=message,
                              target=proc.ir_id)

    try:
        connector.ready()
    except (ConnectorDisabled, ConnectorNotConfigured) as exc:
        raise await refuse(str(exc)) from exc
    if not connector.config.url("confluence_url"):
        raise await refuse(NO_URL)
    if not getattr(connector.config.app, "space_key", ""):
        raise await refuse(NO_SPACE)
    if await latest_design(session, proc.id) is None:
        raise await refuse(NO_DESIGN)
    job_id = await runner.submit(PUBLISH_KIND, {"process_id": proc.id, "actor": principal.actor,
                                                "create_again": create_again,
                                                "dedupe_key": confluence_dedupe_key(proc.id, create_again)},
                                 project_id=proc.project_id, process_id=proc.id)
    if job_id is None:  # a queued publish of this process will render the same, latest data
        raise HTTPException(status_code=409, detail="a Confluence publish of this process is already queued")
    return JobAccepted(job_id=job_id)


@router.get("/processes/{process_id}/confluence")
async def confluence_page(process_id: str, session: SessionDep) -> LinkOut:
    """The process's architecture page link (its id, URL and published version)."""
    if await session.get(Process, process_id) is None:
        raise HTTPException(status_code=404, detail=f"process {process_id} not found")
    link = await page_link(session, process_id)
    if link is None:
        raise HTTPException(status_code=404, detail="not published to Confluence yet")
    return LinkOut.model_validate(link)


IssueRoles = Annotated[Principal, Depends(require_roles("architect", "sme", "qa", "admin"))]


@router.post("/processes/{process_id}/jira/issues", status_code=202)
async def jira_issues(process_id: str, body: JiraIssuesIn, session: SessionDep, settings: SettingsDep,
                      runner: RunnerDep, principal: IssueRoles) -> JobAccepted:
    """Queue ``connector.jira.issues``: 422 for an item that does not exist or has nothing to track
    (a finding decided ``keep`` or ``ask``, an answered question, an explained difference) and for
    a malformed project key; 409 when Atlassian is off, has no credential, no Jira URL or project
    key (audited ``refused``; no request is made), or the same request is already queued. Items
    are queued in their stable form (ruling X17)."""
    proc = await session.get(Process, process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail=f"process {process_id} not found")
    src = await issue_source(session, proc)
    items = list(body.items)

    async def refuse(status: int, message: str) -> HTTPException:
        return await _refused(session, principal, "jira", "create", proc, status=status, message=message,
                              target=proc.ir_id, detail={"items": [i.local_ref for i in items]})

    try:
        items = check_items(src, body.items)
    except ValueError as exc:
        raise await refuse(422, str(exc)) from exc
    connector = await connector_for(session, settings, "atlassian")

    try:
        connector.ready()
    except (ConnectorDisabled, ConnectorNotConfigured) as exc:
        raise await refuse(409, str(exc)) from exc
    if not connector.config.url("jira_url"):
        raise await refuse(409, NO_JIRA_URL)
    project_key = str(getattr(connector.config.app, "project_key", "") or "")
    problem = bad_project_key(project_key)
    if problem:
        raise await refuse(409 if problem == NO_PROJECT else 422, problem)
    job_id = await runner.submit(ISSUES_KIND, {
        "process_id": proc.id, "items": [i.model_dump() for i in items], "create_again": body.create_again,
        "actor": principal.actor, "dedupe_key": dedupe_key(proc.id, items, body.create_again)},
        project_id=proc.project_id, process_id=proc.id)
    if job_id is None:
        raise HTTPException(status_code=409, detail="the same issues are already queued for this process")
    return JobAccepted(job_id=job_id)


FigmaRoles = Annotated[Principal, Depends(require_roles("architect", "admin"))]
NO_FIGMA_URL = "the Figma connector has no API URL (connectors.figma.apiUrl)"


def _frame_link_out(link: Any) -> FrameLinkOut:
    d = link.detail or {}
    return FrameLinkOut(id=link.id, file_key=str(d.get("file_key") or link.local_ref.partition(":")[0]),
                        node_id=link.remote_id, name=str(d.get("name") or link.remote_id),
                        page=str(d.get("page") or ""), text=str(d.get("text") or ""), url=link.remote_url,
                        has_image=bool(d.get("image_path")), image_note=d.get("image_note"),
                        stale=link.stale, updated_at=link.updated_at)


async def _figma_ready(session: SessionDep, settings: SettingsDep) -> FigmaConnector:
    connector = await connector_for(session, settings, "figma")
    if not isinstance(connector, FigmaConnector):
        raise HTTPException(status_code=500, detail="the Figma connector is not available")
    return connector


def _check_figma_file(connector: FigmaConnector, key: str) -> None:
    try:
        connector.check_file(key)
    except (FileNotAllowed, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/figma/files/{key}/frames")
async def figma_file_frames(key: str, session: SessionDep, settings: SettingsDep,
                            principal: FigmaRoles) -> list[Frame]:
    """The frames of a Figma file, to choose from: 422 for a file outside the install's Figma
    files, 409 when Figma is off, has no credential or no API URL, 502 when Figma fails."""
    connector = await _figma_ready(session, settings)
    _check_figma_file(connector, key)
    try:
        connector.ready()
        connector.api_url  # noqa: B018  raises when there is none
    except (ConnectorDisabled, ConnectorNotConfigured) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConnectorError as exc:
        raise HTTPException(status_code=409, detail=NO_FIGMA_URL) from exc
    secrets = header_secrets(connector.auth_header())
    try:
        return await connector.frames(key)
    except ConnectorError as exc:
        status = 404 if exc.status == 404 else 502
        raise HTTPException(status_code=status, detail=redact(exc.message, secrets)) from exc


@router.post("/processes/{process_id}/figma/frames", status_code=202)
async def attach_figma_frames(process_id: str, body: FigmaFramesIn, session: SessionDep, settings: SettingsDep,
                              runner: RunnerDep, principal: FigmaRoles) -> JobAccepted:
    """Queue ``connector.figma.sync``: 422 for a malformed or disallowed file or frame id, 409 when
    Figma is off, has no credential or API URL (no request is made) or the same frames are already
    queued."""
    proc = await session.get(Process, process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail=f"process {process_id} not found")
    node_ids = list(dict.fromkeys(body.node_ids))

    async def refuse(status: int, message: str) -> HTTPException:
        return await _refused(session, principal, "figma", "sync", proc, status=status, message=message,
                              target=body.file_key, detail={"node_ids": node_ids})

    if not valid_file_key(body.file_key):
        raise await refuse(422, f"file_key: not a Figma file key: {body.file_key!r}")
    for node_id in node_ids:
        if not valid_node_id(node_id):
            raise await refuse(422, f"node_ids: not a Figma node id: {node_id!r}")
    connector = await _figma_ready(session, settings)
    try:
        connector.check_file(body.file_key)
    except FileNotAllowed as exc:
        raise await refuse(422, str(exc)) from exc
    try:
        connector.ready()
    except (ConnectorDisabled, ConnectorNotConfigured) as exc:
        raise await refuse(409, str(exc)) from exc
    if not connector.config.url("api_url"):
        raise await refuse(409, NO_FIGMA_URL)
    job_id = await runner.submit(FIGMA_SYNC_KIND, {
        "process_id": proc.id, "file_key": body.file_key, "node_ids": node_ids, "actor": principal.actor,
        "dedupe_key": figma_dedupe_key(proc.id, body.file_key, node_ids)},
        project_id=proc.project_id, process_id=proc.id)
    if job_id is None:
        raise HTTPException(status_code=409, detail="the same frames are already queued for this process")
    return JobAccepted(job_id=job_id)


@router.get("/processes/{process_id}/figma/frames")
async def process_figma_frames(process_id: str, session: SessionDep) -> list[FrameLinkOut]:
    if await session.get(Process, process_id) is None:
        raise HTTPException(status_code=404, detail=f"process {process_id} not found")
    return [_frame_link_out(link) for link in await frame_links(session, process_id)]


@router.delete("/figma/frames/{link_id}", status_code=204)
async def remove_figma_frame(link_id: str, session: SessionDep, settings: SettingsDep,
                             principal: FigmaRoles) -> Response:
    """Detach a frame from its process: the link and its stored thumbnail go (audited)."""
    link = await session.get(ConnectorLink, link_id)
    if link is None or link.object_type != "figma_frame":
        raise HTTPException(status_code=404, detail="frame not found")
    await audit_connector(session, principal.actor, "figma", "detach", project_id=link.project_id,
                          process_id=link.process_id, target=link.remote_url or link.local_ref, outcome="ok",
                          detail={"local_ref": link.local_ref})
    await remove_frame_link(session, settings.data_dir, link)
    await session.commit()
    return Response(status_code=204)


@router.get("/figma/frames/{link_id}/image")
async def figma_frame_image(link_id: str, session: SessionDep, settings: SettingsDep) -> FileResponse:
    """The stored thumbnail of an attached frame (404 when it has none)."""
    link = await session.get(ConnectorLink, link_id)
    if link is None or link.object_type != "figma_frame":
        raise HTTPException(status_code=404, detail="frame not found")
    stored = (link.detail or {}).get("image_path")
    root = settings.data_dir.resolve() / "figma"
    path = Path(str(stored)).resolve() if stored else None
    if path is None or not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="this frame has no thumbnail")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})
