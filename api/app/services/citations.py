"""Inline citation spans (``cited``) for the Discover domains, the Validate rules and findings,
the Design narrative and decisions, the Build review findings, the Verify parity differences and
the Readiness rows.

Each claim keeps every citation it carries: a ref anchors on the first sentence naming its
activity (or an identifier its quote holds), else on the last sentence (``engine.citations.claims``).
Text is masked before anchoring, so offsets stay true to the text shown: ``cited.text`` is the
MASKED text and may differ from the raw field it restates; the spans index into ``cited.text``
(code points), so clients render ``cited.text`` whenever ``cited`` is present.

Entity names (activities, rules, findings, tables, queues) only ever come from the payload's own
process; a single common word (``Map``, ``dealer``) is never a hint."""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

from engine.agents.provider import MASK, mask_sensitive
from engine.citations.claims import HintedRef, anchor, link_entities, tail
from engine.citations.model import (
    LABEL_MAX,
    PREVIEW_MAX,
    CitedText,
    CiteRef,
    CiteTarget,
    UiPhase,
    cut,
)
from engine.citations.uids import activity_uid, finding_chunk_uid, rule_chunk_uid
from engine.domains.discover import activity_messages, activity_sql, sql_tables
from engine.ir.models import Activity

# The field names a deployment adds to the default sensitive ones (Settings.sensitive_fields, set
# at app start). Every text a client shows from these builders, and every value the API shows on
# their behalf (parity values, Deploy evidence lines), is masked with them here; the browser never
# masks anything itself.
_EXTRA_SENSITIVE: tuple[str, ...] = ()


def configure_masking(fields: Iterable[str]) -> None:
    """Use ``fields`` as the configured sensitive field names from now on (the app calls it at
    start with ``Settings.sensitive_fields``)."""
    global _EXTRA_SENSITIVE
    _EXTRA_SENSITIVE = tuple(f for f in fields if f)


def mask(text: str) -> str:
    """``text`` masked for display: SSNs and the values of the default and configured sensitive
    fields (``engine.agents.provider.mask_sensitive``)."""
    return mask_sensitive(text, _EXTRA_SENSITIVE)


def mask_lines(text: str) -> str:
    """``text`` masked line by line, so line ``n`` of the result is line ``n`` of ``text`` masked
    (an element spanning lines is not collapsed): for evidence read from a line of a file."""
    return "\n".join(mask(line) for line in text.split("\n"))


_WORD = re.compile(r"[A-Za-z_][\w]*")
# An identifier, not a common word: snake case, a digit, or a capital after the first letter.
_IDENTIFIER = re.compile(r"_|\d|.[A-Z]")

# Queues and topics read as one sentence.
_SAME_SENTENCE = {"topic": "queue"}
_DOMAIN_SENTENCE = {
    "table": "Shares the tables {}.",
    "queue": "Uses the queues {}.",
    "call": "Calls between {}.",
    "module": "Lives in the modules {}.",
    "name": "Named alike: {}.",
}


def _identifier_like(name: str) -> bool:
    """Multi-word names and identifiers anchor; a single common word (``Map``, ``Check``)
    would anchor on any prose that uses the word, so it does not."""
    name = name.strip()
    return bool(name) and (len(name.split()) > 1 or bool(_IDENTIFIER.search(name)))


def hints_for(activity_id: str | None, activity_name: str | None, quote: str | None) -> list[str]:
    """The names that put a citation's chip on a sentence: the activity id and name, then the
    identifiers quoted in the citation text; common single words are left out."""
    names = [activity_id or "", activity_name or "", *_WORD.findall(quote or "")]
    return list(dict.fromkeys(n.strip() for n in names if _identifier_like(n)))


def activity_ref(ref_id: str, process_id: str, ir_id: str, activity_id: str, activity_name: str,
                 quote: str, *, verified: bool | None, reason: str | None,
                 phase: UiPhase) -> CiteRef:
    return CiteRef(
        id=ref_id, phase=phase, kind="activity",
        label=cut(mask(activity_name or activity_id or "activity"), LABEL_MAX),
        preview=cut(mask(quote or ""), PREVIEW_MAX),
        memory_uid=activity_uid(ir_id, activity_id) if activity_id else None,
        verified=verified, reason=reason,
        target=CiteTarget(kind="activity", process_id=process_id, activity_id=activity_id or None))


def activity_names(process: Any) -> dict[str, str]:
    """Activity id to name from ``Process.ir``: build it once per view, pass it to each claim."""
    return {a.get("id"): a.get("name") or "" for a in (process.ir or {}).get("activities") or []}


def _cite_claim(key: str, text: str | None, citations: list[Any], process: Any,
                names: dict[str, str] | None) -> CitedText:
    """``cited.text`` is the masked claim text and every span indexes into it."""
    names = activity_names(process) if names is None else names
    hinted = []
    for i, c in enumerate(citations):
        c = c if isinstance(c, dict) else c.model_dump()
        aid = c.get("activity_id") or ""
        name = names.get(aid, "")
        ref = activity_ref(f"c{i + 1}", process.id, process.ir_id, aid, name, c.get("text") or "",
                           verified=c.get("verified"), reason=c.get("reason"), phase="validate")
        hinted.append(HintedRef(ref=ref, hints=hints_for(aid, name, c.get("text"))))
    text = mask(text or "")
    if not text.strip():
        return tail(key, [h.ref for h in hinted])
    return anchor(text, hinted)


def cite_rule(rule: Any, process: Any, citations: list[dict[str, Any]] | None = None, *,
              names: dict[str, str] | None = None) -> CitedText:
    """``citations`` overrides ``rule.citations`` (the view's, carrying ``verified``/``reason``);
    ``names`` is ``activity_names(process)``, built once per view."""
    return _cite_claim(rule.rule_key, rule.text,
                       rule.citations or [] if citations is None else citations, process, names)


def cite_finding(finding: Any, process: Any, *, names: dict[str, str] | None = None) -> CitedText:
    return _cite_claim(finding.finding_key, finding.text, finding.citations or [], process, names)


def cite_domain(domain: Any) -> CitedText:
    """One rationale sentence per evidence kind (queues and topics share one), in order of first
    appearance; one ref per evidence item, hinted by its masked text: it anchors on the first
    sentence that names that text (its own kind's, unless an earlier sentence also holds it),
    else on the last sentence."""
    items = [(str(e.get("kind") or ""), mask(str(e.get("text") or "")))
             for e in domain.evidence or []]
    by_kind: dict[str, list[str]] = {}
    for kind, text in items:
        by_kind.setdefault(_SAME_SENTENCE.get(kind, kind), []).append(text)
    sentences = []
    for kind, texts in by_kind.items():
        template = _DOMAIN_SENTENCE.get(kind) or f"{(kind or 'evidence').capitalize()}: {{}}."
        sentences.append(template.format(", ".join(texts)))
    rationale = " ".join(sentences)
    hinted = [HintedRef(
        ref=CiteRef(id=f"c{i + 1}", phase="discover", kind="evidence",
                    label=cut(text or kind or "evidence", LABEL_MAX), preview=cut(text, PREVIEW_MAX),
                    target=CiteTarget(kind="step", step="discover")),
        hints=[text]) for i, (kind, text) in enumerate(items)]
    return anchor(rationale, hinted) if hinted else CitedText.plain(rationale)


# ------------------------------------------------------------------ design
def _entity_name(name: str) -> bool:
    """A name worth linking: an identifier (see ``_identifier_like``) or a dotted or slashed
    destination (``gmf.funding.request``)."""
    return _identifier_like(name) or bool(re.search(r"\w[./]\w", name or ""))


def _unique(hinted: list[HintedRef]) -> list[HintedRef]:
    """One ref per entity: an activity named by both its id and its name gets one chip, on the
    first mention."""
    seen: set[str] = set()
    out = []
    for h in hinted:
        key = h.ref.model_dump_json(exclude={"id"})
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out


def _ir_resources(process: Any) -> tuple[list[str], list[str]]:
    """The table and queue (or topic) names the process's activities touch, in IR order."""
    tables: dict[str, None] = {}
    queues: dict[str, None] = {}
    for raw in (process.ir or {}).get("activities") or []:
        try:
            act = Activity.model_validate(raw)
        except ValueError:
            continue
        for ref in sql_tables(activity_sql(act) or ""):
            tables.setdefault(ref.name)
        for m in activity_messages(act):
            queues.setdefault(m.destination)
    starter = (process.ir or {}).get("starter") or {}
    dest = (starter.get("config") or {}).get("destination") if starter.get("kind") == "jms" else None
    if isinstance(dest, str) and dest:
        queues.setdefault(dest)
    return list(tables), list(queues)


def design_entities(process: Any, design: dict[str, Any], rules: list[Any],
                    findings: list[Any]) -> dict[str, CiteRef]:
    """The names the design prose may mention, each with its ref: this process's activities (by id
    and by name), its rule and finding keys, and the tables and queues its IR touches. ``design``
    is accepted for the mapping's activity names when the IR has none."""
    out: dict[str, CiteRef] = {}

    def add(name: str | None, ref: CiteRef) -> None:
        name = (name or "").strip()
        if _entity_name(name) and name not in out:
            out[name] = ref

    names = activity_names(process)
    for row in design.get("mapping") or []:
        names.setdefault(row.get("activity_id"), row.get("activity_name") or "")
    types = {a.get("id"): a.get("type") or "" for a in (process.ir or {}).get("activities") or []}
    for aid, name in names.items():
        if not aid:
            continue
        ref = activity_ref("c1", process.id, process.ir_id, aid, name, types.get(aid, ""),
                           verified=None, reason=None, phase="design")
        add(aid, ref)
        add(name, ref)
    for kind, items, key_of, uid_of in (("rule", rules, "rule_key", rule_chunk_uid),
                                        ("finding", findings, "finding_key", finding_chunk_uid)):
        for item in items:
            key = getattr(item, key_of, None) if not isinstance(item, dict) else item.get(key_of)
            text = getattr(item, "text", None) if not isinstance(item, dict) else item.get("text")
            if not key:
                continue
            add(key, CiteRef(
                id="c1", phase="validate", kind=kind, label=cut(mask(key), LABEL_MAX),
                preview=cut(mask(text or ""), PREVIEW_MAX),
                memory_uid=uid_of(process.ir_id, key),
                target=CiteTarget(kind="step", step="validate", process_id=process.id)))
    tables, queues = _ir_resources(process)
    for kind, label in (("table", "Table"), ("queue", "Queue")):
        for name in tables if kind == "table" else queues:
            add(name, CiteRef(id="c1", phase="discover", kind=kind, label=cut(name, LABEL_MAX),
                              preview=cut(f"{label} {name}, used by this process", PREVIEW_MAX),
                              target=CiteTarget(kind="step", step="discover", process_id=process.id)))
    return out


def _cite_prose(text: str, entities: dict[str, CiteRef]) -> CitedText:
    text = mask(text or "")
    hinted = _unique(link_entities(text, entities)) if text.strip() else []
    return anchor(text, hinted) if hinted else CitedText.plain(text)


def _decision_text(choice: str | None, reason: str | None) -> str:
    """``"{choice}. {reason}"`` without a doubled period when the choice already ends a sentence,
    and without a stray ``". "`` when either part is empty."""
    choice, reason = (choice or "").strip(), (reason or "").strip()
    if not choice or not reason:
        return choice or reason
    return f"{choice} {reason}" if choice[-1] in ".!?" else f"{choice}. {reason}"


def cite_design(design: dict[str, Any], entities: dict[str, CiteRef]) -> dict[str, Any]:
    """``design`` plus ``narrative_cited`` (``None`` without a narrative) and a ``cited`` on each
    decision: ``"{choice}. {reason}"`` (see ``_decision_text``) with a span on each sentence naming an entity. A decision
    naming nothing has no spans and no refs (plain text)."""
    narrative = design.get("narrative")
    decisions = [
        {**d, "cited": _cite_prose(_decision_text(d.get("choice"), d.get("reason")), entities)
         .model_dump(mode="json")}
        for d in design.get("decisions") or []]
    return {**design, "decisions": decisions,
            "narrative_cited": _cite_prose(narrative, entities).model_dump(mode="json")
            if narrative else None}


# ------------------------------------------------------------------ build reviews
def cite_review(finding: dict[str, Any], build_id: str, process: Any,
                tibco_ref: dict[str, Any] | None = None) -> CitedText | None:
    """A review finding's text with a ``file`` ref to where it points (``line`` is ``None`` when the
    reviewer gave none, which it records as 0) and a ref for each activity of that file the text
    names. The file ref is hinted by the file's basename (always: ``Program.cs`` names one file),
    its stem and the class and method names its design rows give (``BuildFile.tibco_ref``); it falls to the last sentence otherwise. A
    finding with no text has no sentence to carry the ref: ``None`` (no ``cited``), and clients
    show ``text``, ``file`` and ``line`` as before."""
    text = mask(str(finding.get("text") or ""))
    if not text.strip():
        return None
    path = str(finding.get("file") or "")
    hinted: list[HintedRef] = []
    rows = (tibco_ref or {}).get("rows") or []
    if path:
        base = PurePosixPath(path).name
        line = int(finding.get("line") or 0) or None
        hints = [base, PurePosixPath(path).stem,
                 *(r.get("class_name") or "" for r in rows), *(r.get("method") or "" for r in rows)]
        hinted.append(HintedRef(
            ref=CiteRef(id="c1", phase="build", kind="code",
                        label=cut(f"{base}:{line}" if line else base, LABEL_MAX),
                        preview=cut(f"{finding.get('reviewer') or 'review'} finding in {path}"
                                    + (f" at line {line}" if line else ""), PREVIEW_MAX),
                        target=CiteTarget(kind="file", step="build", process_id=process.id,
                                          path=path, line=line)),
            # A file name with its extension (``Program.cs``) is distinctive on its own; the
            # stem and the design names still need to look like identifiers.
            hints=list(dict.fromkeys(h for i, h in enumerate(hints)
                                     if _identifier_like(h) or (i == 0 and "." in h.strip("."))))))
    names = activity_names(process)
    linked = {r.get("activity_id"): r.get("activity_name") or names.get(r.get("activity_id"), "")
              for r in rows if r.get("activity_id")}
    for aid in (tibco_ref or {}).get("activity_ids") or []:
        linked.setdefault(aid, names.get(aid, ""))
    entities: dict[str, CiteRef] = {}
    for aid, name in linked.items():
        ref = activity_ref("c1", process.id, process.ir_id, aid, name, "", verified=None,
                           reason=None, phase="build")
        for n in (aid, name):
            if _identifier_like(n or ""):
                entities.setdefault(n, ref)
    hinted += _unique(link_entities(text, entities))
    if not hinted:
        return CitedText.plain(text)
    return anchor(text, hinted)


# ------------------------------------------------------------------ verify parity
VALUE_MAX = 120  # a parity value is cut to this many code points in the sentence


def _sensitive_field(path: str) -> bool:
    leaf = re.split(r"[./\[\]@]+", path.strip("./[]@"))[-1] if path.strip("./[]@") else ""
    return bool(leaf) and mask(f"{leaf}: x") != f"{leaf}: x"


def _show(path: str, value: Any) -> str:
    """``value`` as JSON, masked and cut to ``VALUE_MAX``: a sensitive field's value (``ssn``),
    scalar, object or list alike, is ``"***"`` whole; any other value has its SSNs masked. The
    sentence is not masked as a whole, where ``ssn: expected ...`` would read as a ``key: value``
    pair and mask the prose. Masking comes before the cut, so a cut never splits an SSN out of the
    mask's reach."""
    if value is None:
        return "null"
    if _sensitive_field(path):
        return '"***"'
    return cut(mask(json.dumps(value, ensure_ascii=False, default=str)), VALUE_MAX)


def cite_diff(diff: dict[str, Any], run: dict[str, Any], process: Any | None = None) -> CitedText:
    """``"{path}: expected {e}, got {a}."`` (plus the diff's ``note``) with a ``verify`` ref for
    the scenario run and, when the diff carries a ``finding_id`` (a finding key), a ``validate``
    ref for that finding. Values are JSON, each masked (an SSN, or any value of a sensitive field,
    never shows), so ``cited.text`` can differ from the raw ``expected``/``actual``."""
    path = str(diff.get("path") or "")
    text = f"{path}: expected {_show(path, diff.get('expected'))}, got {_show(path, diff.get('actual'))}."
    if diff.get("note"):
        text += f" {mask(str(diff['note']))}"
    pid = getattr(process, "id", None)
    scenario = run.get("scenario_name") or run.get("scenario_key") or run.get("scenario_id") or "scenario"
    parity = run.get("parity")
    hinted = [HintedRef(ref=CiteRef(
        id="c1", phase="verify", kind="test", label=cut(mask(str(scenario)), LABEL_MAX),
        preview=cut(mask(f"Scenario run {scenario}: {run.get('status') or 'run'}"
                                   + (f", parity {parity:.0%}" if isinstance(parity, int | float)
                                      else "")), PREVIEW_MAX),
        target=CiteTarget(kind="step", step="verify", process_id=pid)), hints=[path])]
    key = diff.get("finding_id")
    if key:
        hinted.append(HintedRef(ref=CiteRef(
            id="c2", phase="validate", kind="finding", label=cut(str(key), LABEL_MAX),
            preview=cut(f"Finding {key}" + (", touched through a branch decision"
                                            if diff.get("via") == "control" else ", decided Fix at G2"),
                        PREVIEW_MAX),
            memory_uid=finding_chunk_uid(process.ir_id, key) if process is not None else None,
            target=CiteTarget(kind="step", step="validate", process_id=pid)), hints=[path, key]))
    return anchor(text, hinted)


def _display(path: str, diff: dict[str, Any], key: str) -> str:
    """A compared value as the value columns show it, masked like the cited sentence: ``absent``
    when the side has no value, a sensitive field's value as ``***`` whole, any other value as
    JSON (a string as itself) with its sensitive parts masked. Not cut: the column wraps."""
    if key not in diff:
        return "absent"
    value = diff[key]
    if value is None:
        return "null"
    if _sensitive_field(path):
        return MASK
    return mask(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str))


def cite_parity(view: dict[str, Any], process: Any) -> dict[str, Any]:
    """``view`` with ``expected_display``/``actual_display`` (masked, see ``_display``) on every
    field and side-effect difference of every run, and a ``cited`` on each but a ``match`` that no
    finding explains, which has nothing to cite."""
    reports = []
    for rep in view.get("reports") or []:
        rep = dict(rep)
        for key in ("fields", "side_effects"):
            if isinstance(rep.get(key), list):
                rows = []
                for d in rep[key]:
                    path = str(d.get("path") or "")
                    d = {**d, "expected_display": _display(path, d, "expected"),
                         "actual_display": _display(path, d, "actual")}
                    if not (d.get("status") == "match" and not d.get("finding_id")):
                        d["cited"] = cite_diff(d, rep, process).model_dump(mode="json")
                    rows.append(d)
                rep[key] = rows
        reports.append(rep)
    return {**view, "reports": reports}


# ------------------------------------------------------------------ readiness
_STEP_OF_PROCESS_PAGE = {"canvas": "discover", "scenarios": "verify", "test-data": "verify",
                         "analysis": "validate", "design": "design"}
_PROCESS_PAGE_HASH = {"scenarios": "#scenarios", "test-data": "#test-data"}
_BUILD_PAGE_HASH = {"tests": "#tests", "infra": "#infra", "metrics": "#metrics", "parity": "#parity"}


def _readiness_target(kind: str, ref: str,
                      build_process: dict[str, str]) -> tuple[UiPhase, CiteTarget]:
    """Where an evidence link goes, as ``web/app/readiness/page.tsx``'s ``target()`` resolves it
    (X18): a phase is a ``step`` target with its ``hash``; a page that is no phase (settings,
    history) is a ``page`` target. ``/ask`` and anything unknown are the page ``/`` until SP3's
    ExiQoian panel has a target of its own."""
    path = ref.split("?")[0]
    if path.startswith(("/settings", "/history")):
        return "deploy", CiteTarget(kind="page", path=path.rstrip("/") or "/")
    if path.startswith("/ask"):
        return "memory", CiteTarget(kind="page", path="/")
    if m := re.match(r"^/projects/[^/]+/(report|domains|graph|files)", path):
        step: UiPhase = "upload" if m[1] in ("report", "files") else "discover"
        return step, CiteTarget(kind="step", step=step)
    if m := re.match(r"^/processes/([^/]+)/(canvas|scenarios|test-data|analysis|design)", path):
        step = _STEP_OF_PROCESS_PAGE[m[2]]  # type: ignore[assignment]
        return step, CiteTarget(kind="step", step=step, process_id=m[1],
                                hash=_PROCESS_PAGE_HASH.get(m[2]))
    if m := re.match(r"^/builds/([^/]+)(?:/(\w[\w-]*))?", path):
        sub = m[2] or kind
        step = "verify" if sub == "parity" else "deploy" if sub == "plan" else "build"
        return step, CiteTarget(kind="step", step=step, process_id=build_process.get(m[1]),
                                hash=_BUILD_PAGE_HASH.get(sub))
    return "upload", CiteTarget(kind="page", path="/")


def cite_readiness_row(row: dict[str, Any], build_process: dict[str, str] | None = None) -> CitedText:
    """``"{expectation}: {status}."`` (with the gap) and one ref per evidence item on its last
    sentence. ``build_process`` maps a build id to its API process id for build links."""
    gap = row.get("gap")
    text = mask(f"{row.get('expectation')}: {row.get('status')}" + (f"; {gap}" if gap else "")
                          + ".")
    refs = []
    for i, ev in enumerate(row.get("evidence") or []):
        phase, target = _readiness_target(str(ev.get("kind") or ""), str(ev.get("ref") or ""),
                                          build_process or {})
        label = mask(str(ev.get("label") or ev.get("kind") or "evidence"))
        refs.append(CiteRef(id=f"c{i + 1}", phase=phase, kind="evidence", label=cut(label, LABEL_MAX),
                            preview=cut(label, PREVIEW_MAX), target=target))
    return tail(text, refs)
