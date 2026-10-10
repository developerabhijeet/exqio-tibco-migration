"""Domain discovery: union-find over processes (tab 2, Discover).

Processes are joined into one candidate domain when they share a module, touch
the same table, publish/consume the same queue or topic, or call one another.
Everything here is deterministic code over the parsed IR; AI renaming of the
suggested names happens later in the API layer.

``sql_tables`` and ``process_facts`` are shared with ``graph.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from engine.domains.models import DomainCandidate, Evidence
from engine.ir.models import Activity, Application, Process

# ------------------------------------------------------------------ SQL tables
_SQL_NOISE = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'", re.DOTALL)
_SQL_TABLE = re.compile(
    r"\b(?P<kw>delete\s+from|insert\s+into|update|from|join)\s+"
    r"(?P<name>(?:\"[^\"]+\"|[A-Za-z_][\w$]*)(?:\s*\.\s*(?:\"[^\"]+\"|[A-Za-z_][\w$]*))*)",
    re.IGNORECASE,
)
_NOT_TABLES = {"select", "set", "values", "dual", "lateral", "only", "table", "where"}


@dataclass(frozen=True)
class TableRef:
    name: str
    schema: str | None
    operation: str  # read | insert | update | delete


def sql_tables(sql: str) -> list[TableRef]:
    """Tables a statement touches, from a conservative regex over from/join/update/insert into/delete from."""
    text = _SQL_NOISE.sub(" ", sql or "")
    text = re.sub(r"\s+", " ", text)
    out: list[TableRef] = []
    seen: set[tuple[str, str]] = set()
    for m in _SQL_TABLE.finditer(text):
        kw = re.sub(r"\s+", " ", m.group("kw").lower())
        if kw == "update" and re.search(r"\bfor\s*$", text[: m.start()], re.IGNORECASE):
            continue  # SELECT ... FOR UPDATE is a lock clause, not a write
        parts = [p.strip().strip('"') for p in m.group("name").split(".")]
        name = parts[-1].lower()
        if not name or name in _NOT_TABLES:
            continue
        schema = parts[-2].lower() if len(parts) > 1 else None
        op = {"from": "read", "join": "read", "insert into": "insert", "update": "update",
              "delete from": "delete"}[kw]
        if (name, op) in seen:
            continue
        seen.add((name, op))
        out.append(TableRef(name=name, schema=schema, operation=op))
    return out


def activity_sql(act: Activity) -> str | None:
    sql = act.config.get("sql") or act.config.get("statement")
    return sql if isinstance(sql, str) and sql.strip() else None


# ------------------------------------------------------------------- messaging
_CONSUME_TYPES = {"jms.receive"}


@dataclass(frozen=True)
class MessageRef:
    activity_id: str
    direction: str  # consume | publish
    destination: str
    style: str  # Queue | Topic


def _style(config: dict, act_type: str) -> str:
    raw = str(config.get("messaging_style") or "").lower()
    if raw in ("topic", "queue"):
        return raw.capitalize()
    return "Topic" if act_type == "jms.publish" else "Queue"


def activity_messages(act: Activity) -> list[MessageRef]:
    if not act.type.startswith("jms.") or act.type == "jms.reply":
        return []
    dest = act.config.get("destination")
    if not isinstance(dest, str) or not dest:
        return []
    direction = "consume" if act.type in _CONSUME_TYPES else "publish"
    return [MessageRef(act.id, direction, dest, _style(act.config, act.type))]


def process_messages(p: Process) -> list[MessageRef]:
    refs: list[MessageRef] = []
    for act in p.activities:
        refs.extend(activity_messages(act))
    st = p.starter
    dest = st.config.get("destination") if st and st.kind == "jms" else None
    if isinstance(dest, str) and not any(r.direction == "consume" and r.destination == dest for r in refs):
        style = "Topic" if str(st.config.get("messaging_style", "")).lower() == "topic" else "Queue"
        refs.append(MessageRef(st.activity_id, "consume", dest, style))
    return refs


# ----------------------------------------------------------------------- facts
@dataclass
class ProcessFacts:
    process_id: str
    tables_read: set[str] = field(default_factory=set)
    tables_written: set[str] = field(default_factory=set)
    queues: set[str] = field(default_factory=set)  # queues and topics, both directions
    calls: list[str] = field(default_factory=list)  # resolved process ids


def resolve_call(target: str, by_id: dict[str, Process]) -> str | None:
    if target in by_id:
        return target
    base = re.split(r"[\\/]", target)[-1]
    base = re.sub(r"\.(process|bwp)$", "", base)
    matches = [pid for pid in by_id if pid == base or pid.endswith("." + base)]
    return matches[0] if len(matches) == 1 else None


def process_facts(p: Process, by_id: dict[str, Process]) -> ProcessFacts:
    f = ProcessFacts(p.id)
    for act in p.activities:
        sql = activity_sql(act)
        if sql:
            for ref in sql_tables(sql):
                (f.tables_read if ref.operation == "read" else f.tables_written).add(ref.name)
    for m in process_messages(p):
        f.queues.add(m.destination)
    for c in p.calls:
        target = resolve_call(c.target_process, by_id)
        if target and target not in f.calls:
            f.calls.append(target)
    return f


# ------------------------------------------------------------------ naming
def suggest_name(module: str) -> str:
    """CamelCase / snake / dotted module name -> Title Cased words (CreditOrigination -> Credit Origination)."""
    spaced = re.sub(r"[_.\-\s]+", " ", module)
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    words = [w if w.isupper() and len(w) > 1 else w.capitalize() for w in spaced.split()]
    return " ".join(words) or module


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "domain"


class _UnionFind:
    def __init__(self, items: list[str]):
        self.parent = {i: i for i in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _module_of(p: Process, app: Application) -> tuple[str, bool]:
    if p.module:
        return p.module, True
    return (p.package or app.name), False


def discover_domains(apps: list[Application]) -> list[DomainCandidate]:
    procs: list[tuple[Process, Application]] = [(p, a) for a in apps for p in a.processes]
    by_id = {p.id: p for p, _ in procs}
    ids = list(by_id)
    uf = _UnionFind(ids)
    facts = {p.id: process_facts(p, by_id) for p, _ in procs}
    module_of: dict[str, tuple[str, bool]] = {p.id: _module_of(p, a) for p, a in procs}

    # module membership (a module is scoped to its application)
    first_in_module: dict[tuple[str, str], str] = {}
    for p, a in procs:
        key = (a.id, module_of[p.id][0])
        if key in first_in_module:
            uf.union(first_in_module[key], p.id)
        else:
            first_in_module[key] = p.id

    # shared tables / queues / topics
    users: dict[tuple[str, str], list[str]] = {}
    for pid, f in facts.items():
        for t in f.tables_read | f.tables_written:
            users.setdefault(("table", t), []).append(pid)
        for q in f.queues:
            users.setdefault(("queue", q), []).append(pid)
    for pids in users.values():
        for other in pids[1:]:
            uf.union(pids[0], other)

    # call edges
    for pid, f in facts.items():
        for target in f.calls:
            uf.union(pid, target)

    groups: dict[str, list[str]] = {}
    for pid in ids:
        groups.setdefault(uf.find(pid), []).append(pid)

    candidates: list[DomainCandidate] = []
    used_ids: set[str] = set()
    for members in groups.values():
        member_set = set(members)
        mods: dict[str, list[str]] = {}
        fallback: set[str] = set()
        for pid in members:
            name, real = module_of[pid]
            mods.setdefault(name, []).append(pid)
            if not real:
                fallback.add(name)
        evidence: list[Evidence] = []
        for name, pids in mods.items():
            if name in fallback:
                evidence.append(Evidence(kind="name", text=f"No module declared; grouped by name '{name}' "
                                                           f"({len(pids)} process{'es' if len(pids) != 1 else ''})"))
            else:
                evidence.append(Evidence(kind="module", text=f"Module {name} holds {len(pids)} "
                                                             f"process{'es' if len(pids) != 1 else ''}"))
        for (kind, name), pids in users.items():
            inside = [x for x in pids if x in member_set]
            if len(inside) > 1:
                label = "Table" if kind == "table" else "Queue/topic"
                evidence.append(Evidence(kind=kind, text=f"{label} {name} shared by {', '.join(inside)}"))
        for pid in members:
            for target in facts[pid].calls:
                evidence.append(Evidence(kind="call", text=f"{pid} calls {target}"))
        main = max(mods, key=lambda m: (len(mods[m]), -list(mods).index(m)))
        sname = suggest_name(main)
        did = _slug(sname)
        n = 2
        while did in used_ids:
            did, n = f"{_slug(sname)}-{n}", n + 1
        used_ids.add(did)
        candidates.append(DomainCandidate(id=did, process_ids=sorted(members, key=ids.index),
                                          evidence=evidence, suggested_name=sname))
    candidates.sort(key=lambda c: ids.index(c.process_ids[0]))
    return candidates
