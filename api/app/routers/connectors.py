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
from api.app.models import AuditLog, Build, Process, Setting
from api.app.schemas.connector import ConnectorsPatch, GithubPushIn, JobAccepted, LinkOut
from api.app.services.github_push import PUSH_KIND
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
    """Queue ``connector.github.push``: 409 when GitHub is off or has no credential (no request is
    made), 422 for a repository or branch name refused before anything is queued."""
    build = await session.get(Build, build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="build not found")
    proc = await session.get(Process, build.process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail="the build's process is gone")
    if not valid_repo(body.repo):
        raise HTTPException(status_code=422, detail=f"repo: not owner/name: {body.repo!r}")
    branch = (body.branch or "").strip() or default_push_branch(proc.name, build.id)
    if not valid_push_branch(branch):
        raise HTTPException(status_code=422, detail=PUSH_REFUSED)
    base = (body.base or "").strip() or None
    if base is not None and not valid_branch(base):
        raise HTTPException(status_code=422, detail=f"base: not a branch name: {base!r}")
    connector = await connector_for(session, settings, "github")
    try:
        connector.ready()
    except (ConnectorDisabled, ConnectorNotConfigured) as exc:
        await audit_connector(session, principal.actor, "github", "push", project_id=proc.project_id,
                              process_id=proc.id, target=f"{body.repo}@{branch}", outcome="refused",
                              detail={"build_id": build.id, "message": str(exc)})
        await session.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if build.status in UNFINISHED:
        raise HTTPException(status_code=409, detail="the build has not finished")
    if not build.output_dir or not Path(build.output_dir).is_dir():
        raise HTTPException(status_code=409, detail="the build has no solution on disk")
    job_id = await runner.submit(PUSH_KIND, {"build_id": build.id, "repo": body.repo, "branch": branch,
                                             "open_pr": body.open_pr, "base": base, "actor": principal.actor},
                                 project_id=proc.project_id, process_id=proc.id)
    if job_id is None:  # submit answers None only for a payload with a dedupe_key, which this has not
        raise RuntimeError("the push job was not queued")
    return JobAccepted(job_id=job_id)
