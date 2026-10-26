"""Target stacks: which of .NET, Java, Go and Python this install can build."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from api.app.config import Settings
from api.app.deps import SettingsDep
from api.app.services import settings as settings_svc
from engine.stacks import STACK_KEYS, stack_status

router = APIRouter(tags=["stacks"])


class _AnyToolPresent:
    """Toolchains as if every tool were installed: the registry check alone (implemented and enabled)."""

    @staticmethod
    def get(_tool: str, _default: Any = None) -> dict[str, bool]:
        return {"present": True}


def stack_views(settings: Settings, toolchains: dict[str, Any]) -> list[dict[str, Any]]:
    """Every stack key with its status (toolchains included, informationally) and the install default."""
    return [stack_status(k, toolchains, settings.enabled_stack_list).model_dump() | {"default": k == settings.default_stack}
            for k in STACK_KEYS]


def require_available(key: str, settings: Settings) -> None:
    """422 ``<key> is not available: <reason>`` unless the stack is implemented and enabled. Toolchain
    presence is not checked here: the API pods of a split deployment have none, the worker checks it
    when a build starts."""
    status = stack_status(key, _AnyToolPresent(), settings.enabled_stack_list)
    if not status.available:
        raise HTTPException(status_code=422, detail=f"{key} is not available: {status.reason}")


@router.get("/stacks")
async def list_stacks(settings: SettingsDep) -> dict[str, Any]:
    toolchains = await asyncio.to_thread(settings_svc.detect_toolchains)
    return {"default": settings.default_stack, "stacks": stack_views(settings, toolchains)}
