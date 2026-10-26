"""Target-stack registry: ``get_stack``, ``all_stacks`` and ``stack_status``.

A stack is available when it is implemented, enabled on this install (``ENABLED_STACKS``, the
chart's ``stacks.enabled``) and its required tools are detected (``scripts/toolchain.detect``);
the status reason names the first condition that fails.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from engine.stacks.base import STACK_KEYS, StackGenerator, StackKey, StackStatus

__all__ = ["STACK_KEYS", "StackKey", "all_stacks", "get_stack", "stack_status"]

# Label and tools of the stacks that are not implemented yet, so they can still be listed with
# their reason. An implemented stack's own object is the source: its entry here goes when it
# registers in ``_FACTORIES``.
_PENDING: dict[str, tuple[str, tuple[str, ...]]] = {
    "java": ("Java 21 · Spring Boot 3.5 · Maven", ("java", "mvn")),
    "go": ("Go 1.25 · net/http · pgx", ("go",)),
    "python": ("Python 3.12 · FastAPI · uv", ("python3", "uv")),
}


def _dotnet() -> StackGenerator:
    from engine.stacks.dotnet import DotnetStack
    return DotnetStack()


# Implemented stacks, in STACK_KEYS order; Tasks 7, 12 and 14 register java, go and python.
_FACTORIES: dict[str, Callable[[], StackGenerator]] = {"dotnet": _dotnet}
_INSTANCES: dict[str, StackGenerator] = {}


def get_stack(key: str) -> StackGenerator:
    """The stack object for ``key``; ``KeyError`` for an unknown or a not yet implemented stack."""
    factory = _FACTORIES.get(key)
    if factory is None:
        if key in STACK_KEYS:
            raise KeyError(f"stack {key!r} is not implemented yet")
        raise KeyError(f"unknown stack {key!r}")
    if key not in _INSTANCES:
        _INSTANCES[key] = factory()
    return _INSTANCES[key]


def all_stacks() -> list[StackGenerator]:
    """Every implemented stack, in ``STACK_KEYS`` order."""
    return [get_stack(k) for k in STACK_KEYS if k in _FACTORIES]


def stack_status(key: str, toolchains: dict[str, Any], enabled: Iterable[str]) -> StackStatus:
    """Whether ``key`` can be built on this install, with the first missing condition as reason.

    ``toolchains`` is ``scripts/toolchain.detect()`` output (``{tool: {"present": bool, ...}}``);
    ``enabled`` the stack keys this install enables. ``KeyError`` for a key outside ``STACK_KEYS``.
    """
    if key not in STACK_KEYS:
        raise KeyError(f"unknown stack {key!r}")
    if key in _FACTORIES:
        stack = get_stack(key)
        label, tools = stack.label, tuple(stack.required_tools)
    else:
        label, tools = _PENDING[key]

    def status(reason: str | None) -> StackStatus:
        return StackStatus(key=key, label=label, available=reason is None, reason=reason,
                           required_tools=list(tools))

    if key not in _FACTORIES:
        return status("not implemented yet")
    if key not in {e.strip() for e in enabled}:
        return status("not enabled on this install (stacks.enabled)")
    for tool in tools:
        if not (toolchains.get(tool) or {}).get("present"):
            return status(f"toolchain missing: {tool}")
    return status(None)
