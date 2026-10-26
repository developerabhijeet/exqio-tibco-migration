"""Project expert rules: the CSV of TIBCO modernisation guidance an SME uploads, its validation, and
the prompt section that carries the enabled rules into each phase's system prompt.

Everything here is pure: :func:`parse_csv` turns bytes into a report and valid rows, :func:`section`
renders the rules that apply to a phase, and :func:`prompt_section` is what
:func:`engine.agents.prompting.render_prompt` calls with the rules of the running job (set through
:data:`ACTIVE` by the API's job wrapper). No rules in scope means no section: prompts are unchanged.
"""
from __future__ import annotations

import csv
import io
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

MAX_BYTES = 2 * 1024 * 1024
SECTION_LIMIT = 6000
SEVERITIES = ("Critical", "High", "Medium", "Low")
PHASES = ("discover", "analysis", "design", "build", "verify", "deploy")
RULE_ID = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
SECTION_TITLE = "Project expert rules (these override the default guidance where they conflict)"

#: CSV header -> field. The header is matched case- and whitespace-insensitively.
COLUMNS: dict[str, str] = {
    "Rule ID": "rule_id",
    "Domain": "domain",
    "Best Practice / Rule Name": "name",
    "Severity": "severity",
    "Codified Rule / Expert Guidance": "guidance",
    "Evidence Required": "evidence",
    "Pass Criteria / Validation": "pass_criteria",
    "Primary Agent": "primary_agent",
    "Automated Action / Enforcement": "enforcement",
    "Target Applicability": "applicability",
    "Rule Version": "version",
    "Rule Status": "rule_status",
    "Owner": "owner",
    "Last Reviewed": "last_reviewed",
    "Exceptions / Notes": "notes",
}
REQUIRED = ("Rule ID", "Best Practice / Rule Name", "Severity", "Codified Rule / Expert Guidance")
FIELDS = tuple(COLUMNS.values())
#: Generous per-cell cap so one runaway cell cannot fill the table.
CELL_LIMIT = 4000

#: The prompt templates, by the phase whose rules they receive.
PROMPT_PHASES: dict[str, str] = {
    "domain_namer": "discover",
    "analyst": "analysis", "challenger": "analysis", "risk": "analysis",
    "designer": "design",
    "builder": "build", "repair": "build", "review_apply": "build",
    "reviewer_equivalence": "build", "reviewer_security": "build", "reviewer_standards": "build",
    "reviewer_test": "verify", "test_engineer": "verify",
}


def _key(header: str) -> str:
    return re.sub(r"\s+", "", header or "").lower()


_BY_KEY = {_key(h): f for h, f in COLUMNS.items()}


def template_csv() -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(COLUMNS.keys())
    w.writerow(["TIB-001", "Discovery & Assessment", "Inventory every deployable asset", "Critical",
                "Discover BusinessWorks projects, processes, shared modules, adapters and endpoints.",
                "Asset inventory; repository scan", "All in-scope assets have an owner and disposition",
                "Discovery Agent", "Block completeness sign-off if material assets remain unknown", "All",
                "1.0", "Proposed", "", "", ""])
    return out.getvalue()


def phases_for(agent: str | None) -> tuple[str, ...]:
    """The phases whose prompts carry a rule owned by ``agent`` (the CSV's Primary Agent)."""
    a = (agent or "").lower()
    out: list[str] = []
    if "discover" in a:
        out += ["discover", "analysis"]
    if "tibco" in a or "expert" in a:
        out += ["analysis", "design"]
    if "architect" in a or "design" in a:
        out += ["design"]
    if any(k in a for k in ("transform", "build", "code", "develop")):
        out += ["build"]
    if any(k in a for k in ("test", "qa", "verif", "validat", "governance")):
        out += ["verify"]
    if "security" in a:
        out += ["build", "deploy"]
    if any(k in a for k in ("deploy", "operation", "ops", "infra")):
        out += ["deploy"]
    return tuple(p for p in PHASES if p in out) or PHASES


@dataclass
class ImportReport:
    rows: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    fatal: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"total": self.total, "valid": len(self.rows), "errors": self.errors,
                "warnings": self.warnings, "fatal": self.fatal}


def decode(data: bytes) -> str:
    """UTF-8 (with or without a BOM), else Windows-1252: Excel saves the latter by default."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def parse_csv(data: bytes) -> ImportReport:
    """Validate the uploaded CSV. Rows with an error are left out of ``rows``; ``fatal`` is set
    when nothing can be read (size, header)."""
    rep = ImportReport()
    if len(data) > MAX_BYTES:
        rep.fatal = f"the file is larger than {MAX_BYTES // (1024 * 1024)} MB"
        return rep
    text = decode(data)
    if not text.strip():
        rep.fatal = "the file is empty"
        return rep
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except (StopIteration, csv.Error) as exc:
        rep.fatal = f"cannot read the CSV header: {exc}"
        return rep
    index: dict[str, int] = {}
    for i, h in enumerate(header):
        f = _BY_KEY.get(_key(h))
        if f and f not in index:
            index[f] = i
        elif h.strip() and not f:
            rep.warnings.append({"row": 1, "rule_id": None, "field": h.strip(),
                                 "message": "unknown column, ignored"})
    missing = [h for h in REQUIRED if COLUMNS[h] not in index]
    if missing:
        rep.fatal = "missing required column(s): " + ", ".join(missing)
        return rep
    for h, f in COLUMNS.items():
        if f not in index and h not in REQUIRED:
            rep.warnings.append({"row": 1, "rule_id": None, "field": h, "message": "column missing, left empty"})
    seen: dict[str, int] = {}
    try:
        for n, cells in enumerate(reader, start=2):
            if not any(c.strip() for c in cells):
                continue
            rep.total += 1
            row = {f: (cells[i].strip() if i < len(cells) else "") for f, i in index.items()}
            for f in FIELDS:
                row.setdefault(f, "")
            errs = _row_errors(row, n, seen)
            if errs:
                rep.errors += errs
                continue
            for f, v in row.items():
                if len(v) > CELL_LIMIT:
                    row[f] = v[:CELL_LIMIT]
                    rep.warnings.append({"row": n, "rule_id": row["rule_id"], "field": f,
                                         "message": f"longer than {CELL_LIMIT} characters, cut"})
            seen[row["rule_id"]] = n
            rep.rows.append(row)
    except csv.Error as exc:
        rep.errors.append({"row": reader.line_num, "rule_id": None, "field": None,
                           "message": f"malformed CSV: {exc}"})
    return rep


def _row_errors(row: dict[str, str], n: int, seen: dict[str, int]) -> list[dict[str, Any]]:
    rid = row["rule_id"]
    out: list[dict[str, Any]] = []

    def err(f: str, message: str) -> None:
        out.append({"row": n, "rule_id": rid or None, "field": f, "message": message})

    if not rid:
        err("rule_id", "Rule ID is empty")
    elif not RULE_ID.match(rid):
        err("rule_id", f"Rule ID {rid!r} must look like TIB-001 (capitals, a dash, digits)")
    elif rid in seen:
        err("rule_id", f"duplicate Rule ID (first on row {seen[rid]})")
    sev = next((s for s in SEVERITIES if s.lower() == row["severity"].lower()), None)
    if sev is None:
        err("severity", f"Severity {row['severity']!r} must be one of {', '.join(SEVERITIES)}")
    else:
        row["severity"] = sev
    if not row["name"]:
        err("name", "Rule name is empty")
    if not row["guidance"]:
        err("guidance", "Guidance is empty")
    return out


# ------------------------------------------------------------------ the prompt section
@dataclass
class ActiveRules:
    """The enabled rules of the running job's project, and which of them went into a prompt."""

    rules: list[dict[str, Any]]
    used: dict[str, set[str]] = field(default_factory=dict)  # phase -> rule ids


#: Set by the job wrapper for the duration of a phase job; ``None`` everywhere else.
ACTIVE: ContextVar[ActiveRules | None] = ContextVar("expert_rules_active", default=None)

_RANK = {s: i for i, s in enumerate(SEVERITIES)}


def section(rules: list[dict[str, Any]], phase: str, limit: int = SECTION_LIMIT) -> tuple[str, list[str]]:
    """The prompt section for ``phase`` and the ids it lists ("" and [] when none apply)."""
    picked = [r for r in rules if r.get("enabled", True) and phase in phases_for(r.get("primary_agent"))]
    if not picked:
        return "", []
    picked.sort(key=lambda r: (_RANK.get(r.get("severity") or "", 9), r.get("rule_id") or ""))
    lines: list[str] = []
    ids: list[str] = []
    size = 0
    for r in picked:
        parts = [f"- {r['rule_id']} [{r.get('severity')}] {r.get('name')}: {r.get('guidance')}"]
        if r.get("pass_criteria"):
            parts.append(f"  Pass: {r['pass_criteria']}")
        if r.get("enforcement"):
            parts.append(f"  Enforce: {r['enforcement']}")
        entry = "\n".join(parts)
        if size + len(entry) > limit:
            break
        lines.append(entry)
        ids.append(r["rule_id"])
        size += len(entry) + 1
    left = len(picked) - len(ids)
    if left:
        lines.append(f"({left} lower-priority rule(s) left out for length.)")
    return f"## {SECTION_TITLE}\n" + "\n".join(lines), ids


def prompt_section(prompt_name: str) -> str:
    """The section for ``prompt_name``'s phase from :data:`ACTIVE`, recording the ids it used."""
    active = ACTIVE.get()
    phase = PROMPT_PHASES.get(prompt_name)
    if active is None or not active.rules or phase is None:
        return ""
    text, ids = section(active.rules, phase)
    if ids:
        active.used.setdefault(phase, set()).update(ids)
    return text
