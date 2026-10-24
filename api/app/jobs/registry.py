"""Named job handlers: a job is stored data (``kind`` + ``payload``), resolved to code here.

Jobs used to be closures built by the router that queued them, which only works in the process
that built them. Worker pods run jobs the API queued, so each kind registers one handler that
rebuilds what it needs from the payload and :class:`WorkerServices` (settings, knowledge, database,
the generated-service registry, the secret cipher). Handlers live in ``api.app.jobs.handlers``.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

if TYPE_CHECKING:
    from api.app.config import Settings
    from api.app.jobs.runner import JobContext
    from api.app.services.knowledge import Knowledge
    from api.app.services.verify import RunInfo


@dataclass
class WorkerServices:
    """What a handler may use. ``services`` is the API's running-service registry, shared by
    reference in role ``all`` (a later task replaces it)."""

    settings: Settings
    knowledge: Knowledge
    sessionmaker: async_sessionmaker[AsyncSession]
    services: dict[str, RunInfo]
    fernet: Fernet


Handler = Callable[["JobContext", WorkerServices], Awaitable[dict[str, Any]]]

_HANDLERS: dict[str, Handler] = {}


def register(kind: str, handler: Handler) -> None:
    _HANDLERS[kind] = handler


def unregister(kind: str) -> None:
    _HANDLERS.pop(kind, None)


def job(kind: str) -> Callable[[Handler], Handler]:
    """Decorator: register the function as the handler of ``kind``."""
    def deco(fn: Handler) -> Handler:
        register(kind, fn)
        return fn
    return deco


def handler_for(kind: str) -> Handler:
    try:
        return _HANDLERS[kind]
    except KeyError:
        raise KeyError(f"no handler for job kind {kind!r}") from None
