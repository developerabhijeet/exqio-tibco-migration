"""Independent Challenger: agree or disagree per rule, from the IR and the rules alone."""
from __future__ import annotations

from engine.analysis.models import Challenge, Rule
from engine.ir.models import Contract, Process

from .jsonout import CHALLENGE_SCHEMA
from .prompting import ask_json, render_process, render_prompt
from .routing import RoutedProvider


def _rules_block(rules: list[Rule]) -> str:
    """Rule id, text and citations only. The analyst's summary, confidence and memory
    references are part of its reasoning and stay out."""
    lines = []
    for r in rules:
        lines.append(f"- id={r.id}: {r.text}")
        lines += [f'    cites activity={c.activity_id} kind={c.kind}: "{c.text}"' for c in r.citations]
        if not r.citations:
            lines.append("    cites: nothing")
    return "\n".join(lines) or "(none)"


async def challenge(
    process: Process,
    rules: list[Rule],
    provider: RoutedProvider,
    contracts: list[Contract] | None = None,
) -> list[Rule]:
    """Return copies of ``rules`` with ``challenge`` filled. A rule the model gave no valid
    verdict for is left unchallenged. Disagreement never changes a rule's status."""
    system, user = render_prompt(
        "challenger",
        process_name=process.name,
        source=render_process(process, contracts or []),
        rules=_rules_block(rules),
    )
    data, resp = await ask_json(
        provider, "challenge", process, system, user, CHALLENGE_SCHEMA,
        tags={"stage": "challenge", "process": process.id},
        tier="strong",
    )
    verdicts = {
        str(v["rule_id"]): v
        for v in data.get("verdicts") or []
        if isinstance(v, dict) and v.get("rule_id") and v.get("verdict") in ("agree", "disagree")
    }
    out: list[Rule] = []
    for r in rules:
        v = verdicts.get(r.id)
        ch = None
        if v:
            ch = Challenge(
                verdict=v["verdict"], reason=str(v.get("reason", "")).strip(), model=resp.model
            )
        out.append(r.model_copy(update={"challenge": ch}))
    return out
