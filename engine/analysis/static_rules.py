"""Deterministic risk rules S1 to S7 over the neutral IR.

These checks need no model: they read SQL, mappings, transitions, groups and
contracts. Every finding quotes text verbatim from the IR so that the citation
verifier passes on it. Business-policy findings (for example a grace-period
comparison that disagrees with a written policy) are left to the AI risk reviewer.

======  ==========================================================  ========
rule    what it flags                                               severity
======  ==========================================================  ========
S1      read then write on one table with no enclosing transaction   high
S2      ``like`` on an identifier column                             high
S3      hard-coded URL or host in a mapping or config                medium
S4      response schema field never populated by any mapping        medium
S5      conditional transitions out of an activity, no otherwise     low
S6      catch/fault handler with no activities                       low
S7      sensitive field written to a log or an audit insert          high
======  ==========================================================  ========
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from lxml import etree

from engine.analysis.evidence import iter_config_strings
from engine.analysis.models import Citation, Finding
from engine.ir.models import Activity, Contract, Process, SubstVar

_XS = "http://www.w3.org/2001/XMLSchema"
_REST_NS = "http://tns.tibco.com/bw/REST"

_ID_COLUMNS = {"ssn", "dealer_id", "account_number", "vin", "application_id", "deal_id"}
_SENSITIVE = {"ssn", "accountnumber", "cardnumber"}  # compared lower-case, underscores removed
_MASK_HINTS = ("***", "mask")

_SQL_LIKE = re.compile(r"(?P<col>[\w.\"]+)\s+(?:not\s+)?like\s+(?:\?|'[^']*'|:\w+)", re.IGNORECASE)
_TABLE_AFTER = {
    "select": re.compile(r"\b(?:from|join)\s+([\w.\"]+)", re.IGNORECASE),
    "insert": re.compile(r"\binto\s+([\w.\"]+)", re.IGNORECASE),
    "update": re.compile(r"^\s*update\s+([\w.\"]+)", re.IGNORECASE),
    "delete": re.compile(r"\bfrom\s+([\w.\"]+)", re.IGNORECASE),
}
_INSERT_COLUMNS = re.compile(r"\binto\s+[\w.\"]+\s*\(([^)]*)\)", re.IGNORECASE)
_URL = re.compile(r"\b(?:https?|wss?|ftp|sftp)://[^\s\"'<>)\]]+", re.IGNORECASE)
_NAMESPACE_HOSTS = ("w3.org", "tibco.com")
_SKIP_CONFIG_KEYS = {"input_xslt", "message_xslt", "palette_type", "palette_version", "xsi_type", "href"}
_HOST_KEYS = {"host", "hostname", "url", "uri", "endpoint", "endpointurl", "serviceurl", "baseurl", "serverurl"}
_PLACEHOLDER = re.compile(r"%%|\{\{|\$\{|^\$")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


# ----------------------------------------------------------------------- helpers
@dataclass
class _Sql:
    activity: Activity
    text: str
    verb: str  # select / insert / update / delete
    tables: list[str]


def _sql_of(act: Activity) -> str | None:
    cfg = act.config
    settings = cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}
    for text in (cfg.get("sql"), settings.get("sqlStatement"), settings.get("statement")):
        if isinstance(text, str) and text.strip():
            return text
    return None


def _table_name(raw: str) -> str:
    return raw.strip('"').split(".")[-1].strip('"').lower()


def _parse_sql(act: Activity) -> _Sql | None:
    text = _sql_of(act)
    if text is None:
        return None
    verb_m = re.match(r"\s*(select|insert|update|delete|with)\b", text, re.IGNORECASE)
    if verb_m is None:
        return None
    verb = verb_m.group(1).lower()
    verb = "select" if verb == "with" else verb
    tables = [_table_name(t) for t in _TABLE_AFTER[verb].findall(text)]
    return _Sql(act, text, verb, list(dict.fromkeys(tables)))


def _norm_token(token: str) -> str:
    return token.replace("_", "").lower()


def _sensitive_in(text: str) -> set[str]:
    return {_norm_token(w) for w in _WORD.findall(text) if _norm_token(w) in _SENSITIVE}


def _is_masked(text: str) -> bool:
    low = text.lower()
    return any(h in low for h in _MASK_HINTS)


class _Flow:
    """Reachability and enclosing-group lookups over one process."""

    def __init__(self, process: Process):
        self.process = process
        self.edges: dict[str, set[str]] = defaultdict(set)
        for tr in process.transitions:
            self.edges[tr.from_id].add(tr.to_id)
        self.members: dict[str, set[str]] = {g.id: set(g.member_ids) for g in process.groups}
        for act in process.activities:
            if act.group_id and act.group_id in self.members:
                self.members[act.group_id].add(act.id)
        for gid, members in self.members.items():
            self.edges[gid] |= members  # entering a group reaches its members
            for m in members:
                self.edges[m].add(gid)  # leaving a member leaves the group
        self.kind_of = {g.id: g.kind for g in process.groups}
        self.parent: dict[str, str] = {}
        for gid, members in self.members.items():
            for m in members:
                self.parent.setdefault(m, gid)

    def reaches(self, src: str, dst: str) -> bool:
        seen, stack = set(), [src]
        while stack:
            cur = stack.pop()
            for nxt in self.edges.get(cur, ()):
                if nxt == dst:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    def transaction_groups(self, node: str) -> set[str]:
        out: set[str] = set()
        cur = self.parent.get(node)
        while cur is not None and cur not in out:
            if self.kind_of.get(cur) == "transaction":
                out.add(cur)
            cur = self.parent.get(cur)
        return out


class _Ids:
    """Per-process finding id allocator: ``<rule>-<process name>-<n>``."""

    def __init__(self, process: Process):
        self.name = process.name
        self.counts: dict[str, int] = defaultdict(int)

    def next(self, rule: str) -> str:
        self.counts[rule] += 1
        return f"{rule}-{self.name}-{self.counts[rule]}"


# ------------------------------------------------------------------------- rules
def _s1(process: Process, flow: _Flow, ids: _Ids) -> list[Finding]:
    sqls = [s for a in process.activities if (s := _parse_sql(a))]
    out: list[Finding] = []
    for rd in (s for s in sqls if s.verb == "select"):
        for wr in (s for s in sqls if s.verb in ("insert", "update", "delete")):
            shared = [t for t in rd.tables if t in wr.tables]
            if not shared or rd.activity.id == wr.activity.id:
                continue
            if not flow.reaches(rd.activity.id, wr.activity.id):
                continue
            if flow.transaction_groups(rd.activity.id) & flow.transaction_groups(wr.activity.id):
                continue
            table = shared[0]
            out.append(Finding(
                id=ids.next("S1"), severity="high", source="static",
                text=(f"Read-then-write on table {table} without an enclosing transaction group: "
                      f"{rd.activity.name} reads and {wr.activity.name} writes, so two concurrent runs "
                      f"can both pass the read and both write."),
                citations=[Citation(activity_id=rd.activity.id, kind="sql", text=rd.text),
                           Citation(activity_id=wr.activity.id, kind="sql", text=wr.text)],
            ))
    return out


def _s2(process: Process, ids: _Ids) -> list[Finding]:
    out: list[Finding] = []
    for act in process.activities:
        text = _sql_of(act)
        if text is None:
            continue
        for m in _SQL_LIKE.finditer(text):
            col = m.group("col").strip('"').split(".")[-1].lower()
            if col not in _ID_COLUMNS and not col.endswith("_id"):
                continue
            cites = [Citation(activity_id=act.id, kind="sql", text=m.group(0))]
            wild = [b for b in act.input_bindings if "'%'" in b.expression or '"%"' in b.expression]
            cites += [Citation(activity_id=act.id, kind="expression", text=b.expression) for b in wild[:1]]
            out.append(Finding(
                id=ids.next("S2"), severity="high", source="static", citations=cites,
                text=(f"Identifier column {col} is matched with LIKE in {act.name}: a prefix or wildcard "
                      f"match can return a different record than the one asked for "
                      f"(for example D1002 also matches D10021). Use equality."),
            ))
    return out


def _is_namespace_url(url: str) -> bool:
    host = re.sub(r"^[a-z]+://", "", url, flags=re.IGNORECASE).split("/")[0].split(":")[0].lower()
    return host.endswith(_NAMESPACE_HOSTS) or host.startswith("xmlns.")


def _s3(process: Process, substvars: list[SubstVar], ids: _Ids) -> list[Finding]:
    by_value = {s.value: s.name for s in substvars if s.value}
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()

    def emit(act: Activity, kind: str, literal: str, where: str) -> None:
        if (act.id, literal) in seen:
            return
        seen.add((act.id, literal))
        hint = ""
        for value, name in by_value.items():
            if len(value) > 3 and value in literal:
                hint = f" The module property {name} already holds part of this value."
                break
        out.append(Finding(
            id=ids.next("S3"), severity="medium", source="static",
            citations=[Citation(activity_id=act.id, kind=kind, text=literal)],  # type: ignore[arg-type]
            text=(f"Hard-coded endpoint {literal} in {where} of {act.name}: it will be wrong in any other "
                  f"environment and cannot be changed without a rebuild. Move it to configuration.{hint}"),
        ))

    for act in process.activities:
        for b in act.input_bindings:
            for m in _URL.finditer(b.expression):
                if not _is_namespace_url(m.group(0)):
                    emit(act, "expression", m.group(0), f"mapping {b.target.rsplit('/', 1)[-1]}")
        for key, value in iter_config_strings(act.config):
            if key in _SKIP_CONFIG_KEYS or key.endswith("xslt") or _PLACEHOLDER.search(value):
                continue
            literal = None
            m = _URL.search(value)
            if m and not _is_namespace_url(m.group(0)):
                literal = m.group(0)
            elif key.lower() in _HOST_KEYS and value.strip():
                literal = value.strip()
            if literal:
                emit(act, "config", literal, f"config {key}")
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _schema_roots(contracts: list[Contract], prefer: list[str]) -> dict[str, tuple[etree._Element, dict[str, etree._Element]]]:
    """Map root element name -> (element, complexType lookup) over xsd/wsdl contracts."""
    ordered = sorted(contracts, key=lambda c: 0 if c.id in prefer else 1)
    roots: dict[str, tuple[etree._Element, dict[str, etree._Element]]] = {}
    for c in ordered:
        if c.kind not in ("xsd", "wsdl") or not c.text:
            continue
        try:
            doc = etree.fromstring(c.text.encode("utf-8"))
        except etree.XMLSyntaxError:
            continue
        for schema in doc.iter(f"{{{_XS}}}schema"):
            if schema.get("targetNamespace") == _REST_NS:
                continue  # REST fault/header plumbing, not a business response
            types = {t.get("name"): t for t in schema.findall(f"{{{_XS}}}complexType")}
            for el in schema.findall(f"{{{_XS}}}element"):
                roots.setdefault(el.get("name"), (el, types))
    return roots


def _direct_children(el, types):
    """Child elements of el's content model, not descending into nested complex content."""
    ctype = el.find(f"{{{_XS}}}complexType")
    if ctype is None and el.get("type"):
        ctype = types.get(el.get("type").rsplit(":", 1)[-1])
    if ctype is None:
        return []
    out = []
    for group in ctype:
        if _local(group.tag) in ("sequence", "all", "choice"):
            out.extend(e for e in group.iter(f"{{{_XS}}}element"))
    return out


def _unpopulated(el, types, path: str, bound: set[str], depth: int = 0) -> list[str]:
    """Paths (relative to the root) of schema fields with no binding at or below them."""
    missing: list[str] = []
    for child in _direct_children(el, types):
        name = child.get("name") or (child.get("ref") or "").rsplit(":", 1)[-1]
        if not name:
            continue
        child_path = f"{path}/{name}" if path else name
        has_below = any(b == child_path or b.startswith(child_path + "/") for b in bound)
        has_above = any(child_path.startswith(b + "/") for b in bound)
        if has_above:
            continue  # a parent is mapped as a whole
        if not has_below:
            missing.append(child_path)
        elif depth < 6:
            missing.extend(_unpopulated(child, types, child_path, bound, depth + 1))
    return missing


def _s4(process: Process, contracts: list[Contract], ids: _Ids) -> list[Finding]:
    roots = _schema_roots(contracts, process.contracts_used)
    if not roots:
        return []
    # response mappings per root element, over every non-fault reply in the process
    mapped: dict[str, list[tuple[Activity, str, str]]] = defaultdict(list)  # root -> (activity, rel path, expr)
    for act in process.activities:
        if act.type != "reply" or act.config.get("fault_name"):
            continue
        for b in act.input_bindings:
            root, _, rel = b.target.partition("/")
            if root in roots:
                mapped[root].append((act, rel, b.expression))
    out: list[Finding] = []
    for root, entries in mapped.items():
        el, types = roots[root]
        # the reply that carries most of the mapping is the one the finding is about
        counts: dict[str, int] = defaultdict(int)
        for act, _, _ in entries:
            counts[act.id] += 1
        primary = max(entries, key=lambda e: counts[e[0].id])[0]
        own = [(rel, expr) for act, rel, expr in entries if act.id == primary.id]
        bound = {rel for _, rel, _ in entries}
        order = [c.get("name") for c in _direct_children(el, types)]
        for field in _unpopulated(el, types, "", bound):
            parent, _, leaf = field.rpartition("/")
            siblings = [(rel, expr) for rel, expr in own if rel.rpartition("/")[0] == parent]
            if not parent and leaf in order:
                # neighbours in schema order that are mapped show the gap in the real mapping
                idx = order.index(leaf)
                before = [x for x in siblings if x[0] in order and order.index(x[0]) < idx]
                after = [x for x in siblings if x[0] in order and order.index(x[0]) > idx]
                picks = ([before[-1]] if before else []) + ([after[0]] if after else [])
            else:
                picks = siblings[:2]
            picks = picks or own[:1]
            out.append(Finding(
                id=ids.next("S4"), severity="medium", source="static",
                citations=[Citation(activity_id=primary.id, kind="expression", text=expr) for _, expr in picks],
                text=(f"Response field {field} of {root} is declared in the response schema but no mapping in "
                      f"{primary.name} populates it, so callers always receive it empty or absent."),
            ))
    return out


def _s5(process: Process, ids: _Ids) -> list[Finding]:
    out: list[Finding] = []
    by_source: dict[str, list] = defaultdict(list)
    for tr in process.transitions:
        by_source[tr.from_id].append(tr)
    names = {a.id: a.name for a in process.activities}
    for src, outs in by_source.items():
        cond = [t for t in outs if t.kind == "conditional" and t.expression]
        if not cond or any(t.kind in ("otherwise", "success") for t in outs):
            continue
        out.append(Finding(
            id=ids.next("S5"), severity="low", source="static",
            citations=[Citation(activity_id=src, kind="condition", text=t.expression) for t in cond],  # type: ignore[arg-type]
            text=(f"{names.get(src, src)} has {len(cond)} conditional transition(s) and no otherwise: when no "
                  f"condition is true the flow stops silently and nothing is returned or logged."),
        ))
    return out


def _s6(process: Process, ids: _Ids) -> list[Finding]:
    out: list[Finding] = []
    for fh in process.fault_handlers:
        if fh.activity_ids:
            continue
        label = fh.fault_name or fh.id
        out.append(Finding(
            id=ids.next("S6"), severity="low", source="static",
            citations=[Citation(activity_id=fh.id, kind="config", text=label)],
            text=f"Catch handler {label} has no activities: the fault is swallowed without a reply, log or compensation.",
        ))
    return out


_AUDIT_TABLE = re.compile(r"audit|(^|_)log$", re.IGNORECASE)


def _s7(process: Process, ids: _Ids) -> list[Finding]:
    out: list[Finding] = []
    for act in process.activities:
        # (a) log activities
        if act.type == "log":
            hits = [(b, _sensitive_in(b.expression)) for b in act.input_bindings
                    if not b.target.endswith("/msgCode") and not _is_masked(b.expression)]
            hits = [(b, s) for b, s in hits if s]
            if hits:
                fields = sorted({f for _, s in hits for f in s})
                out.append(Finding(
                    id=ids.next("S7"), severity="high", source="static",
                    citations=[Citation(activity_id=act.id, kind="expression", text=b.expression) for b, _ in hits],
                    text=(f"Sensitive field ({', '.join(fields)}) is written to the log by {act.name} "
                          f"without masking; logs are widely readable and retained."),
                ))
        # (b) audit-style inserts
        parsed = _parse_sql(act)
        if parsed is None or parsed.verb != "insert" or not any(_AUDIT_TABLE.search(t) for t in parsed.tables):
            continue
        cols = _INSERT_COLUMNS.search(parsed.text)
        col_names = [c.strip().strip('"') for c in cols.group(1).split(",")] if cols else []
        sens_cols = [c for c in col_names if _norm_token(c) in _SENSITIVE]
        if not sens_cols:
            continue
        cites = [Citation(activity_id=act.id, kind="sql", text=parsed.text)]
        wanted = {_norm_token(c) for c in sens_cols}
        for b in act.input_bindings:
            if _norm_token(b.target.rsplit("/", 1)[-1]) in wanted and not _is_masked(b.expression):
                cites.append(Citation(activity_id=act.id, kind="expression", text=b.expression))
        out.append(Finding(
            id=ids.next("S7"), severity="high", source="static", citations=cites,
            text=(f"Sensitive column {', '.join(sens_cols)} is written in clear text to {parsed.tables[0]} by "
                  f"{act.name}; the audit trail becomes a second copy of regulated personal data."),
        ))
    return out


# ------------------------------------------------------------------------ entry
def run_static_rules(process: Process, contracts: list[Contract], substvars: list[SubstVar]) -> list[Finding]:
    """Run S1 to S7 over one process and return findings (``source="static"``)."""
    flow = _Flow(process)
    ids = _Ids(process)
    findings: list[Finding] = []
    findings += _s1(process, flow, ids)
    findings += _s2(process, ids)
    findings += _s3(process, substvars, ids)
    findings += _s4(process, contracts, ids)
    findings += _s5(process, ids)
    findings += _s6(process, ids)
    findings += _s7(process, ids)
    return findings
