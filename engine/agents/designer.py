"""Solution Designer: the narrative, decisions and open questions around a design skeleton.

Code fixed the mapping (``engine.dotnet_gen.design``); the model only writes the advice on top of
it. The three fields it fills are in ``HASH_EXCLUDED``, so the design hash cannot move, and plain
code checks that it did not.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
from .provider import MemoryItem, mask_sensitive

log = logging.getLogger(__name__)

NOT_A_DECISION = "not a decision"
MAX_PROPOSED_RULES = 10
PROPOSED_RULE_LIMIT = 1000
_MARKER = re.compile(r"\[S\d+\]")

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
# Only asked for (and only read) when the design runs in an iteration.
PROPOSED_RULES_SCHEMA: dict = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"text": {"type": "string"}, "rationale": {"type": "string"}},
        "required": ["text", "rationale"],
    },
}


class IterationItem(BaseModel):
    """One input of an iteration as the designer reads it (an ``api`` ``FailureItem`` or an
    ``iteration_inputs`` row validates into it by attribute)."""

    model_config = ConfigDict(from_attributes=True)

    kind: str
    ref: str | None = None
    title: str
    text: str = ""


class IterationContext(BaseModel):
    """What an iteration brings to the Design job: what failed and the updated requirements."""

    n: int
    inputs: list[IterationItem] = Field(default_factory=list)
    requirements: str = ""


class ProposedRule(BaseModel):
    text: str
    rationale: str = ""


class DesignResult(BaseModel):
    design: DesignRecord
    proposed_rules: list[ProposedRule] = Field(default_factory=list)


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


def _ref_line(value: Any, limit: int) -> str:
    """One line: whitespace and control characters collapsed, angle brackets removed."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\s]+", " ", str(value or "").replace("<", "").replace(">", "")).strip()[:limit]


def references_block(references: list[dict[str, Any]] | None) -> str:
    """Design references (Figma frames an architect attached), one line per frame: name, page, link
    and screen text. Every value is made single-line here too, so text cannot start another entry."""
    lines: list[str] = []
    for r in references or []:
        where = f" (page {_ref_line(r.get('page'), 120)})" if r.get("page") else ""
        parts = [_ref_line(x, 4000) for x in str(r.get("text") or "").splitlines()]
        text = " | ".join(x for x in parts if x)
        url = _ref_line(r.get("url"), 500)
        lines.append(f"- frame {_ref_line(r.get('name'), 120) or 'unnamed'}{where}" + (f", {url}" if url else "")
                     + (f": {text}" if text else ""))
    return "\n".join(lines) or "(none)"


def _data(text: Any) -> str:
    """Untrusted text for the iteration block: literal citation markers stripped (until none is
    left, so ``[[S1]S2]`` cannot leave one) and masked again."""
    out = str(text or "")
    while True:
        stripped = _MARKER.sub("", out)
        if stripped == out:
            return mask_sensitive(out)
        out = stripped


def iteration_block(iteration: IterationContext | None) -> str:
    """The ``Iteration n`` block (empty without an iteration): each input with its kind, title and
    excerpt, then the updated requirements. ``render_prompt`` wraps it in ``<source>``."""
    if iteration is None:
        return ""
    lines = [f"Iteration {iteration.n}", "", "What failed (from the previous iteration and uploaded logs):"]
    for i, item in enumerate(iteration.inputs, 1):
        ref = f" ref={_ref_line(_data(item.ref), 120)}" if item.ref else ""
        lines.append(f"- input {i} kind={_ref_line(item.kind, 40)}{ref}: {_ref_line(_data(item.title), 200)}")
        lines += [f"    {line}" for line in _data(item.text).splitlines()]
    if not iteration.inputs:
        lines.append("(none)")
    lines += ["", "Updated requirements:", _data(iteration.requirements).strip() or "(none)"]
    return "\n".join(lines)


#: The inputs the Code Builder, Test Engineer and repair loops read as hints (not plan or parity).
BUILD_INPUT_KINDS = frozenset({"build_failure", "test_failure", "uploaded_log"})


def build_iteration_block(iteration: IterationContext | None) -> str:
    """The ``Iteration n`` block for the build-side agents: only build failures, failing tests and
    uploaded logs, and the requirements (empty without an iteration)."""
    if iteration is None:
        return ""
    return iteration_block(iteration.model_copy(update={
        "inputs": [i for i in iteration.inputs if i.kind in BUILD_INPUT_KINDS]}))


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


def _proposed_rules(raw: Any) -> list[ProposedRule]:
    """At most ``MAX_PROPOSED_RULES``, each text on one line, masked and at most
    ``PROPOSED_RULE_LIMIT`` characters (a longer one is dropped, not cut: half a rule is another
    rule); duplicates by text are dropped."""
    out: list[ProposedRule] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = mask_sensitive(_text(_data(item.get("text"))))
        rationale = mask_sensitive(_text(_data(item.get("rationale"))))[:PROPOSED_RULE_LIMIT]
        if not text or len(text) > PROPOSED_RULE_LIMIT or text.lower() in seen:
            log.info("designer: dropped a proposed rule that is empty, too long or repeated")
            continue
        seen.add(text.lower())
        out.append(ProposedRule(text=text, rationale=rationale))
        if len(out) == MAX_PROPOSED_RULES:
            break
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
    references: list[dict[str, Any]] | None = None,
    stack: str = "dotnet",
    iteration: IterationContext | None = None,
) -> DesignRecord:
    """Return a copy of ``design`` with ``narrative``, ``decisions`` and ``open_questions`` filled
    (see :func:`design_with_proposals`, which also returns the proposed rules)."""
    result = await design_with_proposals(design, process, rules, findings, memory_items, provider,
                                         contracts, references, iteration, stack=stack)
    return result.design


async def design_with_proposals(
    design: DesignRecord,
    process: Process,
    rules: list[Rule],
    findings: list[Finding],
    memory_items: list[MemoryItem],
    provider: Any,
    contracts: list[Contract] | None = None,
    references: list[dict[str, Any]] | None = None,
    iteration: IterationContext | None = None,
    stack: str = "dotnet",
) -> DesignResult:
    """The narrated design and, in an iteration, the rules the designer proposes.

    The mapping and every other field code decided are carried over unchanged; the design hash is
    recomputed and compared, and :class:`DesignHashChanged` is raised if it moved. ``stack`` picks
    the prompt pack (the design's own ``stack``). ``iteration`` adds the ``Iteration n`` block
    (inside ``<source>``) and asks for ``proposed_rules``."""
    before = compute_design_hash(design)
    if design.design_hash and design.design_hash != before:
        raise DesignHashChanged(
            f"design record {design.process_id} does not match its stored hash before narration"
        )
    system, user = render_prompt(
        "designer", stack,
        process_name=process.name,
        source=render_process(process, contracts or []),
        design=skeleton_text(design),
        rules=rules_block(rules),
        findings=findings_block(findings),
        memory=memory_block(memory_items),
        references=references_block(references),
        iteration=iteration_block(iteration),
    )
    schema = DESIGN_SCHEMA
    if iteration is not None:
        schema = {**DESIGN_SCHEMA, "properties": {**DESIGN_SCHEMA["properties"],
                                                  "proposed_rules": PROPOSED_RULES_SCHEMA}}
    data, _ = await ask_json(
        provider, "design", process, system, user, schema,
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
    proposed = _proposed_rules(data.get("proposed_rules")) if iteration is not None else []
    return DesignResult(design=out, proposed_rules=proposed)
