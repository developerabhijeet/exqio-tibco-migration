"""The architecture page of a process, in Confluence storage format (XHTML), built by code from
stored data only (spec section 4).

Every value is masked (``mask_sensitive``, plus password, secret and token fields) and then
XML-escaped; nothing from a model, a design or a TIBCO project is ever inserted as markup, so a
narrative holding ``<ac:structured-macro>`` or ``<script>`` shows as text. Configuration lists keys
and whether each is a secret, never a value. Diagrams are tables (Confluence renders them natively
and an editor can search them).
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from typing import Any
from xml.sax.saxutils import escape

from pydantic import BaseModel, Field

from engine.agents.provider import mask_sensitive
from engine.stacks import get_stack

#: Field names masked on the page on top of ``SENSITIVE_FIELDS`` (connection strings, keys).
PAGE_MASKED_FIELDS = ("password", "pwd", "secret", "token", "api_key", "access_key")
GATE_NAMES = ("G1", "G2", "G3", "G4", "G5", "G6")
NONE = "<p><em>None.</em></p>"
SECRET_LABEL = "secret (Key Vault; value not shown)"  # fixed text: not masked
_HTTPS = re.compile(r"https://[^\s\"'<>]+")


class PageContext(BaseModel):
    app_name: str
    process_name: str
    ir_id: str
    source_path: str
    design: dict[str, Any]
    design_version: int
    gates: list[dict[str, Any]] = Field(default_factory=list)
    build: dict[str, Any] | None = None
    stack: str = "dotnet"
    iteration: int = 1
    figma: list[dict[str, Any]] = Field(default_factory=list)


def page_title(app: str, process: str) -> str:
    return f"{app} · {process} · Architecture"


def _text(value: Any) -> str:
    """Any value as page text: masked, then escaped (``&``, ``<``, ``>``)."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return escape(mask_sensitive(str(value), PAGE_MASKED_FIELDS))


def _attr(value: str) -> str:
    return escape(mask_sensitive(value, PAGE_MASKED_FIELDS), {'"': "&quot;", "'": "&apos;"})


def _code(value: Any) -> str:
    text = _text(value)
    return f"<code>{text}</code>" if text else ""


def _table(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """A table whose cells are already-escaped fragments; ``None.`` when there is no row."""
    body = [f"<tr>{''.join(f'<td>{cell}</td>' for cell in row)}</tr>" for row in rows]
    if not body:
        return NONE
    head = f"<tr>{''.join(f'<th>{_text(h)}</th>' for h in headers)}</tr>"
    return f"<table><tbody>{head}{''.join(body)}</tbody></table>"


def _lines(values: Iterable[Any]) -> str:
    return "<br/>".join(_text(v) for v in values if v not in (None, ""))


def _paragraphs(text: str) -> str:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return "".join(f"<p>{'<br/>'.join(_text(line) for line in p.splitlines())}</p>" for p in parts)


def _section(title: str, body: str) -> str:
    return f"<h2>{_text(title)}</h2>{body}"


def _rows(design: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [r for r in design.get(key) or [] if isinstance(r, dict)]


# ---------------------------------------------------------------- sections


def _overview(ctx: PageContext) -> str:
    narrative = ctx.design.get("narrative") or ""
    note = (f"<p><em>Generated from design v{ctx.design_version} of {_text(ctx.ir_id)}. "
            "Edits made on this page are replaced on the next publish.</em></p>")
    return note + (_paragraphs(narrative) or "<p><em>No narrative yet.</em></p>")


def _build_file(stack: str, name: str) -> str:
    """The stack's top build file, from its layout hook: ``Solution: <name>.sln`` for .NET,
    ``Build file: pom.xml`` (``go.mod``, ``pyproject.toml``) for the others."""
    try:
        file = get_stack(stack).layout(name).solution_file
    except KeyError:
        return f"Build file: {_code('not recorded')}"
    return f"{'Solution' if file.endswith('.sln') else 'Build file'}: {_code(file)}"


def _layout(ctx: PageContext) -> str:
    solution = ctx.design.get("solution") or {}
    head = f"<p>Target stack: {_code(ctx.stack)}. {_build_file(ctx.stack, str(solution.get('name') or ''))}</p>"
    rows = [(_text(p.get("name")), _text(p.get("kind")), _lines(p.get("files") or []))
            for p in solution.get("projects") or [] if isinstance(p, dict)]
    return head + _table(("Project", "Kind", "Files"), rows)


def _decisions(ctx: PageContext) -> str:
    rows = [(_text(d.get("title")), _text(d.get("choice")), _lines(d.get("alternatives") or []),
             _text(d.get("reason"))) for d in _rows(ctx.design, "decisions")]
    return _table(("Decision", "Choice", "Alternatives", "Reason"), rows)


def _questions(ctx: PageContext) -> str:
    rows = []
    for i, q in enumerate(_rows(ctx.design, "open_questions")):
        verdict = q.get("decision") or "open"
        by = " ".join(x for x in (str(q.get("decided_by") or ""), str(q.get("decided_at") or "")) if x)
        rows.append((str(i), _text(q.get("question")), _text(q.get("recommendation")), _text(verdict),
                     _text(q.get("answer")), _text(by)))
    return _table(("#", "Question", "Recommendation", "Verdict", "Answer", "Decided by"), rows)


def _mapping(ctx: PageContext) -> str:
    rows = [(_text(r.get("activity_name") or r.get("activity_id")), _text(r.get("canonical_type")),
             _text(r.get("pattern")), _text(r.get("project")), _code(r.get("file")), _code(r.get("class_name")),
             _code(r.get("method")), _text(r.get("origin"))) for r in _rows(ctx.design, "mapping")]
    return _table(("Activity", "Type", "Pattern", "Project", "File", "Class", "Method", "Origin"), rows)


def _contracts(ctx: PageContext) -> str:
    rows = [(_code(c.get("contract_id")), _lines(c.get("record_names") or []), _code(c.get("namespace")),
             _code(c.get("file"))) for c in _rows(ctx.design, "contracts")]
    return _table(("Contract", "Records", "Namespace", "File"), rows)


def _configuration(ctx: PageContext) -> str:
    rows = [(_code(c.get("substvar")), _code(c.get("key")),
             SECRET_LABEL if c.get("secret") else "setting")
            for c in _rows(ctx.design, "config")]
    out = "<p>Keys only; values are never published.</p>" + _table(("TIBCO variable", "Key", "Kind"), rows)
    ignored = [(_code(c.get("substvar")), _text(c.get("reason"))) for c in _rows(ctx.design, "config_ignored")]
    if ignored:
        out += "<p>Not carried over:</p>" + _table(("TIBCO variable", "Reason"), ignored)
    return out


def _messaging(ctx: PageContext) -> str:
    rows = [(_text(m.get("kind")), _code(m.get("name")), _text(m.get("direction")))
            for m in _rows(ctx.design, "messaging")]
    return _table(("Kind", "Name", "Direction"), rows)


def _resilience(ctx: PageContext) -> str:
    rows = [(_code(r.get("resource_id")), _text(r.get("timeout_ms")), _text(r.get("retry")),
             _text(r.get("breaker"))) for r in _rows(ctx.design, "resilience")]
    return _table(("Resource", "Timeout (ms)", "Retry", "Circuit breaker"), rows)


def _sequence(ctx: PageContext) -> str:
    rows = [(str(i + 1), _text(s.get("from") or s.get("from_")), _text(s.get("to")), _text(s.get("label")))
            for i, s in enumerate(_rows(ctx.design, "sequence"))]
    return _table(("Step", "From", "To", "Message"), rows)


def _data_model(ctx: PageContext) -> str:
    views = ctx.design.get("views") or {}
    tables = [t for t in views.get("tables") or [] if isinstance(t, dict)]
    if not tables:
        return NONE
    out = []
    for t in tables:
        used = ", ".join(str(v) for v in t.get("verbs") or [])
        acts = ", ".join(str(a) for a in t.get("activity_ids") or [])
        cols = [(_code(c.get("name")), _text(c.get("type")), _text(c.get("role")))
                for c in t.get("columns") or [] if isinstance(c, dict)]
        out.append(f"<h3>{_text(t.get('name'))}</h3><p>Statements: {_text(used) or '-'}; "
                   f"activities: {_text(acts) or '-'}</p>{_table(('Column', 'Type', 'Use'), cols)}")
    return "".join(out)


def _gates(ctx: PageContext) -> str:
    by_name = {str(g.get("gate")): g for g in ctx.gates}
    rows = []
    for name in GATE_NAMES:
        g = by_name.get(name) or {}
        rows.append((_text(name), _text(g.get("state") or "pending"), _text(g.get("decided_by")),
                     _text(g.get("decided_at")), _code(g.get("spec_hash")), _code(g.get("design_hash")),
                     _code(g.get("build_hash")), _code(g.get("evidence_hash"))))
    return _table(("Gate", "State", "Decided by", "Decided at", "Spec hash", "Design hash", "Build hash",
                   "Evidence hash"), rows)


def _figma(ctx: PageContext) -> str:
    items = []
    for f in ctx.figma:
        name = _text(f.get("name") or f.get("node_id") or "frame")
        url = str(f.get("url") or "")
        # only an https URL becomes a link, and it is an attribute value: quotes escaped too
        items.append(f'<li><a href="{_attr(url)}">{name}</a></li>' if _HTTPS.fullmatch(url) else f"<li>{name}</li>")
    return f"<ul>{''.join(items)}</ul>" if items else NONE


def _source(ctx: PageContext) -> str:
    build = ctx.build or {}
    rows = [("TIBCO process", _code(ctx.source_path)), ("Process id", _code(ctx.ir_id)),
            ("Spec hash", _code(ctx.design.get("spec_hash"))),
            ("Design", _text(f"v{ctx.design_version}") + " " + _code(ctx.design.get("design_hash"))),
            ("Build", (_code(build.get("id")) + " " + _text(build.get("status")) + " "
                       + _code(build.get("build_hash"))).strip() if build else _text("not built yet")),
            ("Iteration", _text(ctx.iteration))]
    return _table(("Item", "Value"), [(_text(k), v) for k, v in rows])


SECTIONS = (("Overview", _overview), ("Target stack and solution layout", _layout), ("Decisions", _decisions),
            ("Open questions", _questions), ("Mapping", _mapping), ("Contracts", _contracts),
            ("Configuration", _configuration), ("Messaging", _messaging), ("Resilience", _resilience),
            ("Sequence", _sequence), ("Data model", _data_model), ("Gate record", _gates),
            ("Figma references", _figma), ("Source", _source))


def render_architecture_page(ctx: PageContext) -> str:
    """The page body (storage format), one ``<h2>`` section per line of ``SECTIONS``."""
    return "\n".join(_section(title, build(ctx)) for title, build in SECTIONS) + "\n"


def design_attachment(design: dict[str, Any]) -> bytes:
    """The design record attached to the page as JSON, masked like the page."""
    text = json.dumps(design, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    return mask_sensitive(text, PAGE_MASKED_FIELDS).encode("utf-8")


def attachment_name(design_hash: str) -> str:
    return f"design-{(design_hash or 'unhashed')[:12]}.json"
