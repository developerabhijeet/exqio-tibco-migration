"""Shared text hygiene for untrusted or secret-bearing text bound for a page, a ticket or a prompt."""
from __future__ import annotations

import re

from engine.agents.provider import mask_sensitive

#: Field names masked on top of ``SENSITIVE_FIELDS`` (connection strings, keys).
MASKED_FIELDS = ("password", "pwd", "secret", "token", "api_key", "access_key")

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  \s]+")


def one_line(value: str, limit: int | None = None) -> str:
    """``value`` masked, on one line (whitespace and control characters collapsed, ``<`` and ``>``
    removed), cut to ``limit`` characters."""
    text = _CONTROL.sub(" ", str(value))  # before masking: a control character cannot split a secret
    out = _CONTROL.sub(" ", mask_sensitive(text, MASKED_FIELDS).replace("<", "").replace(">", "")).strip()
    return out[:limit] if limit else out
