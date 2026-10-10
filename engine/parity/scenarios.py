"""Scenario generation for the parity evaluator (design spec section 6.3).

Three layers:

* :class:`SchemaIndex`: the estate's XSD contracts as element declarations (types,
  enumerations, patterns, ``elementFormDefault``). The evaluator uses it to turn scenario dicts
  into correctly qualified XML (``$post.item/tns:applicant/tns:ssn``) and to type JSON; the
  generator uses it to produce schema-valid values.
* :class:`SeedData`: the rows of the estate's ``seed.sql`` (and primary keys from
  ``schema.sql``), so generated inputs reuse real ids and JDBC lookups hit.
* :func:`generate_scenarios` (static: happy path, one scenario per conditional/otherwise
  transition by solving simple predicates, one per ``throw``, one timeout and one 5xx per HTTP
  stub) and :func:`generate_and_evaluate` (runs the evaluator, fills ``expected``, and closes
  coverage gaps the static solver cannot reach, such as predicates over JDBC results, by trying
  the other seed rows).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lxml import etree

from engine.ir.models import Activity, Application, Contract, Process, Resource, Transition
from engine.parity.models import Scenario, StubResponse
from engine.parity.stubs import StubCall, StubRegistry

if TYPE_CHECKING:  # pragma: no cover
    from engine.parity.evaluator import Evaluator

XS = "http://www.w3.org/2001/XMLSchema"
_XS = f"{{{XS}}}"

INT_TYPES = {"int", "integer", "long", "short", "byte", "nonNegativeInteger", "positiveInteger",
             "negativeInteger", "nonPositiveInteger", "unsignedInt", "unsignedLong",
             "unsignedShort", "unsignedByte"}
NUMBER_TYPES = INT_TYPES | {"decimal", "double", "float"}


# --- XSD index -------------------------------------------------------------------


@dataclass
class XsdElement:
    """One element declaration, with its (resolved) type flattened in."""

    name: str
    ns: str | None  # namespace of the element name; None when unqualified
    simple_type: str | None = None  # XSD built-in local name for simple content
    children: list[XsdElement] = field(default_factory=list)
    min_occurs: int = 1
    max_occurs: int | None = 1  # None = unbounded
    enumerations: list[str] = field(default_factory=list)
    pattern: str | None = None
    complex: bool = False

    def child(self, name: str) -> XsdElement | None:
        return next((c for c in self.children if c.name == name), None)

    @property
    def repeating(self) -> bool:
        return self.max_occurs is None or self.max_occurs > 1

    def leaves(self, prefix: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], XsdElement]]:
        for c in self.children:
            path = (*prefix, c.name)
            if c.complex:
                yield from c.leaves(path)
            else:
                yield path, c


@dataclass
class _Schema:
    root: etree._Element
    tns: str | None
    qualified: bool
    path: str


class SchemaIndex:
    """Global element declarations of every XSD contract, keyed by ``(namespace, name)``."""

    def __init__(self, contracts: Iterable[Contract]) -> None:
        self._schemas: list[_Schema] = []
        self._types: dict[tuple[str | None, str], tuple[etree._Element, _Schema]] = {}
        self._elements: dict[tuple[str | None, str], tuple[etree._Element, _Schema]] = {}
        self._cache: dict[tuple[str | None, str], XsdElement] = {}
        for c in contracts:
            if c.kind != "xsd":
                continue
            text = c.text or (Path(c.path).read_text(encoding="utf-8") if Path(c.path).exists() else "")
            if not text.strip():
                continue
            try:
                root = etree.fromstring(text.encode("utf-8"))
            except etree.XMLSyntaxError:
                continue
            schema = _Schema(root, root.get("targetNamespace"),
                             root.get("elementFormDefault") == "qualified", c.id or c.path)
            self._schemas.append(schema)
            for el in root:
                if not isinstance(el.tag, str) or el.get("name") is None:
                    continue
                key = (schema.tns, el.get("name"))
                if el.tag in (f"{_XS}complexType", f"{_XS}simpleType"):
                    self._types.setdefault(key, (el, schema))
                elif el.tag == f"{_XS}element":
                    self._elements.setdefault(key, (el, schema))

    # lookups
    def element(self, ns: str | None, name: str) -> XsdElement | None:
        key = (ns, name)
        if key not in self._cache:
            found = self._elements.get(key)
            if found is None:
                return None
            el, schema = found
            self._cache[key] = self._element(el, schema, top=True, seen=set())
        return self._cache[key]

    def find(self, name: str, *, path_hint: str | None = None, ns: str | None = None) -> XsdElement | None:
        """First global element with local ``name`` (preferring a schema whose path matches)."""
        candidates = [(k, s) for k, (_, s) in self._elements.items() if k[1] == name
                      and (ns is None or k[0] == ns)]
        if path_hint:
            hinted = [c for c in candidates if path_hint in c[1].path or c[1].path.endswith(path_hint)]
            candidates = hinted or candidates
        return self.element(*candidates[0][0]) if candidates else None

    def from_qname(self, qname: str | None) -> XsdElement | None:
        """``{ns}Name`` (as in ``Variable.schema``) to a declaration."""
        if not qname:
            return None
        m = re.fullmatch(r"\{([^}]*)\}(.+)", qname)
        return self.element(m.group(1), m.group(2)) if m else self.find(qname)

    def from_href(self, href: str) -> XsdElement | None:
        """``platform:/resource/Module/Schemas/X.xsd#//Name;XSDElementDeclaration``."""
        m = re.search(r"/resource/(?P<path>[^#]+)#//(?P<name>[^;]+)", href)
        if not m:
            return None
        return self.find(m.group("name"), path_hint=m.group("path"))

    # declaration resolution
    def _qname(self, value: str, ctx: etree._Element) -> tuple[str | None, str]:
        prefix, _, local = value.rpartition(":")
        return ctx.nsmap.get(prefix or None), local

    def _element(self, el: etree._Element, schema: _Schema, *, top: bool, seen: set) -> XsdElement:
        if el.get("ref"):
            ns, local = self._qname(el.get("ref"), el)
            target = self._elements.get((ns, local))
            decl = (self._element(target[0], target[1], top=True, seen=seen) if target
                    else XsdElement(local, ns))
            decl = copy.copy(decl)
        else:
            ns = schema.tns if (top or schema.qualified or el.get("form") == "qualified") else None
            decl = XsdElement(el.get("name"), ns)
            type_attr = el.get("type")
            if type_attr:
                self._apply_type(decl, *self._qname(type_attr, el), seen)
            else:
                for c in el:
                    if c.tag == f"{_XS}complexType":
                        self._complex(decl, c, schema, seen)
                    elif c.tag == f"{_XS}simpleType":
                        self._simple(decl, c, seen)
                if not decl.complex and decl.simple_type is None:
                    decl.simple_type = "string"
        decl.min_occurs = int(el.get("minOccurs", "1"))
        mx = el.get("maxOccurs", "1")
        decl.max_occurs = None if mx == "unbounded" else int(mx)
        return decl

    def _apply_type(self, decl: XsdElement, ns: str | None, local: str, seen: set) -> None:
        if ns == XS:
            decl.simple_type = local
            return
        found = self._types.get((ns, local))
        if found is None or (ns, local) in seen:
            decl.simple_type = decl.simple_type or "string"
            return
        node, schema = found
        inner = seen | {(ns, local)}
        if node.tag == f"{_XS}complexType":
            self._complex(decl, node, schema, inner)
        else:
            self._simple(decl, node, inner)

    def _simple(self, decl: XsdElement, node: etree._Element, seen: set) -> None:
        restriction = node.find(f"{_XS}restriction")
        if restriction is None:
            decl.simple_type = decl.simple_type or "string"
            return
        base = restriction.get("base")
        if base:
            self._apply_type(decl, *self._qname(base, restriction), seen)
        for facet in restriction:
            if facet.tag == f"{_XS}enumeration":
                decl.enumerations.append(facet.get("value"))
            elif facet.tag == f"{_XS}pattern":
                decl.pattern = facet.get("value")

    def _complex(self, decl: XsdElement, node: etree._Element, schema: _Schema, seen: set) -> None:
        decl.complex = True
        for c in node.iter(f"{_XS}extension"):
            base = c.get("base")
            if base:
                ns, local = self._qname(base, c)
                found = self._types.get((ns, local))
                if found is not None and (ns, local) not in seen:
                    base_decl = XsdElement(decl.name, decl.ns)
                    self._complex(base_decl, found[0], found[1], seen | {(ns, local)})
                    decl.children.extend(base_decl.children)
        for c in node.iter(f"{_XS}element"):
            # only direct particles of this type, not elements of nested anonymous types
            owner = c.getparent()
            while owner is not None and owner.tag not in (f"{_XS}complexType", f"{_XS}element"):
                owner = owner.getparent()
            if owner is node:
                decl.children.append(self._element(c, schema, top=False, seen=seen))

    # building
    def build(self, decl: XsdElement | None, value: Any, root: str | None = None,
              nsmap: dict[str | None, str] | None = None) -> etree._Element:
        """Build ``value`` (a dict) as an element of ``decl``, children in schema order."""
        if decl is None:
            from engine.parity.models import dict_to_xml

            return dict_to_xml(value if isinstance(value, dict) else {}, root or "root")
        el = etree.Element(_tag(decl.ns, root or decl.name), nsmap=nsmap)
        _fill_decl(el, decl, value)
        return el


def _tag(ns: str | None, name: str) -> str:
    return f"{{{ns}}}{name}" if ns else name


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _fill_decl(el: etree._Element, decl: XsdElement, value: Any) -> None:
    if not isinstance(value, dict):
        if value is not None:
            el.text = _text(value)
        return
    known = {c.name for c in decl.children}
    for child in decl.children:
        if child.name not in value:
            continue
        items = value[child.name]
        for item in items if isinstance(items, list) else [items]:
            sub = etree.SubElement(el, _tag(child.ns, child.name))
            if child.complex:
                _fill_decl(sub, child, item if isinstance(item, dict) else {})
            elif item is not None:
                sub.text = _text(item)
    for key, items in value.items():
        if key in known:
            continue
        for item in items if isinstance(items, list) else [items]:
            sub = etree.SubElement(el, _tag(decl.ns if decl.children and decl.children[0].ns else None, key))
            _fill_decl(sub, XsdElement(key, None, complex=isinstance(item, dict)), item)


def typed_json(el: etree._Element, decl: XsdElement | None) -> Any:
    """Element to a JSON-ready value, typing leaves by the schema (BW JSON Render)."""
    kids = [c for c in el if isinstance(c.tag, str)]
    if not kids:
        return _typed_leaf((el.text or "").strip(), decl.simple_type if decl else None)
    out: dict[str, Any] = {}
    for c in kids:
        name = etree.QName(c).localname
        child_decl = decl.child(name) if decl else None
        value = typed_json(c, child_decl)
        if child_decl is not None and child_decl.repeating:
            out.setdefault(name, []).append(value)
        elif name in out:
            prev = out[name]
            out[name] = (prev if isinstance(prev, list) else [prev]) + [value]
        else:
            out[name] = value
    return out


def _typed_leaf(text: str, simple: str | None) -> Any:
    if simple in INT_TYPES:
        try:
            return int(text)
        except ValueError:
            return text
    if simple in ("decimal", "double", "float"):
        try:
            d = Decimal(text)
        except InvalidOperation:
            return text
        return int(d) if d == d.to_integral_value() and "." not in text else float(d)
    if simple == "boolean":
        return text in ("true", "1")
    return text


# --- seed data --------------------------------------------------------------------


_INSERT = re.compile(r"insert\s+into\s+([\w.]+)\s*\(([^)]*)\)\s*values\s*", re.IGNORECASE)


def _split_top(text: str, sep: str = ",") -> list[str]:
    out, depth, quote, start = [], 0, False, 0
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 1
                else:
                    quote = False
        elif c == "'":
            quote = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == sep and depth == 0:
            out.append(text[start:i])
            start = i + 1
        i += 1
    out.append(text[start:])
    return [s.strip() for s in out]


def _sql_literal(text: str) -> Any:
    t = text.strip()
    if t.lower() == "null":
        return None
    m = re.fullmatch(r"(?:date\s+)?'((?:[^']|'')*)'", t, re.IGNORECASE)
    if m:
        return m.group(1).replace("''", "'")
    if re.fullmatch(r"-?\d+(\.\d+)?", t):
        return t
    return t  # an expression such as ``current_date + 20``


class SeedData:
    """Rows of the estate's seed SQL plus the primary keys of its schema SQL."""

    def __init__(self, rows: dict[str, list[dict[str, Any]]], primary_keys: dict[str, str] | None = None):
        self.rows = rows
        self.primary_keys = primary_keys or {}

    @classmethod
    def from_sql(cls, seed_sql: str, schema_sql: str = "") -> SeedData:
        rows: dict[str, list[dict[str, Any]]] = {}
        text = re.sub(r"--[^\n]*", "", seed_sql)
        for m in _INSERT.finditer(text):
            table = m.group(1).split(".")[-1]
            cols = [c.strip() for c in m.group(2).split(",")]
            rest = text[m.end():]
            end = _statement_end(rest)
            for tup in _split_top(rest[:end]):
                if tup.startswith("(") and tup.endswith(")"):
                    vals = [_sql_literal(v) for v in _split_top(tup[1:-1])]
                    rows.setdefault(table, []).append(dict(zip(cols, vals, strict=False)))
        keys: dict[str, str] = {}
        for m in re.finditer(r"create\s+table\s+([\w.]+)\s*\((.*?)\);", schema_sql, re.IGNORECASE | re.DOTALL):
            for line in m.group(2).splitlines():
                pk = re.match(r"\s*(\w+)\s+.*primary\s+key", line, re.IGNORECASE)
                if pk:
                    keys[m.group(1).split(".")[-1]] = pk.group(1)
        return cls(rows, keys)

    @classmethod
    def for_app(cls, app: Application, process: Process | None = None) -> SeedData | None:
        root = estate_root(app, process)
        if root is None:
            return None
        seed, schema = "", ""
        for rel in app.sql_files:
            text = (root / rel).read_text(encoding="utf-8")
            if re.search(r"create\s+table", text, re.IGNORECASE):
                schema += text + "\n"
            if re.search(r"insert\s+into", text, re.IGNORECASE):
                seed += text + "\n"
        return cls.from_sql(seed, schema)

    def fresh_value(self, table: str, column: str) -> str | None:
        """A value of the same shape as the seeded ones, past the largest (for new keys)."""
        values = [str(r[column]) for r in self.rows.get(table, []) if r.get(column) is not None]
        best = None
        for v in values:
            m = re.search(r"(\d+)(?!.*\d)", v)
            if m and (best is None or int(m.group(1)) > best[0]):
                best = (int(m.group(1)), v, m)
        if best is None:
            return None
        n, v, m = best
        digits = m.group(1)
        return v[:m.start(1)] + str(n + 1).zfill(len(digits)) + v[m.end(1):]


def _statement_end(text: str) -> int:
    quote = False
    for i, c in enumerate(text):
        if c == "'":
            quote = not quote
        elif c == ";" and not quote:
            return i
    return len(text)


def estate_root(app: Application, process: Process | None = None) -> Path | None:
    """The folder ``app.sql_files`` are relative to, found above a process or contract path."""
    if not app.sql_files:
        return None
    starts = [Path(p.source_path) for p in ([process] if process else []) + list(app.processes)]
    starts += [Path(c.path) for c in app.contracts]
    for start in starts:
        for parent in start.parents:
            if (parent / app.sql_files[0]).exists():
                return parent
    return None


def snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


# --- WireMock stubs -------------------------------------------------------------------


@dataclass
class _Mapping:
    method: str
    path: str
    priority: int
    body_patterns: list[dict[str, Any]]
    response: StubResponse


def _json_path(body: Any, expression: str) -> Any:
    """Tiny ``$.a.b`` JSONPath (what the estate's WireMock mappings use)."""
    if not expression.startswith("$"):
        return None
    cur = body
    for part in [p for p in expression[1:].split(".") if p]:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


class WireMockStubs(StubRegistry):
    """A :class:`StubRegistry` answering from WireMock mapping files (``Stubs/*.json``).

    Requests are matched like WireMock does for the subset the estate uses: ``urlPath``,
    ``method`` and ``bodyPatterns`` with ``matchesJsonPath`` + ``equalTo``; the lowest
    ``priority`` wins. ``fixedDelayMilliseconds`` becomes ``delay_ms``, so a delay above the
    resource timeout is a timeout. The resource id is not used for matching (the mapping files
    are per service and paths are distinct); an explicit ``register`` still takes precedence.
    """

    def __init__(self, timeout_ms: int | None = None) -> None:
        if timeout_ms is None:
            super().__init__()
        else:
            super().__init__(timeout_ms)
        self.mappings: list[_Mapping] = []

    @classmethod
    def from_folder(cls, folder: Path, timeout_ms: int | None = None) -> WireMockStubs:
        reg = cls(timeout_ms)
        for f in sorted(Path(folder).glob("*.json")):
            doc = json.loads(f.read_text(encoding="utf-8"))
            for m in doc.get("mappings", [doc] if "request" in doc else []):
                req, resp = m.get("request", {}), m.get("response", {})
                body = resp.get("jsonBody")
                if body is None and resp.get("body"):
                    try:
                        body = json.loads(resp["body"])
                    except ValueError:
                        body = {"text": resp["body"]}
                reg.mappings.append(_Mapping(
                    method=str(req.get("method", "ANY")).upper(),
                    path=req.get("urlPath") or req.get("url") or "",
                    priority=int(m.get("priority", 5)),
                    body_patterns=req.get("bodyPatterns", []),
                    response=StubResponse(status=int(resp.get("status", 200)), body=body,
                                          delay_ms=int(resp.get("fixedDelayMilliseconds", 0))),
                ))
        reg.mappings.sort(key=lambda m: m.priority)
        return reg

    def match(self, resource_id: str, path: str, body: Any) -> StubResponse:
        try:
            return super()._lookup(resource_id, path)
        except LookupError:
            pass
        for m in self.mappings:
            if m.path != path:
                continue
            if all(self._pattern_ok(p, body) for p in m.body_patterns):
                return m.response
        raise LookupError(f"no WireMock mapping for {path} with body {body!r}")

    @staticmethod
    def _pattern_ok(pattern: dict[str, Any], body: Any) -> bool:
        jp = pattern.get("matchesJsonPath")
        if isinstance(jp, dict):
            value = _json_path(body if isinstance(body, dict) else {}, jp.get("expression", ""))
            if "equalTo" in jp:
                return value is not None and str(value) == str(jp["equalTo"])
            return value is not None
        if isinstance(jp, str):
            return _json_path(body if isinstance(body, dict) else {}, jp) is not None
        if "equalToJson" in pattern:
            want = pattern["equalToJson"]
            return body == (json.loads(want) if isinstance(want, str) else want)
        return True

    def _lookup(self, resource_id: str, path: str) -> StubResponse:
        try:
            return super()._lookup(resource_id, path)
        except LookupError:
            for m in self.mappings:
                if m.path == path and not m.body_patterns:
                    return m.response
            raise

    def call(self, resource_id: str, path: str, body: Any, timeout_ms: int | None = None) -> StubResponse:
        limit = self.timeout_ms if timeout_ms is None else timeout_ms
        response = self.match(resource_id, path, body)
        if response.delay_ms > limit:
            self.calls.append(StubCall(resource_id, path, body, "timeout"))
            raise TimeoutError(f"{resource_id} {path} timed out after {limit} ms")
        self.calls.append(StubCall(resource_id, path, body, "ok", response.status))
        return response


# --- starter contracts ------------------------------------------------------------------


def editor_href(act: Activity, key: str) -> str | None:
    refs = (act.config.get("settings") or {}).get(key) or []
    if isinstance(refs, dict):
        refs = [refs]
    return next((r.get("href") for r in refs if isinstance(r, dict) and r.get("href")), None)


def rest_body_decl(act: Activity, app: Application, schema: SchemaIndex) -> XsdElement | None:
    """The XSD element of a REST starter's JSON body (Swagger ``$ref`` -> global element)."""
    descriptor = str(act.config.get("service_descriptor") or "")
    for c in app.contracts:
        if c.kind != "json" or not descriptor or not c.id.endswith(descriptor.split("/")[-1]):
            continue
        text = c.text
        if not text and Path(c.path).exists():
            text = Path(c.path).read_text(encoding="utf-8")
        try:
            doc = json.loads(text or "{}")
        except ValueError:
            continue
        for op in doc.get("paths", {}).get(act.config.get("path"), {}).values():
            if not isinstance(op, dict):
                continue
            for p in op.get("parameters", []):
                ref = (p.get("schema") or {}).get("$ref", "")
                if p.get("in") == "body" and ref:
                    return schema.find(ref.split("/")[-1], path_hint=c.id.split("/")[0])
    return None


def rest_parameters_decl(act: Activity, process: Process, schema: SchemaIndex) -> XsdElement | None:
    """``<partnerLink><Verb>Parameters`` (BW6 REST path/query parameter element)."""
    verb = str(act.config.get("verb", "GET")).capitalize()
    name = f"{act.config.get('partner_link', '')}{verb}Parameters"
    return schema.find(name, path_hint=process.module or None)


def starter_shape(process: Process, app: Application, schema: SchemaIndex) -> dict[str | None, XsdElement | None]:
    """How a scenario input is laid out: ``{key: element}``; key ``None`` = the whole input.

    REST: ``{"body": ...}`` and/or ``{"parameters": ...}``; JMS: the message body element;
    subprocess: the ``Start`` element; BW5 HTTP: the declared parameters.
    """
    starter = process.starter
    if starter is None or starter.kind == "subprocess":
        var = next((v for v in process.variables if v.name == "Start"), None)
        return {None: schema.from_qname(var.schema if var else None)}
    act = next((a for a in process.activities if a.id == starter.activity_id), None)
    if act is None:
        return {None: None}
    if process.dialect == "bw5":
        params = starter.config.get("parameters") or []
        decl = XsdElement("parameters", None, complex=True, children=[
            XsdElement(p["name"], None, simple_type="string",
                       min_occurs=0 if p.get("repeating") == "Optional" else 1)
            for p in params if p.get("name")])
        return {None: decl}
    if starter.kind in ("rest", "http", "soap"):
        verb = str(act.config.get("verb", "POST")).upper()
        if verb in ("GET", "DELETE", "HEAD"):
            return {"parameters": rest_parameters_decl(act, process, schema)}
        shape: dict[str | None, XsdElement | None] = {"body": rest_body_decl(act, app, schema)}
        if "{" in str(act.config.get("path", "")):
            shape["parameters"] = rest_parameters_decl(act, process, schema)
        return shape
    if starter.kind == "jms":
        href = editor_href(act, "bodyEditorElement")
        return {None: schema.from_href(href) if href else None}
    return {None: None}


def starter_prefixes(process: Process) -> dict[str, str | None]:
    """XPath prefixes that address the starter input -> input key (``None`` = whole input)."""
    starter = process.starter
    if starter is None or starter.kind == "subprocess":
        return {"$Start": None}
    act = next((a for a in process.activities if a.id == starter.activity_id), None)
    if act is None:
        return {}
    if process.dialect == "bw5":
        name = act.name.replace(" ", "-")
        return {f"${name}/ProcessStarterOutput/parameters": None, f"${name}/ProcessStarterOutput": None}
    if starter.kind in ("rest", "http", "soap"):
        return {f"${act.name}.item": "body", f"${act.name}.parameters": "parameters"}
    if starter.kind == "jms":
        return {f"${act.name}/Body/*": None}
    return {f"${act.name}": None}


# --- predicate solving ---------------------------------------------------------------------

_NUM = r"-?\d+(?:\.\d+)?"
_PATH = r"\$[A-Za-z_][\w.-]*(?:/[\w.*:-]+(?:\[\d+\])?)*"
_OPS = {">": "gt", ">=": "ge", "<": "lt", "<=": "le", "=": "eq", "!=": "ne"}
_FLIP = {"gt": "le", "ge": "lt", "lt": "ge", "le": "gt", "eq": "ne", "ne": "eq"}
_MIRROR = {"gt": "lt", "ge": "le", "lt": "gt", "le": "ge", "eq": "eq", "ne": "ne"}


@dataclass(frozen=True)
class Atom:
    """One solvable comparison: ``measure(path) op value``.

    ``measure`` is ``num`` (``number()``/``xsd:integer()``/bare path vs a number), ``len``
    (``string-length``), ``str`` (string equality), ``exists``, ``in`` (membership in a set of
    strings) or ``matches`` (a regular expression).
    """

    measure: str
    path: str
    op: str
    value: Any = None

    def negate(self) -> Atom:
        if self.measure == "exists":
            return Atom("exists", self.path, "eq", not self.value)
        if self.measure in ("in", "matches"):
            return Atom(self.measure, self.path, _FLIP[self.op], self.value)
        return Atom(self.measure, self.path, _FLIP[self.op], self.value)


def _split_bool(expr: str, word: str) -> list[str]:
    parts, depth, quote, start, i = [], 0, "", 0, 0
    token = f" {word} "
    while i < len(expr):
        c = expr[i]
        if quote:
            quote = "" if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif depth == 0 and expr.startswith(token, i):
            parts.append(expr[start:i])
            start = i + len(token)
            i = start
            continue
        i += 1
    parts.append(expr[start:])
    return [p.strip() for p in parts]


def _strip_parens(expr: str) -> str:
    expr = expr.strip()
    while expr.startswith("(") and expr.endswith(")"):
        depth = 0
        for i, c in enumerate(expr):
            depth += c == "("
            depth -= c == ")"
            if depth == 0 and i < len(expr) - 1:
                return expr
        expr = expr[1:-1].strip()
    return expr


def parse_condition(expr: str) -> list[list[Atom | None]]:
    """Disjunctive form of a transition condition: ``[[atom and atom], or [...]]``.

    An atom the solver does not understand is ``None`` (the conjunction is then only partly
    solvable and the coverage loop has to confirm it).
    """
    out = []
    for disjunct in _split_bool(_strip_parens(expr), "or"):
        out.append([parse_atom(a) for a in _split_bool(_strip_parens(disjunct), "and")])
    return out


def parse_atom(text: str) -> Atom | None:
    t = _strip_parens(text)
    neg = re.fullmatch(r"not\s*\((.*)\)", t, re.DOTALL)
    if neg:
        inner = parse_atom(neg.group(1))
        return inner.negate() if inner else None
    m = re.fullmatch(rf"(exists|empty)\(\s*({_PATH})\s*\)", t)
    if m:
        return Atom("exists", m.group(2), "eq", m.group(1) == "exists")
    m = re.fullmatch(rf"matches\(\s*({_PATH})\s*,\s*'([^']*)'\s*\)", t)
    if m:
        return Atom("matches", m.group(1), "eq", m.group(2))
    m = re.fullmatch(rf"({_PATH})\s*(!?=)\s*\(((?:\s*'[^']*'\s*,?)+)\)", t)
    if m:
        values = tuple(re.findall(r"'([^']*)'", m.group(3)))
        return Atom("in", m.group(1), "eq" if m.group(2) == "=" else "ne", values)
    m = re.fullmatch(r"(.+?)\s*(>=|<=|!=|=|>|<)\s*(.+)", t)
    if not m:
        return None
    left, op, right = m.group(1).strip(), _OPS[m.group(2)], m.group(3).strip()
    if re.fullmatch(_NUM, left) or re.fullmatch(r"'[^']*'", left):
        left, right, op = right, left, _MIRROR[op]
    measure, path = _operand(left)
    if path is None:
        return None
    if re.fullmatch(_NUM, right):
        value: Any = Decimal(right)
        if measure == "str":
            measure = "num"
    elif re.fullmatch(r"'[^']*'|\"[^\"]*\"", right):
        value = right[1:-1]
        if measure != "str":
            return None
    else:
        return None
    return Atom(measure, path, op, value)


def _operand(text: str) -> tuple[str, str | None]:
    t = _strip_parens(text)
    m = re.fullmatch(rf"(?:number|xsd?:integer|xsd?:int|xsd?:decimal|xsd?:double)\(\s*({_PATH})\s*\)", t)
    if m:
        return "num", m.group(1)
    m = re.fullmatch(rf"string-length\(\s*({_PATH})\s*\)", t)
    if m:
        return "len", m.group(1)
    m = re.fullmatch(rf"(?:string|normalize-space)\(\s*({_PATH})\s*\)", t)
    if m:
        return "str", m.group(1)
    if re.fullmatch(_PATH, t):
        return "str", t
    return "?", None


_TRANSPARENT = re.compile(
    r"^(?:normalize-space|upper-case|lower-case|string|number|tib:trim|xsd?:\w+)\((.*)\)$", re.DOTALL)


def _local_path(path: str) -> tuple[str, tuple[str, ...]]:
    """``$X/tns:a/b[1]`` -> (``$X``, (``a``, ``b``))."""
    var, *steps = path.split("/")
    return var, tuple(re.sub(r"\[\d+\]$", "", s).split(":")[-1] for s in steps if s)


class _Resolver:
    """Map a condition's XPath path to a scenario input path (through mapper aliases)."""

    def __init__(self, process: Process) -> None:
        self.prefixes = starter_prefixes(process)
        self.aliases: dict[tuple[str, tuple[str, ...]], tuple[str | None, tuple[str, ...]]] = {}
        names = {a.name: a for a in process.activities}
        var_of = (lambda a: "$" + a.name.replace(" ", "-")) if process.dialect == "bw5" else (lambda a: "$" + a.name)
        for act in process.activities:
            if act.type not in ("mapper", "assign") or act.name not in names:
                continue
            for b in act.input_bindings:
                expr = b.expression.strip()
                while True:
                    m = _TRANSPARENT.match(expr)
                    if not m or "," in m.group(1):
                        break
                    expr = m.group(1).strip()
                if not re.fullmatch(_PATH, expr):
                    continue
                target = self._starter(expr)
                if target is None:
                    continue
                # BW6: $Mapper is the output element; BW5: $Mapper wraps it (root in the path)
                steps = tuple(b.target.split("/")[0 if process.dialect == "bw5" else 1:])
                self.aliases[(var_of(act), steps)] = target

    def _starter(self, path: str) -> tuple[str | None, tuple[str, ...]] | None:
        for prefix in sorted(self.prefixes, key=len, reverse=True):
            if prefix.endswith("/*"):
                base = prefix[:-2]
                if path.startswith(base + "/"):
                    rest = path[len(base) + 1:].split("/", 1)
                    steps = _local_path("$x/" + rest[1])[1] if len(rest) > 1 else ()
                    return self.prefixes[prefix], steps
            elif path == prefix or path.startswith(prefix + "/"):
                return self.prefixes[prefix], _local_path("$x" + path[len(prefix):])[1]
        return None

    def resolve(self, path: str) -> tuple[str | None, tuple[str, ...]] | None:
        direct = self._starter(path)
        if direct is not None:
            return direct
        var, steps = _local_path(path)
        return self.aliases.get((var, steps))


def _get(data: dict, key: str | None, steps: tuple[str, ...]) -> Any:
    cur: Any = data.get(key) if key else data
    for s in steps:
        if not isinstance(cur, dict) or s not in cur:
            return None
        cur = cur[s]
    return cur


def _set(data: dict, key: str | None, steps: tuple[str, ...], value: Any, *, remove: bool = False) -> None:
    cur = data.setdefault(key, {}) if key else data
    for s in steps[:-1]:
        cur = cur.setdefault(s, {})
    if remove:
        cur.pop(steps[-1], None)
    else:
        cur[steps[-1]] = value


def _num(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _solve_path(atoms: list[Atom], current: Any, integer: bool) -> tuple[bool, Any]:
    """A value satisfying every atom on one path (``(False, None)`` when contradictory).

    Returns ``(True, _REMOVE)`` when the field should be absent.
    """
    if all(_holds(a, current) for a in atoms):
        return True, current
    candidates: list[Any] = []
    step = Decimal(1) if integer else Decimal("0.01")
    for a in atoms:
        if a.measure == "num" and isinstance(a.value, Decimal):
            v = a.value
            candidates += {"gt": [v + step], "ge": [v], "lt": [v - step], "le": [v],
                           "eq": [v], "ne": [v + step, v - step]}[a.op]
        elif a.measure == "len":
            n = int(a.value)
            want = {"gt": n + 1, "ge": n, "lt": n - 1, "le": n, "eq": n, "ne": n + 1}[a.op]
            if want <= 0:
                candidates += [_REMOVE, ""]
            else:
                base = str(current or "")
                candidates.append((base + "X" * want)[:want])
        elif a.measure == "str":
            candidates.append(a.value if a.op == "eq" else f"{a.value}X")
        elif a.measure == "in":
            candidates.append(a.value[0] if a.op == "eq" else "ZZ")
        elif a.measure == "exists":
            candidates.append(current if a.value else _REMOVE)
        elif a.measure == "matches" and a.op == "ne":
            candidates.append("INVALID")
    for c in candidates:
        if c is not _REMOVE and isinstance(c, Decimal):
            c = int(c) if integer else c
        if all(_holds(a, c) for a in atoms):
            return True, c
    return False, None


class _Remove:
    def __repr__(self) -> str:
        return "<remove>"


_REMOVE = _Remove()


def _holds(atom: Atom, value: Any) -> bool:
    absent = value is None or value is _REMOVE
    if atom.measure == "exists":
        return (not absent) == bool(atom.value)
    if atom.measure == "len":
        n = 0 if absent else len(str(value))
        return _cmp(Decimal(n), atom.op, atom.value)
    if absent:
        return atom.op == "ne" and atom.measure in ("str", "in")
    if atom.measure == "num":
        v = _num(value)
        return v is not None and _cmp(v, atom.op, atom.value)
    if atom.measure == "str":
        return (str(value) == atom.value) == (atom.op == "eq")
    if atom.measure == "in":
        return (str(value) in atom.value) == (atom.op == "eq")
    if atom.measure == "matches":
        return bool(re.search(atom.value, str(value))) == (atom.op == "eq")
    return False


def _cmp(v: Decimal, op: str, ref: Decimal) -> bool:
    return {"gt": v > ref, "ge": v >= ref, "lt": v < ref, "le": v <= ref, "eq": v == ref, "ne": v != ref}[op]


# --- generation ----------------------------------------------------------------------------


@dataclass
class _Ctx:
    process: Process
    app: Application
    schema: SchemaIndex
    seed: SeedData | None
    shape: dict[str | None, XsdElement | None]
    written: set[str]


def _written_tables(process: Process, app: Application, seen: set[str] | None = None) -> set[str]:
    seen = seen or set()
    if process.id in seen:
        return set()
    seen.add(process.id)
    tables: set[str] = set()
    for a in process.activities:
        sql = str(a.config.get("sql") or "")
        m = re.match(r"\s*(?:insert\s+into|update)\s+([\w.\"]+)", sql, re.IGNORECASE)
        if m:
            tables.add(m.group(1).replace('"', "").split(".")[-1])
    by_id = {p.id: p for p in app.processes}
    for c in process.calls:
        if c.target_process in by_id:
            tables |= _written_tables(by_id[c.target_process], app, seen)
    return tables


def _match_column(table_cols: Iterable[str], name: str, parent: str | None) -> str | None:
    cols = list(table_cols)
    s = snake(name)
    if s in cols:
        return s
    if parent and f"{snake(parent)}_{s}" in cols:
        return f"{snake(parent)}_{s}"
    for c in cols:
        if c.startswith(f"{s}_") and c.endswith(("_code", "_id", "_number")):
            return c
    return None


def _default_value(decl: XsdElement) -> Any:
    if decl.enumerations:
        return decl.enumerations[0]
    t = decl.simple_type or "string"
    if t in INT_TYPES:
        return 1
    if t in NUMBER_TYPES:
        return "100.00"
    if t == "boolean":
        return False
    if t == "date":
        return dt.date.today().isoformat()
    if t == "dateTime":
        return dt.datetime.now().replace(microsecond=0).isoformat()
    if decl.pattern:
        return _sample_pattern(decl.pattern)
    return f"{decl.name.upper()}-1"


def _sample_pattern(pattern: str) -> str:
    """A string for the simple patterns XSD contracts use (``[0-9]{3}-[0-9]{2}``, ``[A-Z]{2}``)."""
    out = []
    for m in re.finditer(r"\[([^\]]+)\](\{(\d+)(?:,\d*)?\})?|\\d(\{(\d+)\})?|(\\?.)", pattern.strip("^$")):
        if m.group(1):
            cls, n = m.group(1), int(m.group(3) or 1)
            ch = "0" if "0" in cls else ("A" if "A" in cls else cls[0])
            out.append(ch * n)
        elif m.group(0).startswith("\\d"):
            out.append("0" * int(m.group(5) or 1))
        elif m.group(6):
            out.append(m.group(6).lstrip("\\"))
    return "".join(out)


def _coerce(value: Any, decl: XsdElement) -> Any:
    if value is None:
        return None
    t = decl.simple_type
    if t in INT_TYPES:
        n = _num(value)
        return int(n) if n is not None else value
    if t == "boolean":
        return str(value).lower() in ("true", "t", "1")
    if t == "date":
        return str(value)[:10]
    return str(value)


def _happy_value(ctx: _Ctx, decl: XsdElement, k: int, table: str | None = None,
                 parent: str | None = None) -> Any:
    if not decl.complex:
        return _default_value(decl)
    rows = ctx.seed.rows if ctx.seed else {}
    leaves = [c for c in decl.children if not c.complex]

    def score(tbl: str) -> int:
        cols = rows[tbl][0].keys() if rows.get(tbl) else []
        return sum(1 for c in leaves if _match_column(cols, c.name, decl.name))

    def pk_hits(tbl: str) -> int:
        pk = ctx.seed.primary_keys.get(tbl) if ctx.seed else None
        return sum(1 for c in leaves if pk and _match_column([pk], c.name, decl.name))

    if table is None or score(table) == 0:
        # most matching columns; then the table the ids are the primary key of; then the larger
        scored = sorted(rows, key=lambda t: (-score(t), -pk_hits(t), -len(rows[t]), t))
        table = scored[0] if scored and score(scored[0]) > 0 else None
    out: dict[str, Any] = {}
    for c in decl.children:
        if c.complex:
            out[c.name] = _happy_value(ctx, c, k, table, decl.name)
            continue
        value: Any = None
        found = False
        for tbl in ([table] if table else []) + sorted(rows):
            data = rows.get(tbl) or []
            col = _match_column(data[0].keys(), c.name, decl.name) if data else None
            if col is None:
                continue
            if ctx.seed and tbl in ctx.written and ctx.seed.primary_keys.get(tbl) == col:
                value = ctx.seed.fresh_value(tbl, col)
            else:
                value = data[k % len(data)].get(col)
                if isinstance(value, str) and re.search(r"current_|interval|\+|\(", value):
                    continue  # an SQL expression, not a usable literal
            found = value is not None
            if found:
                break
        if not found and table and rows.get(table):
            # last resort, same table only: a column sharing the field's last word
            # (fundingAmount ~ approved_amount)
            last = snake(c.name).split("_")[-1]
            col = next((k for k in rows[table][0] if k.split("_")[-1] == last), None)
            if col is not None:
                value = rows[table][k % len(rows[table])].get(col)
                found = value is not None and not (isinstance(value, str) and re.search(r"current_|interval", value))
        out[c.name] = _coerce(value, c) if found else _default_value(c)
    return out


def happy_input(ctx: _Ctx, k: int = 0) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, decl in ctx.shape.items():
        value = _happy_value(ctx, decl, k) if decl is not None else {}
        if key is None:
            data.update(value if isinstance(value, dict) else {})
        else:
            data[key] = value
    return data


def _leaf_decl(ctx: _Ctx, key: str | None, steps: tuple[str, ...]) -> XsdElement | None:
    decl = ctx.shape.get(key)
    for s in steps:
        decl = decl.child(s) if decl else None
    return decl


def _solve(ctx: _Ctx, resolver: _Resolver, base: dict[str, Any], atoms: list[Atom]) -> dict[str, Any] | None:
    by_path: dict[tuple[str | None, tuple[str, ...]], list[Atom]] = {}
    for a in atoms:
        target = resolver.resolve(a.path)
        if target is None:
            continue  # not an input field: keep the base value (the coverage loop confirms)
        by_path.setdefault(target, []).append(a)
    if not by_path:
        return None
    data = copy.deepcopy(base)
    for (key, steps), path_atoms in by_path.items():
        if not steps:
            return None
        decl = _leaf_decl(ctx, key, steps)
        integer = bool(decl and decl.simple_type in INT_TYPES)
        ok, value = _solve_path(path_atoms, _get(data, key, steps), integer)
        if not ok:
            return None
        if value is _REMOVE:
            if decl is not None and decl.min_occurs > 0:
                value = ""
            else:
                _set(data, key, steps, None, remove=True)
                continue
        if isinstance(value, Decimal):
            value = str(value)
        _set(data, key, steps, value)
    return data


def _branch_atoms(target: Transition, siblings: list[Transition]) -> list[list[Atom]]:
    """Alternative atom sets that make ``target`` the transition BW takes."""
    earlier = [t for t in siblings if t.kind == "conditional" and t.expression
               and (target.kind == "otherwise" or siblings.index(t) < siblings.index(target))]
    negations: list[Atom] = []
    for t in earlier:
        parsed = parse_condition(t.expression or "")
        # not(A or B) = not A and not B; not(a and b): negate the first solvable atom
        for disjunct in parsed:
            atom = next((a for a in disjunct if a is not None), None)
            if atom is not None:
                negations.append(atom.negate())
    if target.kind == "otherwise":
        return [negations]
    options = []
    for disjunct in parse_condition(target.expression or ""):
        known = [a for a in disjunct if a is not None]
        if known:
            options.append(known + negations)
    return options


def _scenario_id(process: Process, *parts: str) -> str:
    return ":".join([process.name, *parts])


def _http_targets(process: Process, app: Application, seen: set[str] | None = None) -> list[tuple[str, str, Resource | None, str]]:
    """(resource id, path, resource, activity name) of every HTTP call reachable from ``process``."""
    seen = seen or set()
    if process.id in seen:
        return []
    seen.add(process.id)
    resources = {r.id: r for r in app.resources}
    out = []
    for a in process.activities:
        if a.type not in ("http.invoke", "rest.invoke", "soap.invoke"):
            continue
        path = next((b.expression.strip().strip("\"'") for b in a.input_bindings
                     if b.target.endswith("RequestURI") and re.fullmatch(r"\"[^\"]*\"|'[^']*'", b.expression.strip())),
                    str(a.config.get("path") or "/"))
        rid = str(a.config.get("resource_ref") or a.id)
        out.append((rid, path, resources.get(rid), a.name))
    by_id = {p.id: p for p in app.processes}
    for c in process.calls:
        if c.target_process in by_id:
            out += _http_targets(by_id[c.target_process], app, seen)
    return out


def generate_scenarios(process: Process, contracts: list[Contract], app: Application,
                       seed: SeedData | None = None, *, variant: int = 0) -> list[Scenario]:
    """Static scenario set of one process (``expected`` is left empty, see ``fill_expected``).

    * ``happy``: values from the request contract and seed row ``variant`` (ids reused so
      lookups hit; keys of tables the process inserts into are fresh).
    * ``branch`` / ``fault``: one per conditional or otherwise transition whose predicate is
      solvable on the input (``fault`` when the transition leads to a ``throw``).
    * ``external_failure``: one timeout and one 5xx per HTTP call.
    """
    schema = SchemaIndex(contracts)
    seed = seed if seed is not None else SeedData.for_app(app, process)
    ctx = _Ctx(process, app, schema, seed, starter_shape(process, app, schema), _written_tables(process, app))
    base = happy_input(ctx, variant)
    scenarios = [Scenario(id=_scenario_id(process, "happy"), process_id=process.id,
                          name="happy path", kind="happy", input=base)]
    resolver = _Resolver(process)
    throws = {a.id for a in process.activities if a.type == "throw"}
    seen_inputs = [base]
    by_source: dict[str, list[Transition]] = {}
    for t in process.transitions:
        by_source.setdefault(t.from_id, []).append(t)
    for source, outs in by_source.items():
        if not any(t.kind == "conditional" for t in outs):
            continue
        branches = list(outs)
        if not any(t.kind == "otherwise" for t in outs):
            # no otherwise link: when no condition holds BW takes no transition (the job ends)
            branches.append(Transition(id=f"{source}:no-transition", from_id=source, to_id="",
                                       kind="otherwise"))
        for t in branches:
            if t.kind not in ("conditional", "otherwise"):
                continue
            data = None
            for atoms in _branch_atoms(t, branches):
                data = _solve(ctx, resolver, base, atoms)
                if data is not None:
                    break
            if data is None or data in seen_inputs:
                continue
            seen_inputs.append(data)
            kind = "fault" if t.to_id in throws else "branch"
            name = f"{t.from_id} -> {t.to_id}" if t.to_id else f"{t.from_id}: no condition holds"
            scenarios.append(Scenario(id=_scenario_id(process, kind, t.id), process_id=process.id,
                                      name=name, kind=kind, input=data))
    for rid, path, resource, name in _http_targets(process, app):
        timeout = int((resource.settings.get("timeout_ms") if resource else None) or 30_000)
        key = f"{rid}{path}"
        label = resource.name if resource else name
        scenarios.append(Scenario(
            id=_scenario_id(process, "timeout", label), process_id=process.id,
            name=f"{label} times out", kind="external_failure", input=copy.deepcopy(base),
            stub_responses={key: StubResponse(status=200, body=None, delay_ms=timeout + 1)}))
        scenarios.append(Scenario(
            id=_scenario_id(process, "5xx", label), process_id=process.id,
            name=f"{label} returns 503", kind="external_failure", input=copy.deepcopy(base),
            stub_responses={key: StubResponse(status=503, body={"error": "SERVICE_UNAVAILABLE"})}))
    return scenarios


def _covered(scenario: Scenario) -> set[str]:
    out: set[str] = set()
    for step in scenario.expected.trace if scenario.expected else []:
        if "/" in step.activity_id:
            continue
        out.add(step.activity_id)
        out.update((step.chosen_transition or "").split(",") if step.chosen_transition else [])
    return out


def _quality(result: Any) -> tuple[int, int, int]:
    """Happy-path ranking: no fault or error reply, then no error link, then the longer path."""
    status = result.output.get("status") if isinstance(result.output, dict) else None
    ok = result.fault is None and not (isinstance(status, int) and status >= 400)
    errors = sum(1 for s in result.trace if s.output.get("fault"))
    return (1 if ok else 0, -errors, len(result.trace))


def _mutations(data: dict[str, Any], prefix: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    for key, value in data.items():
        path = (*prefix, key)
        if isinstance(value, dict):
            yield from _mutations(value, path)
        elif isinstance(value, str) and value:
            number = _num(value)
            if number is not None:
                yield path, str(number * 2)
                yield path, "0"
            else:
                yield path, value[:-1] + ("Y" if value.endswith("X") else "X")
            yield path, ""
        elif isinstance(value, bool):
            yield path, not value
        elif isinstance(value, int):
            yield path, value * 2
            yield path, 0


async def fill_expected(scenarios: list[Scenario], evaluator: Evaluator) -> list[Scenario]:
    """Run every scenario through the evaluator; store the expected result and stub responses."""
    for sc in scenarios:
        sc.expected = await evaluator.run(sc.process_id, sc)
        for key, response in evaluator.last_stub_responses.items():
            sc.stub_responses.setdefault(key, response)
    return scenarios


async def generate_and_evaluate(process: Process, contracts: list[Contract], app: Application,
                                evaluator: Evaluator, seed: SeedData | None = None,
                                *, max_variants: int = 10) -> list[Scenario]:
    """Static scenarios plus coverage closure, all with ``expected`` filled.

    The happy path is the first seed row whose run ends without a fault, an error reply or an
    error transition. Transitions (and ``throw`` activities) still uncovered afterwards, such as
    predicates over JDBC results, are searched for among the other seed rows and single-field
    mutations of the happy input; each one found becomes a ``branch`` or ``fault`` scenario.
    """
    seed = seed if seed is not None else SeedData.for_app(app, process)
    schema = SchemaIndex(contracts)
    ctx = _Ctx(process, app, schema, seed, starter_shape(process, app, schema), _written_tables(process, app))
    n_rows = max((len(r) for r in (seed.rows.values() if seed else [])), default=1)
    probes: list[tuple[int, dict[str, Any], Any]] = []
    best = 0
    best_quality: tuple[int, int, int] | None = None
    for k in range(min(n_rows, max_variants)):
        data = happy_input(ctx, k)
        if any(p[1] == data for p in probes):
            continue
        result = await evaluator.run(process.id, Scenario(id="probe", process_id=process.id, name="probe",
                                                          kind="happy", input=data))
        probes.append((k, data, result))
        q = _quality(result)
        if best_quality is None or q > best_quality:
            best, best_quality = k, q
    scenarios = generate_scenarios(process, contracts, app, seed, variant=best)
    await fill_expected(scenarios, evaluator)
    covered: set[str] = set().union(*(_covered(s) for s in scenarios))
    targets = [t.id for t in process.transitions if t.kind in ("conditional", "otherwise")]
    targets += [a.id for a in process.activities if a.type == "throw"]
    throws = {a.id for a in process.activities if a.type == "throw"}
    leads_to = {t.id: t.to_id for t in process.transitions}
    missing = [t for t in targets if t not in covered]
    if not missing:
        return scenarios
    base = scenarios[0].input
    candidates: list[tuple[str, dict[str, Any]]] = [(f"seed row {k + 1}", d) for k, d, _ in probes if k != best]
    candidates += [(f"seed row {k + 1}", happy_input(ctx, k)) for k in range(min(n_rows, max_variants))
                   if all(p[0] != k for p in probes)]
    for path, value in _mutations(base):
        data = copy.deepcopy(base)
        _set(data, None, path, value)
        candidates.append((f"{'.'.join(path)} = {value!r}", data))
    tried: list[dict[str, Any]] = [s.input for s in scenarios]
    for label, data in candidates:
        if not missing:
            break
        if data in tried:
            continue
        tried.append(data)
        probe = Scenario(id="probe", process_id=process.id, name=label, kind="branch", input=data)
        await fill_expected([probe], evaluator)
        hit = [t for t in missing if t in _covered(probe)]
        if not hit:
            continue
        first = hit[0]
        kind = "fault" if first in throws or leads_to.get(first) in throws else "branch"
        probe.id = _scenario_id(process, kind, first)
        probe.kind = kind
        probe.name = f"{first} ({label})"
        scenarios.append(probe)
        missing = [t for t in missing if t not in hit]
    return scenarios
