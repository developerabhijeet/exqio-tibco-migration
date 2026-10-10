"""Review panel: equivalence, security and standards reviewers over the agent-written code.

Each reviewer sees the diff (the code a model wrote, with line numbers) and the spec pack (the
design skeleton, the rules and the findings), never the whole repository. Plain code keeps only
findings that point at a reviewed file, checks the line and normalises the triage.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from engine.analysis.models import Finding, Rule
from engine.dotnet_gen.generator import GeneratedSolution
from engine.dotnet_gen.models import DesignRecord
from engine.ir.models import Process

from .builder import is_test_path
from .designer import findings_block, rules_block, skeleton_text
from .prompting import ask_json, render_process, render_prompt

Reviewer = Literal["equivalence", "security", "standards"]
Triage = Literal["patch", "defer", "decision"]
REVIEWERS: tuple[Reviewer, ...] = ("equivalence", "security", "standards")
TRIAGES: tuple[Triage, ...] = ("patch", "defer", "decision")
# Reviews have no task kind of their own in the routing table; they are independent second
# opinions on generated work, routed like the challenger.
REVIEW_TASK_KIND = "challenge"

REVIEW_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "text": {"type": "string"},
                    "triage": {"enum": list(TRIAGES)},
                },
                "required": ["file", "line", "text", "triage"],
            },
        }
    },
    "required": ["findings"],
}


class ReviewFinding(BaseModel):
    reviewer: Reviewer
    file: str
    line: int  # 1-based; 0 when the reviewer gave no line inside the file
    text: str
    triage: Triage


def reviewed_files(solution: GeneratedSolution) -> dict[str, str]:
    """The agent-written production files: what a model changed."""
    return {
        f.path: f.content for f in solution.files
        if f.origin == "agent" and not is_test_path(f.path)
    }


def numbered(files: dict[str, str]) -> str:
    out: list[str] = []
    for path, text in files.items():
        out.append(f"=== {path}")
        out += [f"{i:>4} | {line}" for i, line in enumerate(text.splitlines(), 1)]
    return "\n".join(out) or "(no agent-written files)"


def _line(value: Any, length: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if 1 <= n <= length else 0


def _coerce(reviewer: Reviewer, data: dict, files: dict[str, str]) -> list[ReviewFinding]:
    out: list[ReviewFinding] = []
    for item in data.get("findings") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("file") or "").strip().removeprefix("./")
        text = str(item.get("text") or "").strip()
        if path not in files or not text:
            continue
        triage = item.get("triage")
        out.append(ReviewFinding(
            reviewer=reviewer,
            file=path,
            line=_line(item.get("line"), len(files[path].splitlines())),
            text=text,
            # an unknown triage goes to a person
            triage=triage if triage in TRIAGES else "decision",
        ))
    return out


async def review_panel(
    solution: GeneratedSolution,
    design: DesignRecord,
    rules: list[Rule],
    findings: list[Finding],
    provider: Any,
    *,
    process: Process | None = None,
    diff: str | None = None,
) -> list[ReviewFinding]:
    """Run the three reviewers in turn and return their findings, equivalence first.

    ``diff`` overrides the default view of the change (the numbered agent-written files); findings
    are still kept only for those files. ``process`` adds the TIBCO source for the equivalence
    comparison; without it the rule citations are the source."""
    files = reviewed_files(solution)
    code = diff if diff is not None else numbered(files)
    if process is not None:
        source = render_process(process, [])
    else:
        source = "TIBCO source not supplied; compare against the citations of the rules."
    spec = skeleton_text(design)
    out: list[ReviewFinding] = []
    for reviewer in REVIEWERS:
        system, user = render_prompt(
            f"reviewer_{reviewer}",
            process_name=process.name if process is not None else design.process_id,
            source=source,
            design=spec,
            rules=rules_block(rules),
            findings=findings_block(findings),
            code=code,
        )
        data, _ = await ask_json(
            provider, REVIEW_TASK_KIND, process, system, user, REVIEW_SCHEMA,
            tags={"stage": "review", "reviewer": reviewer, "process": design.process_id},
            tier=None if process is not None else "standard",
        )
        out += _coerce(reviewer, data, files)
    return out
