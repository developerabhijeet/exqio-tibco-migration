"""The neutral contract model: XSD, WSDL and JSON Schema walked once, rendered per stack.

``contract_model`` walks a contract (lxml for XSD and the schemas inside a WSDL; JSON Schema and
Swagger 2.0 ``definitions``/``$defs``) into records, enums, lists, optionality and lineage paths,
with no target language in it: property types are the neutral scalars in ``SCALARS`` or the name
of a record or enum of the same model. Each stack renders the model in its own language
(``engine/dotnet_gen/csharp_contracts`` for C#).

``plan_contracts`` assigns every contract of one application its namespace, file and record names
through the stack's ``StackNaming.contract_target``, so record names never collide within a
namespace and two contracts never share a file.

Names: records, enums and properties are PascalCase identifiers (``dealerId`` -> ``DealerId``),
unique within the model (records and enums) or the record (properties), and a property never
takes its record's name (it gets a ``Value`` suffix). A stack derives its own casing from them.

Lineage path: the element path from the first global element (document order, depth first) that
reaches the record; a record no element reaches is rooted at its own name.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from lxml import etree

from engine.ir.models import Contract

if TYPE_CHECKING:
    from engine.stacks.base import StackNaming

XS = "http://www.w3.org/2001/XMLSchema"

# The neutral scalar types a property can have (anything else is a record or enum name).
SCALARS: frozenset[str] = frozenset({
    "string", "int", "long", "ulong", "decimal", "double", "float", "bool",
    "date", "datetime", "time", "bytes", "any",
})

_XS_TYPES: dict[str, str] = {
    "string": "string", "normalizedString": "string", "token": "string", "anyURI": "string",
    "QName": "string", "NCName": "string", "Name": "string", "ID": "string", "IDREF": "string",
    "NMTOKEN": "string", "language": "string", "duration": "string", "gYear": "string",
    "gYearMonth": "string", "gMonthDay": "string", "gDay": "string", "gMonth": "string",
    "int": "int", "integer": "int", "short": "int", "byte": "int", "unsignedShort": "int",
    "unsignedByte": "int", "nonNegativeInteger": "int", "positiveInteger": "int",
    "negativeInteger": "int", "nonPositiveInteger": "int",
    "long": "long", "unsignedInt": "long", "unsignedLong": "ulong",
    "decimal": "decimal", "double": "double", "float": "float", "boolean": "bool",
    "date": "date", "dateTime": "datetime", "time": "time",
    "base64Binary": "bytes", "hexBinary": "bytes", "anyType": "any", "anySimpleType": "string",
}

_UNBOUNDED = 10**9


# --------------------------------------------------------------------- naming
def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^0-9A-Za-z]+", name) if t]


def pascal(name: str) -> str:
    """``dealerId`` -> ``DealerId``, ``ON-HOLD`` -> ``OnHold``, ``GMF.DB.URL`` -> ``GmfDbUrl``."""
    out = []
    for t in _tokens(name):
        if t.isupper() and len(t) > 1:
            out.append(t[0] + t[1:].lower())
        else:
            out.append(t[0].upper() + t[1:])
    return "".join(out)


def _camel(name: str) -> str:
    return name[:1].lower() + name[1:] if name else name


def ident(name: str, fallback: str = "Value") -> str:
    """PascalCase identifier; ``fallback`` when empty, prefixed with it when it starts with a digit."""
    p = pascal(name)
    if not p:
        return fallback
    return f"{fallback}{p}" if p[0].isdigit() else p


# ---------------------------------------------------------------------- model
@dataclass
class ContractProp:
    name: str  # PascalCase identifier, unique in the record
    json_name: str  # the name on the wire (JSON property name)
    type_ref: str  # a neutral scalar (``SCALARS``) or a record or enum name; no list or optional decoration
    required: bool
    repeated: bool
    path_part: str  # the XML step under the record: "name", "@name", or "" for simple content
    child: str | None = None  # the nested record lineage walks into, when the type is a record
    enum_values: list[str] = field(default_factory=list)  # the values when ``type_ref`` is an enum
    tibco_path: str = ""  # lineage: the record's path plus ``path_part``
    # The element, attribute or JSON property name as the contract spells it, before any casing:
    # what a stack's naming cases its own way (``name`` is the PascalCase .NET-era identifier).
    raw_name: str = ""


@dataclass
class ContractRecord:
    name: str
    doc: str | None = None
    props: list[ContractProp] = field(default_factory=list)
    tibco_path: str = ""  # lineage path of the record


@dataclass
class ContractEnum:
    name: str
    values: list[str]


@dataclass
class ContractModel:
    """Records and enums of one contract, in emission order."""

    records: dict[str, ContractRecord] = field(default_factory=dict)
    enums: dict[str, ContractEnum] = field(default_factory=dict)
    root_paths: list[tuple[str, str]] = field(default_factory=list)  # (record, "/Root") in document order

    def names(self) -> list[str]:
        """Record names, then enum names, in emission order."""
        return list(self.records) + list(self.enums)

    def lineage(self) -> dict[str, str]:
        """Record name -> lineage path, in walk order (roots first, depth first, then the rest)."""
        out: dict[str, str] = {}

        def walk(rec_name: str, path: str) -> None:
            if rec_name in out or rec_name not in self.records:
                return
            out[rec_name] = path
            for p in self.records[rec_name].props:
                if p.child:
                    walk(p.child, f"{path}/{p.path_part}" if p.path_part else path)

        for rec_name, path in self.root_paths:
            walk(rec_name, path)
        for rec_name in self.records:
            walk(rec_name, f"/{rec_name}")
        return out


class _Draft:
    """A model under construction: name claims against ``reserved`` and the current model."""

    def __init__(self, reserved: frozenset[str] = frozenset(), suffix: str = "") -> None:
        self.model = ContractModel()
        self.records = self.model.records
        self.enums = self.model.enums
        self.root_paths = self.model.root_paths
        self.taken: set[str] = set()
        self.reserved = reserved  # names another contract of the same namespace already emits
        self.suffix = suffix  # appended to a name that is reserved

    def _free(self, cand: str) -> bool:
        return bool(cand) and cand not in self.taken and cand not in self.reserved

    def claim(self, preferred: str, alt: str) -> str:
        cands = [preferred]
        if preferred in self.reserved and self.suffix:
            cands.append(preferred + self.suffix)
        cands += [alt, alt + self.suffix] if self.suffix else [alt]
        for cand in cands:
            if self._free(cand):
                self.taken.add(cand)
                return cand
        n = 2
        while not self._free(f"{alt}{n}"):
            n += 1
        self.taken.add(f"{alt}{n}")
        return f"{alt}{n}"

    @staticmethod
    def finish_props(rec: ContractRecord) -> None:
        seen: dict[str, int] = {}
        for p in rec.props:
            if p.name == rec.name:
                p.name = f"{p.name}Value"
            if p.name in seen:
                seen[p.name] += 1
                p.name = f"{p.name}{seen[p.name]}"
            else:
                seen[p.name] = 1

    def finish(self) -> ContractModel:
        """Fill lineage paths and enum values."""
        m = self.model
        paths = m.lineage()
        for rec in m.records.values():
            rec.tibco_path = paths.get(rec.name, f"/{rec.name}")
            for p in rec.props:
                p.tibco_path = f"{rec.tibco_path}/{p.path_part}" if p.path_part else rec.tibco_path
                p.enum_values = list(m.enums[p.type_ref].values) if p.type_ref in m.enums else []
        return m


# ------------------------------------------------------------------------ XSD
def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _xs_children(el, *names: str):
    return [c for c in el if isinstance(c.tag, str) and c.tag.startswith(f"{{{XS}}}")
            and (not names or _local(c.tag) in names)]


def _occurs(el, attr: str, default: int) -> int:
    v = el.get(attr)
    if v is None:
        return default
    if v == "unbounded":
        return _UNBOUNDED
    try:
        return int(v)
    except ValueError:
        return default


def _doc(el) -> str | None:
    for ann in _xs_children(el, "annotation"):
        for d in _xs_children(ann, "documentation"):
            text = "".join(d.itertext()).strip()
            if text:
                return text
    return None


class _XsdBuilder:
    def __init__(self, schemas: list[Any], draft: _Draft) -> None:
        self.m = draft
        self.cts: dict[str, Any] = {}
        self.sts: dict[str, Any] = {}
        self.elements: dict[str, Any] = {}
        self.global_order: list[Any] = []
        self.ct_records: dict[str, str] = {}  # complexType name -> record name
        self.el_types: dict[str, tuple[str, str | None]] = {}  # global element -> (type, record)
        self.st_enums: dict[str, str] = {}
        for schema in schemas:
            for c in _xs_children(schema):
                name = c.get("name")
                kind = _local(c.tag)
                if not name:
                    continue
                if kind == "complexType":
                    self.cts.setdefault(name, c)
                elif kind == "simpleType":
                    self.sts.setdefault(name, c)
                elif kind == "element":
                    self.elements.setdefault(name, c)
                    self.global_order.append(c)

    # -- qname resolution
    @staticmethod
    def _resolve(el, qname: str | None) -> tuple[str | None, str]:
        if not qname:
            return None, ""
        if ":" in qname:
            prefix, local = qname.split(":", 1)
            return el.nsmap.get(prefix), local
        return el.nsmap.get(None), qname

    def _type_ref(self, el, qname: str, owner: str) -> tuple[str, str | None]:
        """Type and (record name or None) for a type QName."""
        ns, local = self._resolve(el, qname)
        if ns == XS:
            return _XS_TYPES.get(local, "string"), None
        if local in self.cts:
            name = self._named_ct(local)
            return name, name
        if local in self.sts:
            return self._named_st(local), None
        return ("any", None)  # imported or unknown type

    # -- simple types
    def _enum_values(self, st) -> list[str] | None:
        for r in _xs_children(st, "restriction"):
            vals = [e.get("value", "") for e in _xs_children(r, "enumeration")]
            if vals:
                return vals
        return None

    def _simple_base(self, st) -> str:
        for r in _xs_children(st, "restriction"):
            if r.get("base"):
                t, _ = self._type_ref(r, r.get("base"), "")
                return t
            for inner in _xs_children(r, "simpleType"):
                return self._simple_base(inner)
        return "string"

    def _named_st(self, name: str) -> str:
        if name in self.st_enums:
            return self.st_enums[name]
        st = self.sts[name]
        vals = self._enum_values(st)
        if vals is None:
            t = self._simple_base(st)
            self.st_enums[name] = t
            return t
        en = self.m.claim(ident(name), ident(name) + "Enum")
        self.st_enums[name] = en
        self.m.enums[en] = ContractEnum(en, vals)
        return en

    def _inline_st(self, st, elem_name: str, owner: str) -> str:
        vals = self._enum_values(st)
        if vals is None:
            return self._simple_base(st)
        en = self.m.claim(ident(elem_name), owner + ident(elem_name))
        self.m.enums[en] = ContractEnum(en, vals)
        return en

    # -- complex types
    def _named_ct(self, name: str) -> str:
        if name in self.ct_records:
            return self.ct_records[name]
        rec_name = self.m.claim(ident(name), ident(name) + "Type")
        self.ct_records[name] = rec_name
        ct = self.cts[name]
        rec = ContractRecord(rec_name, _doc(ct))
        self.m.records[rec_name] = rec
        rec.props = self._ct_props(ct, rec_name, set())
        self.m.finish_props(rec)
        return rec_name

    def _inline_ct(self, ct, elem_name: str, owner: str, doc: str | None, memo_key: str | None = None) -> str:
        rec_name = self.m.claim(ident(elem_name), owner + ident(elem_name))
        if memo_key is not None:  # registered before the walk so a recursive ref finds it
            self.el_types[memo_key] = (rec_name, rec_name)
        rec = ContractRecord(rec_name, doc or _doc(ct))
        self.m.records[rec_name] = rec
        rec.props = self._ct_props(ct, rec_name, set())
        self.m.finish_props(rec)
        return rec_name

    def _ct_props(self, ct, owner: str, seen_bases: set[str]) -> list[ContractProp]:
        props: list[ContractProp] = []
        for c in _xs_children(ct):
            kind = _local(c.tag)
            if kind in ("sequence", "all", "choice"):
                props += self._particle(c, owner, kind == "choice", False)
            elif kind == "attribute":
                props += self._attribute(c, owner)
            elif kind in ("complexContent", "simpleContent"):
                for d in _xs_children(c, "extension", "restriction"):
                    base = d.get("base")
                    if _local(d.tag) == "extension" and base:
                        ns, local = self._resolve(d, base)
                        if ns != XS and local in self.cts and local not in seen_bases:
                            props += self._ct_props(self.cts[local], owner, seen_bases | {local})
                        elif kind == "simpleContent":
                            t, _ = self._type_ref(d, base, owner)
                            props.append(ContractProp("Value", "value", t, True, False, "", raw_name="value"))
                    elif kind == "simpleContent" and base:
                        t, _ = self._type_ref(d, base, owner)
                        props.append(ContractProp("Value", "value", t, True, False, "", raw_name="value"))
                    props += self._ct_props(d, owner, seen_bases)
        return props

    def _particle(self, comp, owner: str, optional: bool, repeated: bool) -> list[ContractProp]:
        optional = optional or _occurs(comp, "minOccurs", 1) == 0
        repeated = repeated or _occurs(comp, "maxOccurs", 1) > 1
        props: list[ContractProp] = []
        for c in _xs_children(comp):
            kind = _local(c.tag)
            if kind == "element":
                props.append(self._element(c, owner, optional, repeated))
            elif kind in ("sequence", "all"):
                props += self._particle(c, owner, optional, repeated)
            elif kind == "choice":
                props += self._particle(c, owner, True, repeated)
        return props

    def _element(self, el, owner: str, optional: bool, repeated: bool) -> ContractProp:
        min_o = _occurs(el, "minOccurs", 1)
        max_o = _occurs(el, "maxOccurs", 1)
        required = not optional and min_o >= 1 and el.get("nillable") != "true"
        repeated = repeated or max_o > 1
        target = el
        name = el.get("name") or ""
        if el.get("ref"):
            _, local = self._resolve(el, el.get("ref"))
            name = local
            target = self.elements.get(local, el)
        t, child = self._element_type(target, name, owner)
        return ContractProp(ident(name), _camel(name), t, required, repeated, name, child, raw_name=name)

    def _element_type(self, el, name: str, owner: str) -> tuple[str, str | None]:
        parent = el.getparent()
        is_global = parent is not None and _local(parent.tag) == "schema" and bool(el.get("name"))
        key = el.get("name") if is_global else None
        if key is not None and key in self.el_types:  # a global element has one type, however referenced
            return self.el_types[key]
        if el.get("type"):
            out = self._type_ref(el, el.get("type"), owner)
        elif _xs_children(el, "complexType"):
            rec = self._inline_ct(_xs_children(el, "complexType")[0], name, owner, _doc(el), key)
            out = (rec, rec)
        elif _xs_children(el, "simpleType"):
            out = (self._inline_st(_xs_children(el, "simpleType")[0], name, owner), None)
        else:
            out = ("any", None)
        if key is not None:
            self.el_types[key] = out
        return out

    def _attribute(self, at, owner: str) -> list[ContractProp]:
        name = at.get("name")
        if not name and at.get("ref"):
            _, name = self._resolve(at, at.get("ref"))
        if not name:
            return []
        if at.get("type"):
            t, _ = self._type_ref(at, at.get("type"), owner)
        else:
            sts = _xs_children(at, "simpleType")
            t = self._inline_st(sts[0], name, owner) if sts else "string"
        return [ContractProp(ident(name), _camel(name), t, at.get("use") == "required", False, f"@{name}",
                             raw_name=name)]

    def build(self) -> _Draft:
        for el in self.global_order:
            name = el.get("name")
            _, rec = self._element_type(el, name, "")
            if rec:
                self.m.root_paths.append((rec, f"/{name}"))
        for name in self.cts:
            self._named_ct(name)
        for name in self.sts:
            self._named_st(name)
        return self.m


_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, remove_comments=True, huge_tree=False)


def _schemas(contract: Contract) -> list[Any]:
    root = etree.fromstring(contract.text.encode("utf-8"), _PARSER)
    if root.tag == f"{{{XS}}}schema":
        return [root]
    return [s for s in root.iter(f"{{{XS}}}schema")]  # WSDL types


def stem(contract: Contract) -> str:
    """The contract's file stem: ``Schemas/funding.Request.xsd`` -> ``Request``."""
    return contract.id.rsplit("/", 1)[-1].rsplit(".", 1)[0].rsplit(".", 1)[-1]


def suffix(contract: Contract) -> str:
    """Disambiguating suffix for a record name another contract of the namespace already emits."""
    if contract.kind in ("json", "wsdl"):
        return contract.kind.capitalize()
    return ident(stem(contract))


def xsd_model(contract: Contract, reserved: frozenset[str] = frozenset()) -> ContractModel:
    """The model of an XSD or of the schemas inside a WSDL."""
    return _XsdBuilder(_schemas(contract), _Draft(reserved, suffix(contract))).build().finish()


# ----------------------------------------------------------------------- JSON
class _JsonBuilder:
    def __init__(self, doc: dict[str, Any], fallback_name: str, draft: _Draft) -> None:
        self.m = draft
        self.doc = doc
        self.defs: dict[str, Any] = {}
        for key in ("definitions", "$defs"):
            if isinstance(doc.get(key), dict):
                for k, v in doc[key].items():
                    self.defs.setdefault(k, v)
        self.def_records: dict[str, str] = {}
        self.fallback = fallback_name

    def _ref_name(self, ref: str) -> str:
        return ref.rsplit("/", 1)[-1]

    def _refs_in(self, schema: Any) -> set[str]:
        out: set[str] = set()
        if isinstance(schema, dict):
            if isinstance(schema.get("$ref"), str):
                out.add(self._ref_name(schema["$ref"]))
            for v in schema.values():
                out |= self._refs_in(v)
        elif isinstance(schema, list):
            for v in schema:
                out |= self._refs_in(v)
        return out

    def _def_record(self, name: str) -> str | None:
        if name in self.def_records:
            return self.def_records[name]
        schema = self.defs.get(name)
        if not isinstance(schema, dict) or not self._is_object(schema):
            return None
        rec_name = self.m.claim(ident(name), ident(name) + "Record")
        self.def_records[name] = rec_name
        self._fill(rec_name, schema)
        return rec_name

    @staticmethod
    def _is_object(schema: dict[str, Any]) -> bool:
        return schema.get("type") == "object" or "properties" in schema or "allOf" in schema

    def _fill(self, rec_name: str, schema: dict[str, Any]) -> None:
        rec = ContractRecord(rec_name,
                             schema.get("description") if isinstance(schema.get("description"), str) else None)
        self.m.records[rec_name] = rec
        rec.props = self._props(schema, rec_name)
        self.m.finish_props(rec)

    def _props(self, schema: dict[str, Any], owner: str) -> list[ContractProp]:
        props: list[ContractProp] = []
        for part in schema.get("allOf") or []:
            if isinstance(part, dict) and isinstance(part.get("$ref"), str):
                base = self.defs.get(self._ref_name(part["$ref"]))
                if isinstance(base, dict):
                    props += self._props(base, owner)
            elif isinstance(part, dict):
                props += self._props(part, owner)
        required = set(schema.get("required") or [])
        for name, ps in (schema.get("properties") or {}).items():
            t, child, repeated = self._type(ps if isinstance(ps, dict) else {}, name, owner)
            props.append(ContractProp(ident(name), name, t, name in required, repeated, name, child, raw_name=name))
        return props

    def _type(self, ps: dict[str, Any], name: str, owner: str) -> tuple[str, str | None, bool]:
        if isinstance(ps.get("$ref"), str):
            ref = self._ref_name(ps["$ref"])
            rec = self._def_record(ref)
            if rec:
                return rec, rec, False
            target = self.defs.get(ref)
            return (self._type(target, name, owner)[0] if isinstance(target, dict) else "any"), None, False
        t = ps.get("type")
        if isinstance(t, list):
            t = next((x for x in t if x != "null"), None)
        if t == "array":
            items = ps.get("items") if isinstance(ps.get("items"), dict) else {}
            item, child, _ = self._type(items, name, owner)
            return item, child, True
        if ps.get("enum") and t in (None, "string"):
            en = self.m.claim(ident(name), owner + ident(name))
            self.m.enums[en] = ContractEnum(en, [str(v) for v in ps["enum"]])
            return en, None, False
        if t == "string":
            fmt = ps.get("format")
            return {"date": "date", "date-time": "datetime", "byte": "bytes",
                    "time": "time"}.get(fmt, "string"), None, False
        if t == "integer":
            return ("long" if ps.get("format") == "int64" else "int"), None, False
        if t == "number":
            return {"double": "double", "float": "float"}.get(ps.get("format"), "decimal"), None, False
        if t == "boolean":
            return "bool", None, False
        if self._is_object(ps) and ps.get("properties"):
            rec = self.m.claim(ident(name), owner + ident(name))
            self._fill(rec, ps)
            return rec, rec, False
        return "any", None, False

    def build(self) -> _Draft:
        referenced: set[str] = set()
        for name, schema in self.defs.items():
            referenced |= {r for r in self._refs_in(schema) if r != name}
        if isinstance(self.doc.get("properties"), dict):
            title = self.doc.get("title") if isinstance(self.doc.get("title"), str) else self.fallback
            rec = self.m.claim(ident(title), ident(title) + "Record")
            self._fill(rec, self.doc)
            self.m.root_paths.append((rec, f"/{title}"))
        for name in self.defs:
            if name not in referenced:
                rec = self._def_record(name)
                if rec:
                    self.m.root_paths.append((rec, f"/{name}"))
        for name in self.defs:
            self._def_record(name)
        return self.m


def json_model(contract: Contract, reserved: frozenset[str] = frozenset()) -> ContractModel:
    """The model of a JSON Schema or Swagger 2.0 document (``definitions``/``$defs``)."""
    doc = json.loads(contract.text) if contract.text.strip() else {}
    if not isinstance(doc, dict):
        doc = {}
    return _JsonBuilder(doc, stem(contract), _Draft(reserved, suffix(contract))).build().finish()


# ----------------------------------------------------------------- dispatcher
def contract_model(contract: Contract, reserved: frozenset[str] = frozenset()) -> ContractModel:
    """The neutral model of ``contract`` (json to ``json_model``, xsd and wsdl to ``xsd_model``).

    ``reserved`` are record names other contracts already emit into the same namespace; a clash is
    renamed with a suffix (see ``plan_contracts``). Raises on an unparsable schema.
    """
    return json_model(contract, reserved) if contract.kind == "json" else xsd_model(contract, reserved)


def record_names(contract: Contract, reserved: frozenset[str] = frozenset()) -> list[str]:
    """Names of the records then enums of ``contract``, in emission order ([] when unparsable)."""
    try:
        return contract_model(contract, reserved).names()
    except (etree.XMLSyntaxError, ValueError):
        return []


# ------------------------------------------------------- per-application plan
def contract_module(contract: Contract) -> str:
    """The module (project folder) a contract belongs to: the first segment of its id."""
    return contract.id.split("/", 1)[0] if "/" in contract.id else ""


@dataclass
class ContractPlan:
    contract_id: str
    module: str
    namespace: str
    file: str
    record_names: list[str]
    reserved: frozenset[str]  # pass to the renderer so the rendered names match ``record_names``
    collisions: dict[str, str] = field(default_factory=dict)  # natural name -> emitted name
    collides_with: dict[str, str] = field(default_factory=dict)  # natural name -> contract id owning it


_KIND_RANK = {"xsd": 0, "wsdl": 1, "json": 2}


def _with_suffix(file: str, extra: str) -> str:
    base, dot, ext = file.rpartition(".")
    return f"{base}{extra}{dot}{ext}" if dot else f"{file}{extra}"


def plan_contracts(app_name: str, contracts: list[Contract], naming: StackNaming) -> dict[str, ContractPlan]:
    """Namespace, file and record names for every contract of one application.

    ``naming.contract_target(app_name, module, stem)`` gives each contract's namespace and file.
    Within a namespace contracts are taken XSD first, then WSDL, then JSON, each by id; a record
    name an earlier contract already emits is renamed with a suffix (``Json``, ``Wsdl``, or the XSD
    stem). Two contracts never share a file path, compared case-insensitively: a checkout on a
    case-insensitive file system (macOS, Windows) would otherwise hold one file for both.
    """
    plans: dict[str, ContractPlan] = {}
    owners: dict[str, dict[str, str]] = {}  # namespace -> record name -> contract id
    files: set[str] = set()  # lower-cased
    for c in sorted(contracts, key=lambda c: (contract_module(c), _KIND_RANK.get(c.kind, 9), c.id)):
        ns, file = naming.contract_target(app_name, contract_module(c), stem(c))
        owned = owners.setdefault(ns, {})
        reserved = frozenset(owned)
        natural = record_names(c)
        names = record_names(c, reserved)
        collisions = {n: e for n, e in zip(natural, names, strict=False) if n in reserved and n != e}
        if file.lower() in files:
            natural_file, n = file, 2
            file = _with_suffix(natural_file, suffix(c))
            while file.lower() in files:
                file, n = _with_suffix(natural_file, f"{suffix(c)}{n}"), n + 1
        files.add(file.lower())
        plans[c.id] = ContractPlan(
            contract_id=c.id, module=contract_module(c), namespace=ns, file=file, record_names=names,
            reserved=reserved, collisions=collisions,
            collides_with={n: owned[n] for n in collisions})
        for n in names:
            owned.setdefault(n, c.id)
    return plans
