"""Solution Designer: the narrative, decisions and open questions around a design skeleton.

Code fixed the mapping (``engine.dotnet_gen.design``); the model only writes the advice on top of
it. The three fields it fills are in ``HASH_EXCLUDED``, so the design hash cannot move, and plain
code checks that it did not.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from engine.analysis.models import Finding, Rule
from engine.dotnet_gen.models import (
    HASH_EXCLUDED,
    Decision,
    DesignRecord,
    OpenQuestion,
    compute_design_hash,
)
from engine.ir.models import Contract, Process

from .prompting import ask_json, memory_block, render_process, render_prompt
from .provider import MemoryItem

log = logging.getLogger(__name__)

NOT_A_DECISION = "not a decision"

DESIGN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "narrative": {"type": "string"},
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "choice": {"type": "string"},
                    "alternatives": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["title", "choice", "alternatives", "reason"],
            },
        },
        "open_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": ["question", "recommendation"],
            },
        },
    },
    "required": ["narrative", "decisions", "open_questions"],
}


class DesignHashChanged(RuntimeError):
    """The narrative step changed what code decided. Never expected; raised instead of saved."""


# ------------------------------------------------------------ shared renderers


def rules_block(rules: list[Rule], include_rejected: bool = False) -> str:
    """Rule id, status, text and verbatim citations. Rejected rules are left out by default."""
    lines: list[str] = []
    for r in rules:
        if r.status == "rejected" and not include_rejected:
            continue
        lines.append(f"- id={r.id} status={r.status}: {r.text}")
        lines += [f'    cites activity={c.activity_id} kind={c.kind}: "{c.text}"' for c in r.citations]
    return "\n".join(lines) or "(none)"


def findings_block(findings: list[Finding]) -> str:
    lines: list[str] = []
    for f in findings:
        decision = f.decision or "undecided"
        lines.append(f"- id={f.id} severity={f.severity} decision={decision}: {f.text}")
        if f.note:
            lines.append(f"    note: {f.note}")
        lines += [f'    cites activity={c.activity_id} kind={c.kind}: "{c.text}"' for c in f.citations]
    return "\n".join(lines) or "(none)"


def skeleton_text(design: DesignRecord) -> str:
    """The part of the design record code decided, as JSON (the model's advice is left out)."""
    payload = design.model_dump(mode="json", exclude=set(HASH_EXCLUDED), by_alias=True)
    return json.dumps(payload, indent=1, ensure_ascii=False)


# ------------------------------------------------------------ parsing


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if isinstance(value, (str, int, float)) else ""


def _decisions(raw: Any) -> list[Decision]:
    out: list[Decision] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        title, choice = _text(item.get("title")), _text(item.get("choice"))
        reason = str(item.get("reason") or "").strip()
        alternatives = [
            _text(a) for a in item.get("alternatives") or [] if _text(a)
        ] if isinstance(item.get("alternatives"), list) else []
        # A decision names what was chosen, what was not and why; anything less is dropped.
        if not (title and choice and reason and alternatives):
            log.info("designer: dropped a decision without choice, alternative or reason")
            continue
        out.append(Decision(title=title, choice=choice, alternatives=alternatives, reason=reason))
    return out


def _open_questions(raw: Any) -> list[OpenQuestion]:
    out: list[OpenQuestion] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        rec = str(item.get("recommendation") or "").strip() or "none given."
        if NOT_A_DECISION not in rec.lower():
            rec = f"Recommendation, {NOT_A_DECISION}: {rec}"
        out.append(OpenQuestion(question=question, recommendation=rec))
    return out


# ------------------------------------------------------------ agent


async def design_narrative(
    design: DesignRecord,
    process: Process,
    rules: list[Rule],
    findings: list[Finding],
    memory_items: list[MemoryItem],
    provider: Any,
    contracts: list[Contract] | None = None,
) -> DesignRecord:
    """Return a copy of ``design`` with ``narrative``, ``decisions`` and ``open_questions`` filled.

    The mapping and every other field code decided are carried over unchanged; the design hash is
    recomputed and compared, and :class:`DesignHashChanged` is raised if it moved."""
    before = compute_design_hash(design)
    if design.design_hash and design.design_hash != before:
        raise DesignHashChanged(
            f"design record {design.process_id} does not match its stored hash before narration"
        )
    system, user = render_prompt(
        "designer",
        process_name=process.name,
        source=render_process(process, contracts or []),
        design=skeleton_text(design),
        rules=rules_block(rules),
        findings=findings_block(findings),
        memory=memory_block(memory_items),
    )
    data, _ = await ask_json(
        provider, "design", process, system, user, DESIGN_SCHEMA,
        tags={"stage": "design", "process": process.id},
    )
    narrative = str(data.get("narrative") or "").strip() or None
    out = design.model_copy(deep=True, update={
        "narrative": narrative,
        "decisions": _decisions(data.get("decisions")),
        "open_questions": _open_questions(data.get("open_questions")),
    })
    after = compute_design_hash(out)
    if after != before or out.mapping != design.mapping:
        raise DesignHashChanged(f"the narrative changed the design of {design.process_id}")
    return out
