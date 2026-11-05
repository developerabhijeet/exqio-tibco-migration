"""Infrastructure endpoints: liveness, readiness, the About panel's status and the brand.

``/healthz`` answers without touching a dependency, so a slow database never gets a pod killed.
``/readyz`` checks Postgres, that every ``<NAME>_FILE`` secret is readable and Neo4j (when
configured), and answers 503 naming the check that failed, so a pod takes traffic only when it can
serve it. A Neo4j that is down is ``degraded`` (still ready) for ``neo4j_ready_grace_seconds``
(the Community edition's nightly dump stops it), then ``down`` and not ready, unless
``neo4j_gates_readiness`` is off.
Messages carry the check's name and exception class, never a value or a URL.
"""
from __future__ import annotations

import asyncio
import os
import time
from importlib import metadata
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.app.config import Settings
from api.app.deps import AdminDep
from api.app.secrets import SECRET_FILE_FIELDS

router = APIRouter(tags=["system"])

CHECK_TIMEOUT_S = 3.0


async def _select_one(engine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def check_postgres(request: Request) -> str:
    # The timeout covers taking a connection too: a pool waiting on a hung TCP connect (asyncpg's
    # own connect timeout is 60 s) must not hold the probe past CHECK_TIMEOUT_S.
    try:
        await asyncio.wait_for(_select_one(request.app.state.engine), CHECK_TIMEOUT_S)
        return "ok"
    except Exception as exc:  # noqa: BLE001  any failure means "not ready"
        return f"error: {type(exc).__name__}"


async def _probe_neo4j(settings: Settings) -> None:
    """Connectivity only: nothing is written to Neo4j. Raises when it does not answer."""
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password),
        connection_timeout=CHECK_TIMEOUT_S)
    try:
        await asyncio.wait_for(driver.verify_connectivity(), CHECK_TIMEOUT_S)
    finally:
        await driver.close()


async def check_neo4j(settings: Settings, state) -> str:
    """``disabled`` with no URI, ``ok`` when it answers. When it does not: ``degraded: <class>
    (down <s> s)`` until it has been down ``neo4j_ready_grace_seconds`` (the nightly Community dump
    stops it for a minute or so), then ``down: <class>``. ``state.neo4j_down_since`` marks the
    first failed probe and resets on ``ok``."""
    if not settings.neo4j_uri:
        return "disabled"
    try:
        await _probe_neo4j(settings)
    except Exception as exc:  # noqa: BLE001
        now = time.monotonic()
        since = getattr(state, "neo4j_down_since", None)
        if since is None:
            since = state.neo4j_down_since = now
        down = now - since
        if down < settings.neo4j_ready_grace_seconds:
            return f"degraded: {type(exc).__name__} (down {int(down)} s)"
        return f"down: {type(exc).__name__}"
    state.neo4j_down_since = None
    return "ok"


def check_secrets() -> str:
    """Every configured ``<NAME>_FILE`` must be a readable file (a mount that failed is not ready)."""
    bad = []
    for name in SECRET_FILE_FIELDS:
        path = os.environ.get(f"{name.upper()}_FILE", "")
        if path and not (Path(path).is_file() and os.access(path, os.R_OK)):
            bad.append(name)
    return "ok" if not bad else f"error: unreadable {', '.join(bad)}"


async def run_checks(request: Request) -> dict[str, str]:
    postgres, neo4j = await asyncio.gather(
        check_postgres(request), check_neo4j(request.app.state.settings, request.app.state))
    return {"postgres": postgres, "neo4j": neo4j, "secrets": check_secrets()}


# Checks that decide readiness. neo4j is one of them unless NEO4J_GATES_READINESS is off, in which
# case it is reported only.
GATING_CHECKS = ("postgres", "secrets", "neo4j")
_BASE_GATING_CHECKS = ("postgres", "secrets")


def _ready(checks: dict[str, str], settings: Settings) -> bool:
    names = GATING_CHECKS if settings.neo4j_gates_readiness else _BASE_GATING_CHECKS
    return all(checks[n] in ("ok", "disabled") or checks[n].startswith("degraded") for n in names)


def package_version() -> str:
    try:
        return metadata.version("exiqo-migrate")
    except metadata.PackageNotFoundError:
        return "unknown"


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    checks = await run_checks(request)
    return JSONResponse(checks, status_code=200 if _ready(checks, request.app.state.settings) else 503)


@router.get("/system/status")
async def system_status(request: Request, _admin: AdminDep) -> dict:
    """The About panel's data, for the admin role only (dev: ``ops.sam``, per Ruling B)."""
    s: Settings = request.app.state.settings
    return {
        "version": package_version(), "role": s.exiqo_role, "auth_mode": s.auth_mode,
        "deploy_credential_mode": s.deploy_credential_mode, "image_digests": s.image_digests,
        "egress_allowlist": s.egress_allowlist, "brand_name": s.brand_name,
        "support_contact": s.support_contact, "checks": await run_checks(request),
    }


@router.get("/system/brand")
async def brand(request: Request) -> dict[str, str]:
    """Public: the sign-in screen needs the name before anyone has signed in."""
    s: Settings = request.app.state.settings
    return {"brand_name": s.brand_name, "support_contact": s.support_contact,
            "deploy_credential_mode": s.deploy_credential_mode}
