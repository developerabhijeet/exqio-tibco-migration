"""Domain namer: a short business name per candidate group. Code decided the grouping."""
from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any, TypeVar

from .jsonout import DOMAIN_SCHEMA, JsonOutputError
from .prompting import ask_json, render_prompt, wrap_source
from .provider import ProviderError
from .routing import RoutedProvider

C = TypeVar("C")


def _with_name(candidate: Any, name: str) -> Any:
    if hasattr(candidate, "model_copy"):
        return candidate.model_copy(update={"suggested_name": name})
    return dataclasses.replace(candidate, suggested_name=name)


def _block(candidates: Sequence[Any]) -> str:
    lines = []
    for c in candidates:
        lines.append(f"id={c.id} current_name={c.suggested_name} processes={','.join(c.process_ids)}")
        lines += [f"  {e.kind}: {e.text}" for e in c.evidence]
    return "\n".join(lines)


async def name_domains(candidates: Sequence[C], provider: RoutedProvider) -> list[C]:
    """Return copies of ``candidates`` with ``suggested_name`` replaced where the model gave a
    name for that candidate id. If the model is unavailable or its reply is unusable, the names
    code suggested stay."""
    if not candidates:
        return []
    system, user = render_prompt("domain_namer", source=wrap_source(_block(candidates)))
    try:
        data, _ = await ask_json(
            provider, "name", None, system, user, DOMAIN_SCHEMA, tags={"stage": "name"}
        )
    except (ProviderError, JsonOutputError):
        return list(candidates)
    names = {
        str(d["id"]): str(d["name"]).strip()
        for d in data.get("domains") or []
        if isinstance(d, dict) and d.get("id") and str(d.get("name", "")).strip()
    }
    return [_with_name(c, names[c.id]) if c.id in names else c for c in candidates]
