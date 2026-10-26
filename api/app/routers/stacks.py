"""Target stacks: which of .NET, Java, Go and Python this install can build."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException

from api.app.config import Settings
from api.app.deps import SettingsDep
from engine.stacks import STACK_KEYS, stack_status

router = APIRouter(tags=["stacks"])

_TOOLCHAIN_TTL = 30.0
_cache: tuple[float, dict[str, Any]] | None = None


def _toolchains() -> dict[str, Any]:
    """``scripts/toolchain.detect()``, kept for a short while: it runs one process per tool."""
    global _cache
    if _cache is None or time.monotonic() - _cache[0] > _TOOLCHAIN_TTL:
        from scripts.toolchain import detect

        _cache = (time.monotonic(), dict(detect()))
    return _cache[1]


def stack_views(settings: Settings, toolchains: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every stack key with its status and whether it is the install default."""
    tools = _toolchains() if toolchains is None else toolchains
    return [stack_status(k, tools, settings.enabled_stack_list).model_dump() | {"default": k == settings.default_stack}
            for k in STACK_KEYS]


def require_available(key: str, settings: Settings) -> None:
    """422 ``<key> is not available: <reason>`` when the stack cannot be built here."""
    status = stack_status(key, _toolchains(), settings.enabled_stack_list)
    if not status.available:
        raise HTTPException(status_code=422, detail=f"{key} is not available: {status.reason}")


@router.get("/stacks")
async def list_stacks(settings: SettingsDep) -> dict[str, Any]:
    import asyncio

    tools = await asyncio.to_thread(_toolchains)
    return {"default": settings.default_stack, "stacks": stack_views(settings, tools)}
