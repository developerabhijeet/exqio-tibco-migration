"""Infrastructure endpoints: liveness, readiness, the About panel's status and the brand.

``/healthz`` answers without touching a dependency, so a slow database never gets a pod killed.
``/readyz`` checks Postgres, Neo4j (when configured) and that every ``<NAME>_FILE`` secret is
readable, and answers 503 naming the check that failed, so a pod takes traffic only when it can
serve it. Messages carry the failing check's name and exception class, never a value or a URL.
"""
from __future__ import annotations

import asyncio
import os
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


async def check_postgres(request: Request) -> str:
    try:
        engine = request.app.state.engine
        async with engine.connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), CHECK_TIMEOUT_S)
        return "ok"
    except Exception as exc:  # noqa: BLE001  any failure means "not ready"
        return f"error: {type(exc).__name__}"


async def check_neo4j(settings: Settings) -> str:
    """Connectivity only: nothing is written to Neo4j. ``disabled`` when no URI is configured."""
    if not settings.neo4j_uri:
        return "disabled"
    from neo4j import AsyncGraphDatabase

    driver = None
    try:
        driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password),
            connection_timeout=CHECK_TIMEOUT_S)
        await asyncio.wait_for(driver.verify_connectivity(), CHECK_TIMEOUT_S)
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}"
    finally:
        if driver is not None:
            await driver.close()


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
        check_postgres(request), check_neo4j(request.app.state.settings))
    return {"postgres": postgres, "neo4j": neo4j, "secrets": check_secrets()}


def _ready(checks: dict[str, str]) -> bool:
    return all(v in ("ok", "disabled") for v in checks.values())


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
    return JSONResponse(checks, status_code=200 if _ready(checks) else 503)


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
