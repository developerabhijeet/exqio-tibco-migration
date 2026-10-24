"""The diagrams an architect reviews, derived by code from the design record and the process.

Nothing here is drawn or invented by a model. A class box comes from the mapping row that already
decided the class and method; a table comes from the SQL the TIBCO activity already carries; a
component edge comes from the solution layout and the messaging the process already declares. The
architect is therefore reviewing the service the builder is about to emit, not a picture of it.

These views are in ``HASH_EXCLUDED``: they restate facts the design hash already covers (the
mapping) and the spec hash already covers (the process IR), so they add no commitment of their own,
and hashing them would reset an approved G3 the first time this file learns to extract one more
column.
"""
from __future__ import annotations

import re
from itertools import pairwise

from pydantic import BaseModel, Field

from engine.ir.models import Activity, Process

# select ... from X join Y, insert into X, update X, delete from X
_TABLE_AFTER = {
    "select": re.compile(r"\b(?:from|join)\s+([\w.\"]+)", re.IGNORECASE),
    "insert": re.compile(r"\binsert\s+into\s+([\w.\"]+)", re.IGNORECASE),
    "update": re.compile(r"\bupdate\s+([\w.\"]+)", re.IGNORECASE),
    "delete": re.compile(r"\bdelete\s+from\s+([\w.\"]+)", re.IGNORECASE),
}
_SELECT_COLUMNS = re.compile(r"\bselect\s+(.*?)\s+\bfrom\b", re.IGNORECASE | re.DOTALL)
_INSERT_COLUMNS = re.compile(r"\binsert\s+into\s+[\w.\"]+\s*\(([^)]*)\)", re.IGNORECASE | re.DOTALL)
_SET_COLUMNS = re.compile(r"\bset\s+(.*?)(?:\bwhere\b|$)", re.IGNORECASE | re.DOTALL)
_WHERE_COLUMNS = re.compile(r"\bwhere\b(.*)$", re.IGNORECASE | re.DOTALL)
_COLUMN_IN_PREDICATE = re.compile(r"([A-Za-z_][\w.]*)\s*(?:=|<|>|<=|>=|<>|!=|\bin\b|\blike\b)", re.IGNORECASE)
_SQL_KEYS = ("sql", "sqlStatement", "statement")


class Column(BaseModel):
    name: str
    type: str = ""
    role: str = ""  # read | written | filter, how the process uses it


class Table(BaseModel):
    """A database table the process touches, with the columns it names."""

    name: str
    verbs: list[str] = Field(default_factory=list)  # select, insert, update, delete
    columns: list[Column] = Field(default_factory=list)
    activity_ids: list[str] = Field(default_factory=list)


class SqlStatement(BaseModel):
    activity_id: str
    activity_name: str
    verb: str
    tables: list[str] = Field(default_factory=list)
    parameters: list[str] = Field(default_factory=list)
    statement: str


class ClassBox(BaseModel):
    """One generated class with the methods the mapping gives it."""

    name: str
    project: str
    file: str
    stereotype: str = ""  # endpoint, service, repository, record, ...
    methods: list[str] = Field(default_factory=list)
    activity_ids: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)


class ComponentEdge(BaseModel):
    source: str
    target: str
    kind: str  # project | queue | topic | database | process
    label: str = ""


class DesignViews(BaseModel):
    """Everything the Design tab draws, all of it derived."""

    classes: list[ClassBox] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    sql: list[SqlStatement] = Field(default_factory=list)
    components: list[ComponentEdge] = Field(default_factory=list)


def sql_of(act: Activity) -> str | None:
    """The SQL an activity carries, wherever the palette put it."""
    cfg = act.config or {}
    settings = cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}
    for key in _SQL_KEYS:
        for holder in (cfg, settings):
            text = holder.get(key)
            if isinstance(text, str) and text.strip():
                return text
    return None


def _bare(raw: str) -> str:
    return raw.strip().strip('"').split(".")[-1].strip('"').lower()


def _verb(text: str) -> str:
    m = re.match(r"\s*(select|insert|update|delete|with)\b", text, re.IGNORECASE)
    if m is None:
        return ""
    return "select" if m.group(1).lower() == "with" else m.group(1).lower()


def _split_columns(fragment: str) -> list[str]:
    out: list[str] = []
    for part in fragment.split(","):
        name = part.strip().split()[0] if part.strip() else ""
        name = name.strip('"')
        if name and name != "*" and "(" not in name:
            out.append(_bare(name))
    return out


def _columns(text: str, verb: str) -> list[tuple[str, str]]:
    """``(column, role)`` for one statement; a column named twice keeps its first role."""
    found: list[tuple[str, str]] = []
    if verb == "select":
        m = _SELECT_COLUMNS.search(text)
        if m:
            found += [(c, "read") for c in _split_columns(m.group(1))]
    elif verb == "insert":
        m = _INSERT_COLUMNS.search(text)
        if m:
            found += [(c, "written") for c in _split_columns(m.group(1))]
    elif verb == "update":
        m = _SET_COLUMNS.search(text)
        if m:
            found += [(_bare(p.split("=")[0]), "written") for p in m.group(1).split(",") if "=" in p]
    where = _WHERE_COLUMNS.search(text)
    if where:
        found += [(_bare(c), "filter") for c in _COLUMN_IN_PREDICATE.findall(where.group(1))]
    seen: dict[str, str] = {}
    for name, role in found:
        if name and name not in seen:
            seen[name] = role
    return list(seen.items())


def _parameters(act: Activity) -> list[str]:
    """The named JDBC parameters the activity binds, in order."""
    cfg = act.config or {}
    settings = cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}
    out: list[str] = []
    for holder in (settings, cfg):
        params = holder.get("parameters") or holder.get("Parameters")
        if isinstance(params, list):
            for p in params:
                if isinstance(p, dict):
                    name = p.get("ParameterName") or p.get("name")
                    if name and str(name) not in out:
                        out.append(str(name))
    return out


def _column_types(act: Activity) -> dict[str, str]:
    """Types the palette recorded for the result columns, when it recorded any."""
    cfg = act.config or {}
    settings = cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}
    meta = settings.get("columnMetadata") or cfg.get("columnMetadata")
    out: dict[str, str] = {}
    if isinstance(meta, list):
        for col in meta:
            if isinstance(col, dict):
                name = col.get("columnName")
                if name:
                    out[_bare(str(name))] = str(col.get("typeName") or "")
    return out


def statements(process: Process) -> list[SqlStatement]:
    """Every SQL statement the process runs, in activity order."""
    out: list[SqlStatement] = []
    for act in process.activities:
        text = sql_of(act)
        if not text:
            continue
        verb = _verb(text)
        if not verb:
            continue
        tables = list(dict.fromkeys(_bare(t) for t in _TABLE_AFTER[verb].findall(text)))
        out.append(SqlStatement(activity_id=act.id, activity_name=act.name or act.id, verb=verb,
                                tables=tables, parameters=_parameters(act), statement=text.strip()))
    return out


def tables(process: Process) -> list[Table]:
    """The database shape the process implies: one entry per table, with the columns it names."""
    by_name: dict[str, Table] = {}
    for act in process.activities:
        text = sql_of(act)
        if not text:
            continue
        verb = _verb(text)
        if not verb:
            continue
        types = _column_types(act)
        cols = _columns(text, verb)
        for raw in _TABLE_AFTER[verb].findall(text):
            name = _bare(raw)
            table = by_name.setdefault(name, Table(name=name))
            if verb not in table.verbs:
                table.verbs.append(verb)
            if act.id not in table.activity_ids:
                table.activity_ids.append(act.id)
            known = {c.name for c in table.columns}
            for col, role in cols:
                if col not in known:
                    table.columns.append(Column(name=col, type=types.get(col, ""), role=role))
                    known.add(col)
    for table in by_name.values():
        table.columns.sort(key=lambda c: c.name)
    return [by_name[k] for k in sorted(by_name)]


_STEREOTYPE = {"api": "endpoint", "core": "service", "infrastructure": "repository", "tests": "test"}


def classes(mapping: list, solution) -> list[ClassBox]:
    """One box per generated class, built from the mapping rows that already chose it."""
    by_name: dict[str, ClassBox] = {}
    kinds = {p.name: p.kind for p in solution.projects}
    for row in mapping:
        box = by_name.get(row.class_name)
        if box is None:
            box = ClassBox(name=row.class_name, project=row.project, file=row.file,
                           stereotype=_STEREOTYPE.get(kinds.get(row.project, ""), ""))
            by_name[row.class_name] = box
        if row.method and row.method not in box.methods:
            box.methods.append(row.method)
        if row.activity_id not in box.activity_ids:
            box.activity_ids.append(row.activity_id)
        for rid in row.rule_ids:
            if rid not in box.rule_ids:
                box.rule_ids.append(rid)
    return [by_name[k] for k in sorted(by_name)]


def components(solution, messaging: list, table_names: list[str], calls: list[str]) -> list[ComponentEdge]:
    """How the parts of the service connect: projects to each other, then out to the world."""
    out: list[ComponentEdge] = []
    names = [p.name for p in solution.projects]
    order = {"api": 0, "core": 1, "infrastructure": 2, "tests": 3}
    ranked = sorted(solution.projects, key=lambda p: order.get(p.kind, 9))
    for a, b in pairwise(ranked):
        if a.kind != "tests" and b.kind != "tests":
            out.append(ComponentEdge(source=a.name, target=b.name, kind="project", label="uses"))
    infra = next((p.name for p in solution.projects if p.kind == "infrastructure"), names[0] if names else "")
    for table in table_names:
        out.append(ComponentEdge(source=infra, target=table, kind="database", label="reads and writes"))
    for m in messaging:
        edge = ComponentEdge(source=infra, target=m.name, kind=m.kind,
                             label="publishes" if m.direction == "out" else "consumes")
        if m.direction == "in":
            edge = ComponentEdge(source=m.name, target=infra, kind=m.kind, label="consumed by")
        out.append(edge)
    core = next((p.name for p in solution.projects if p.kind == "core"), infra)
    for target in calls:
        out.append(ComponentEdge(source=core, target=target, kind="process", label="calls"))
    return out


def design_views(process: Process, mapping: list, solution, messaging: list,
                 calls: list[str] | None = None) -> DesignViews:
    """Every derived view in one call, so the design job builds them together."""
    tbl = tables(process)
    return DesignViews(
        classes=classes(mapping, solution),
        tables=tbl,
        sql=statements(process),
        components=components(solution, messaging, [t.name for t in tbl], list(calls or [])),
    )
