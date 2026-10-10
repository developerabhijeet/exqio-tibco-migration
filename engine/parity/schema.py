"""The estate's XSD contracts as element declarations (design spec section 6.2).

:class:`SchemaIndex` resolves global elements by ``(namespace, name)`` with their types,
enumerations, patterns, fraction digits and ``elementFormDefault``. The evaluator uses it to
turn scenario dicts into correctly qualified XML (``$post.item/tns:applicant/tns:ssn``) and to
type JSON (JSON Render); the scenario generator uses it to produce schema-valid values.
The starter helpers say how a process's scenario input is laid out.

``engine/domains/graph.py`` has its own XSD index, keyed by local name only; it is not reused
here because the evaluator needs namespace-qualified declarations (see the Task 12 report).
"""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from lxml import etree

from engine.ir.models import Activity, Application, Contract, Process

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
    fraction_digits: int | None = None

    def at(self, steps: Iterable[str]) -> XsdElement | None:
        node: XsdElement | None = self
        for s in steps:
            node = node.child(s) if node else None
        return node

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
            elif facet.tag == f"{_XS}fractionDigits":
                decl.fraction_digits = int(facet.get("value"))

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

