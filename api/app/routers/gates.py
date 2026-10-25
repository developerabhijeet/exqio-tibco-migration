"""Gate decisions G1 to G6 (design spec 5.2). Humans only: ``X-Agent`` 403.

``AUTH_MODE=oidc``: only the gate owner's role (``GATE_OWNERS``) may decide a gate, checked
before anything about the process is read. ``dev``: the persona the screen offers the buttons to
is not enforced here, as before.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from api.app.auth.roles import GATE_OWNERS
from api.app.deps import PrincipalDep, SessionDep, SettingsDep
from api.app.models import Process
from api.app.schemas.analysis import GateDecision, GateOut
from api.app.services import gates as svc

router = APIRouter(tags=["gates"])


@router.get("/processes/{process_id}/gates", response_model=list[GateOut])
async def list_gates(process_id: str, session: SessionDep) -> list[dict]:
    proc = await session.get(Process, process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail="process not found")
    return await svc.gates_view(session, proc)  # read only: stale gates show stale: true


@router.post("/gates/{process_id}/{gate}", response_model=GateOut)
async def decide_gate(process_id: str, gate: str, body: GateDecision, session: SessionDep,
                      principal: PrincipalDep, settings: SettingsDep, request: Request) -> dict:
    """Approve or reject a gate. 409 with the reason when the gate is not enabled."""
    if gate not in svc.GATES:
        raise HTTPException(status_code=404, detail=f"unknown gate {gate}")
    owner = GATE_OWNERS[gate]
    if settings.auth_mode == "oidc" and owner not in principal.roles:
        raise HTTPException(status_code=403, detail=f"{gate} is decided by the {owner} role")
    proc = await session.get(Process, process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail="process not found")
    try:
        out = await svc.decide(session, proc, gate, body.state, (body.note or "").strip() or None,
                               principal)
    except svc.GateBlocked as exc:
        await session.commit()  # keep any stale-gate resets reconcile wrote
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await svc.enqueue_gate_ingest(request.app.state.sessionmaker, settings, proc, gate)
    return out
