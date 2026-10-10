"""Risk Reviewer: business impact for static findings, plus any extra findings with citations."""
from __future__ import annotations

from collections.abc import Callable

from engine.analysis.models import Citation, Finding
from engine.ir.models import Process

from .jsonout import RISK_SCHEMA, coerce_citations
from .prompting import ask_json, render_process, render_prompt
from .routing import RoutedProvider

EvidenceCheck = Callable[[Process, Citation], bool]
_SEVERITIES = ("high", "medium", "low")


def _findings_block(findings: list[Finding]) -> str:
    if not findings:
        return "(none)"
    lines = []
    for f in findings:
        lines.append(f"- id={f.id} severity={f.severity}: {f.text}")
        lines += [f'    cites activity={c.activity_id} kind={c.kind}: "{c.text}"' for c in f.citations]
    return "\n".join(lines)


async def review_risk(
    process: Process,
    static_findings: list[Finding],
    provider: RoutedProvider,
    has_evidence: EvidenceCheck | None = None,
) -> list[Finding]:
    """Return the static findings with ``business_impact`` filled, followed by any new AI
    findings. A new finding is kept only if it has citations and, when ``has_evidence`` is given
    (Task 8's evidence index), every citation is found in the process."""
    system, user = render_prompt(
        "risk",
        process_name=process.name,
        source=render_process(process, []),
        findings=_findings_block(static_findings),
    )
    data, _ = await ask_json(
        provider, "analyse", process, system, user, RISK_SCHEMA,
        tags={"stage": "risk", "process": process.id},
    )
    impacts = {
        str(i["finding_id"]): str(i.get("business_impact", "")).strip()
        for i in data.get("impacts") or []
        if isinstance(i, dict) and i.get("finding_id")
    }
    out = [
        f.model_copy(update={"business_impact": impacts.get(f.id) or f.business_impact})
        for f in static_findings
    ]
    for raw in data.get("findings") or []:
        if not isinstance(raw, dict) or not str(raw.get("text", "")).strip():
            continue
        citations = coerce_citations(raw.get("citations"))
        if not citations or raw.get("severity") not in _SEVERITIES:
            continue
        if has_evidence is not None and not all(has_evidence(process, c) for c in citations):
            continue
        out.append(
            Finding(
                id=f"{process.id}:A{sum(1 for f in out if f.source == 'ai') + 1}",
                severity=raw["severity"],
                text=str(raw["text"]).strip(),
                citations=citations,
                source="ai",
                business_impact=str(raw.get("business_impact", "")).strip() or None,
            )
        )
    return out
