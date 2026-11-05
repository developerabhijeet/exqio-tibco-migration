"""The browser's way to the generated service: ``/builds/{id}/run/proxy/{path}`` on the API.

The service runs on loopback inside the worker that hosts it, and worker pods are never exposed,
so Swagger UI, "Try it out" and the dev-token call come here and are streamed to that worker
(see :mod:`api.app.services.hosting`). ``run_status`` answers ``url`` and ``swagger_url`` on this
route, built with ``url_for`` so they carry the API's mount path and the proxy headers.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from api.app.deps import BrowserUserDep, SessionDep
from api.app.models import Build
from api.app.services import hosting as svc

router = APIRouter(tags=["build"])


def proxy_root(request: Request, build_id: str) -> str:
    """The absolute proxy root of a build's service, ending in ``/``."""
    return str(request.url_for("run_proxy", build_id=build_id, path=""))


def public_urls(request: Request, build_id: str, status: dict) -> dict:
    """Swap the worker's loopback ``url``/``swagger_url`` for the proxy's."""
    if status.get("port") is None:
        return {**status, "url": None, "swagger_url": None}
    root = proxy_root(request, build_id)
    swagger = f"{root}swagger/index.html" if status.get("swagger_url") else None  # none on Java
    return {**status, "url": root, "swagger_url": swagger}


@router.api_route("/builds/{build_id}/run/proxy/{path:path}", methods=svc.PROXY_METHODS, name="run_proxy",
                  include_in_schema=False)
async def run_proxy(build_id: str, path: str, request: Request, session: SessionDep,
                    user: BrowserUserDep) -> Response:
    if svc.unsafe_proxy_path(path, request.scope.get("raw_path")):
        raise HTTPException(status_code=400, detail="dot segments are not allowed in a proxy path")
    build = await session.get(Build, build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="build not found")
    if not build.run_owner:
        raise HTTPException(status_code=404, detail=svc.NOT_RUNNING)
    headers = svc.request_headers(request.headers.items(), host=request.headers.get("host", ""),
                                  proto=request.url.scheme,
                                  prefix=request.url_for("run_proxy", build_id=build_id, path="")
                                  .path.rstrip("/"))
    body = await request.body()
    try:
        return await svc.host_client(request.app.state, build).proxy(
            build_id, request.method, path, request.url.query, headers, body)
    except svc.HostGone as exc:
        await svc.clear_run(session, build)
        raise HTTPException(status_code=502, detail=svc.GONE_REASON) from exc
