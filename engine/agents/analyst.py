"""Workflow Analyst: summary and numbered business rules, each with verbatim citations."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from engine.analysis.models import Rule
from engine.ir.models import Contract, Process

from .jsonout import ANALYST_SCHEMA, as_float, coerce_citations
from .prompting import ask_json, memory_block, render_process, render_prompt
from .provider import MemoryItem
from .routing import RoutedProvider

Verifier = Callable[[Process, list[Rule]], Any]


class AnalystOutput(BaseModel):
    summary: str
    rules: list[Rule]
    technical: dict[str, str] = Field(default_factory=dict)


def _technical(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    out: dict[str, str] = {}
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and item.get("ref"):
            out[str(item["ref"])] = str(item.get("reason", ""))
    return out


async def analyse(
    process: Process,
    contracts: list[Contract],
    memory_items: list[MemoryItem],
    provider: RoutedProvider,
    verifier: Verifier | None = None,
) -> AnalystOutput:
    """Ask the model for rules, then let plain code decide their standing.

    A rule with no well-formed citation is marked ``unverified`` here. When ``verifier`` is
    given (Task 8's ``verify_citations``) it runs next and may mark more rules in place.
    """
    system, user = render_prompt(
        "analyst",
        process_name=process.name,
        source=render_process(process, contracts),
        memory=memory_block(memory_items),
    )
    data, _ = await ask_json(
        provider, "analyse", process, system, user, ANALYST_SCHEMA,
        tags={"stage": "analyse", "process": process.id},
    )
    known_memory = {m.id for m in memory_items}
    rules: list[Rule] = []
    for raw in data.get("rules") or []:
        if not isinstance(raw, dict) or not str(raw.get("text", "")).strip():
            continue
        citations = coerce_citations(raw.get("citations"))
        refs = [r for r in raw.get("memory_refs") or [] if isinstance(r, str) and r in known_memory]
        rules.append(
            Rule(
                id=f"{process.id}:R{len(rules) + 1}",
                text=str(raw["text"]).strip(),
                citations=citations,
                confidence=min(max(as_float(raw.get("confidence")), 0.0), 1.0),
                status="draft" if citations else "unverified",
                memory_refs=refs,
            )
        )
    if verifier is not None:
        verifier(process, rules)
    return AnalystOutput(
        summary=str(data.get("summary", "")).strip(),
        rules=rules,
        technical=_technical(data.get("technical")),
    )
