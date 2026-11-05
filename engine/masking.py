"""The mask every process applies before text is shown to a client or stored for recall: SSNs and
the values of sensitive fields, the defaults of ``engine.agents.provider.mask_sensitive`` plus the
field names the deployment configures (``Settings.sensitive_fields``). Each process (the API at
``create_app``, the worker at start) calls ``configure`` once; until then only the defaults apply.
The patterns themselves live in ``mask_sensitive``; this module only carries the configuration."""
from __future__ import annotations

from collections.abc import Iterable

from engine.agents.provider import mask_sensitive

_EXTRA: tuple[str, ...] = ()


def configure(fields: Iterable[str]) -> None:
    """Mask ``fields`` too, from now on, in this process."""
    global _EXTRA
    _EXTRA = tuple(f for f in fields if f)


def configured() -> tuple[str, ...]:
    """The configured field names (beyond the defaults)."""
    return _EXTRA


def mask(text: str) -> str:
    """``text`` with SSNs and the values of the default and configured sensitive fields masked."""
    return mask_sensitive(text, _EXTRA)
