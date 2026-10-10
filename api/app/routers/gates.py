"""Gate decisions G1 to G6 (design spec 5.2). Humans only: ``X-User`` required, ``X-Agent`` 403."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.app.deps import SessionDep, UserDep
from api.app.models import Process
from api.app.schemas.analysis import GateDecision, GateOut
from api.app.services import gates as svc

router = APIRouter(tags=["gates"])


@router.get("/processes/{process_id}/gates", response_model=list[GateOut])
async def list_gates(process_id: str, session: SessionDep) -> list[dict]:
    proc = await session.get(Process, process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail="process not found")
    if await svc.reconcile(session, proc):
        await session.commit()
    return await svc.gates_view(session, proc)


@router.post("/gates/{process_id}/{gate}", response_model=GateOut)
async def decide_gate(process_id: str, gate: str, body: GateDecision, session: SessionDep,
                      user: UserDep) -> dict:
    """Approve or reject a gate. 409 with the reason when the gate is not enabled."""
    if gate not in svc.GATES:
        raise HTTPException(status_code=404, detail=f"unknown gate {gate}")
    proc = await session.get(Process, process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail="process not found")
    try:
        out = await svc.decide(session, proc, gate, body.state, (body.note or "").strip() or None, user)
    except svc.GateBlocked as exc:
        await session.commit()  # keep any stale-gate resets reconcile wrote
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return out
