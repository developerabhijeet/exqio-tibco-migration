"""Named job handlers: a job is stored data (``kind`` + ``payload``), resolved to code here.

Jobs used to be closures built by the router that queued them, which only works in the process
that built them. Worker pods run jobs the API queued, so each kind registers one handler that
rebuilds what it needs from the payload and :class:`WorkerServices` (settings, knowledge, database,
the generated services this process hosts, the secret cipher). Handlers live in ``api.app.jobs.handlers``.

A kind may register its own ``max_attempts`` (a number, or the name of a ``Settings`` field read when
the job finishes); kinds without one use ``JOB_MAX_ATTEMPTS``. A handler raises :class:`RetryLater`
to go back to the queue for ``delay_s`` seconds (see ``api.app.jobs.runner``). Kinds starting with
:data:`INTERNAL_PREFIX` are the platform's own bookkeeping, kept off the History screen.
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
    from api.app.services.hosting import Hosting
    from api.app.services.knowledge import Knowledge


@dataclass
class WorkerServices:
    """What a handler may use. ``hosting`` holds the generated services this process runs (Verify):
    ``service_start`` starts one there and the Verify job, pinned to this worker, calls it."""

    settings: Settings
    knowledge: Knowledge
    sessionmaker: async_sessionmaker[AsyncSession]
    hosting: Hosting
    fernet: Fernet


Handler = Callable[["JobContext", WorkerServices], Awaitable[dict[str, Any]]]
MaxAttempts = int | str | None

INTERNAL_PREFIX = "memory."

_HANDLERS: dict[str, Handler] = {}
_MAX_ATTEMPTS: dict[str, int | str] = {}


class RetryLater(Exception):
    """Raised by a handler: requeue the job, claimable again in ``delay_s`` seconds.

    A counted retry uses one of the kind's attempts; once they are spent the job fails with
    ``<reason> after <attempts> attempts``. ``count_attempt=False`` (a busy lock, not a failure)
    hands the attempt back."""

    def __init__(self, delay_s: float, reason: str, *, count_attempt: bool = True) -> None:
        super().__init__(reason)
        self.delay_s = delay_s
        self.reason = reason
        self.count_attempt = count_attempt


def register(kind: str, handler: Handler, *, max_attempts: MaxAttempts = None) -> None:
    if isinstance(max_attempts, str):
        from api.app.config import Settings

        if max_attempts not in Settings.model_fields:
            raise ValueError(f"max_attempts of job kind {kind!r} names no setting: {max_attempts!r}")
    _HANDLERS[kind] = handler
    if max_attempts is None:
        _MAX_ATTEMPTS.pop(kind, None)
    else:
        _MAX_ATTEMPTS[kind] = max_attempts


def unregister(kind: str) -> None:
    _HANDLERS.pop(kind, None)
    _MAX_ATTEMPTS.pop(kind, None)


def job(kind: str, *, max_attempts: MaxAttempts = None) -> Callable[[Handler], Handler]:
    """Decorator: register the function as the handler of ``kind``."""
    def deco(fn: Handler) -> Handler:
        register(kind, fn, max_attempts=max_attempts)
        return fn
    return deco


def kinds_with_own_max_attempts() -> tuple[str, ...]:
    """The kinds that registered their own ``max_attempts``: they opted in to being retried, so the
    reaper requeues them when their worker is lost (see ``api.app.jobs.runner``)."""
    return tuple(_MAX_ATTEMPTS)


def max_attempts_for(kind: str, settings: Settings | None, *, default: int | None = None) -> int:
    """How many attempts ``kind`` gets: its own ``max_attempts`` (a ``Settings`` field is read now),
    else ``default`` when given, else ``settings.job_max_attempts``."""
    own = _MAX_ATTEMPTS.get(kind)
    if isinstance(own, int):
        return own
    if isinstance(own, str) and settings is not None:
        return int(getattr(settings, own))
    if default is not None:
        return default
    if settings is None:
        raise ValueError(f"no settings to read the max attempts of job kind {kind!r} from")
    return settings.job_max_attempts


def handler_for(kind: str) -> Handler:
    try:
        return _HANDLERS[kind]
    except KeyError:
        raise KeyError(f"no handler for job kind {kind!r}") from None
