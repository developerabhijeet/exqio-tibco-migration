"""Per-process reads for tab 2: IR, canvas, I/O card and the raw source file."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.app.deps import SessionDep
from api.app.models import Process
from api.app.schemas.process import Canvas
from api.app.services.process_views import canvas_for, io_card_for, source_file
from engine.domains.models import IOCard

router = APIRouter(tags=["processes"])


async def _process(session: AsyncSession, process_id: str) -> Process:
    proc = await session.get(Process, process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail="process not found")
    return proc


@router.get("/processes/{process_id}")
async def get_process(process_id: str, session: SessionDep) -> dict[str, Any]:
    """The neutral IR (engine ``Process``) as stored by the scan."""
    return (await _process(session, process_id)).ir


@router.get("/processes/{process_id}/canvas", response_model=Canvas)
async def get_canvas(process_id: str, session: SessionDep) -> dict[str, Any]:
    return await canvas_for(session, await _process(session, process_id))


@router.get("/processes/{process_id}/io-card", response_model=IOCard)
async def get_io_card(process_id: str, session: SessionDep) -> IOCard:
    return await io_card_for(session, await _process(session, process_id))


@router.get("/processes/{process_id}/xml", response_class=PlainTextResponse)
async def get_xml(process_id: str, session: SessionDep) -> PlainTextResponse:
    path = await source_file(session, await _process(session, process_id))
    if path is None:
        raise HTTPException(status_code=404, detail="source file not found in the stored upload")
    data = await asyncio.to_thread(path.read_bytes)
    return PlainTextResponse(data.decode("utf-8", errors="replace"))
