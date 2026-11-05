"""Target stacks: which of .NET, Java, Go and Python this install can build."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from api.app.config import Settings
from api.app.deps import SettingsDep, require_user
from api.app.services import settings as settings_svc
from engine.stacks import STACK_KEYS, stack_status

log = logging.getLogger(__name__)

router = APIRouter(tags=["stacks"])


class _AnyToolPresent:
    """Toolchains as if every tool were installed: the registry check alone (implemented and enabled)."""

    @staticmethod
    def get(_tool: str, _default: Any = None) -> dict[str, bool]:
        return {"present": True}


def stack_views(settings: Settings, toolchains: dict[str, Any]) -> list[dict[str, Any]]:
    """Every stack key with its status and the install default. ``available``/``reason`` fold in the
    toolchain check; ``selectable`` is the registry check alone (implemented and enabled, what create,
    PATCH and Settings accept, X14) and ``toolchain_missing`` lists the absent required tools."""
    enabled = settings.enabled_stack_list
    views = []
    for k in STACK_KEYS:
        status = stack_status(k, toolchains, enabled)
        registry = stack_status(k, _AnyToolPresent(), enabled)
        missing = [t for t in status.required_tools if not (toolchains.get(t) or {}).get("present")]
        views.append(status.model_dump() | {"default": k == effective_default(settings), "selectable": registry.available,
                                            "toolchain_missing": missing})
    return views


#: Who may change a project's target stack: the SME picks it at upload, the architect at design.
STACK_CHANGERS = frozenset({"sme", "architect", "admin"})


def effective_default(settings: Settings) -> str:
    """The stored default stack, or (when the install no longer enables it) the first enabled one,
    dotnet by preference: a stale default must not make every create without a stack a 422."""
    enabled = settings.enabled_stack_list
    if settings.default_stack in enabled or not enabled:
        return settings.default_stack
    fallback = "dotnet" if "dotnet" in enabled else enabled[0]
    log.warning("the default stack %r is not enabled on this install (%s); using %s",
                settings.default_stack, ", ".join(enabled), fallback)
    return fallback


def require_available(key: str, settings: Settings) -> None:
    """422 ``<key> is not available: <reason>`` unless the stack is implemented and enabled. Toolchain
    presence is not checked here: the API pods of a split deployment have none, the worker checks it
    when a build starts."""
    status = stack_status(key, _AnyToolPresent(), settings.enabled_stack_list)
    if not status.available:
        raise HTTPException(status_code=422, detail=f"{key} is not available: {status.reason}")


@router.get("/stacks")
async def list_stacks(request: Request, settings: SettingsDep) -> dict[str, Any]:
    """The stacks, plus what the caller may do with them (so the screen derives no role from a name)."""
    toolchains = await asyncio.to_thread(settings_svc.detect_toolchains)
    try:
        roles = (await require_user(request, request.headers.get("x-user"), request.headers.get("x-agent"))).roles
    except HTTPException:
        roles = frozenset()
    return {"default": effective_default(settings), "stacks": stack_views(settings, toolchains),
            "can_change_stack": bool(roles & STACK_CHANGERS), "can_set_default": "admin" in roles}
