"""The Go renderer of the neutral contract model (``engine.stacks.contracts``).

One file per contract, ``internal/core/contracts/<module>/<stem>.go`` (``GoNaming.contract_target``),
package ``<module>``: a struct per record with ``json:"..."`` tags and a ``// TibcoPath:`` comment
above each field, a named string type with constants per enum. A required scalar is a value, an
optional one a pointer (null and absent stay apart), a repeated one a slice; decimals are
``json.Number`` so no digit is lost. The structs validate request bodies at the HTTP boundary
(ruling X19): the process itself runs on the JSON tree.
"""
from __future__ import annotations

import re

from engine.stacks.contracts import ContractModel, ContractProp
from engine.stacks.go.naming import GO_KEYWORDS
from engine.stacks.go.text import doc, go_string, import_block

_SCALAR = {"string": "string", "int": "int", "long": "int64", "double": "float64", "float": "float32",
           "bool": "bool", "decimal": "json.Number", "ulong": "json.Number", "date": "string",
           "datetime": "time.Time", "time": "string", "bytes": "[]byte", "any": "json.RawMessage"}
_NO_POINTER = frozenset({"[]byte", "json.RawMessage"})


def field_name(prop: ContractProp, used: set[str]) -> str:
    name = prop.name or "Value"
    name = name[0].upper() + name[1:]
    if not name[0].isalpha():
        name = f"V{name}"
    base, n = name, 2
    while name in used:
        name, n = f"{base}{n}", n + 1
    used.add(name)
    return name


def go_type(prop: ContractProp, rename: dict[str, str]) -> str:
    t = rename.get(prop.type_ref, prop.type_ref)
    inner = _SCALAR.get(prop.type_ref, t)
    if prop.repeated:
        return f"[]{inner}"
    if prop.required or inner in _NO_POINTER:
        return inner
    return f"*{inner}"


def enum_constants(enum: str, values: list[str]) -> list[tuple[str, str]]:
    """(Go constant, wire value) for an enum: ``ON-HOLD`` of ``Status`` -> ``StatusOnHold``."""
    out: list[tuple[str, str]] = []
    used: set[str] = set()
    for v in values:
        words = re.findall(r"[A-Za-z0-9]+", v)
        name = enum + ("".join(w[:1].upper() + w[1:].lower() for w in words) or "Empty")
        base, n = name, 2
        while name in used:
            name, n = f"{base}{n}", n + 1
        used.add(name)
        out.append((name, v))
    return out


def render(model: ContractModel, import_path: str, source: str) -> str:
    """The Go file of one contract's records and enums (``import_path`` names the package)."""
    package = import_path.rsplit("/", 1)[-1]
    rename = {n: f"{n}_" for n in model.names() if n in GO_KEYWORDS}
    imports: set[str] = set()
    body: list[str] = []
    for rec in model.records.values():
        used: set[str] = set()
        name = rename.get(rec.name, rec.name)
        body += ["", f"// {name} is {doc(rec.doc or f'TIBCO element {rec.tibco_path or rec.name}.')}"]
        if rec.tibco_path:
            body.append(f"// TibcoPath: {doc(rec.tibco_path)}")
        if not rec.props:
            body.append(f"type {name} struct{{}}")
            continue
        body.append(f"type {name} struct {{")
        for p in rec.props:
            gt = go_type(p, rename)
            if "json." in gt:
                imports.add("encoding/json")
            if "time." in gt:
                imports.add("time")
            tag = f'`json:"{p.json_name}{"" if p.required else ",omitempty"}"`'
            if '"' in p.json_name or "`" in p.json_name or "," in p.json_name:
                tag = f"`json:{go_string(p.json_name)}`"
            if p.tibco_path:
                body.append(f"\t// TibcoPath: {doc(p.tibco_path)}")
            body.append(f"\t{field_name(p, used)} {gt} {tag}")
        body.append("}")
    for enum in model.enums.values():
        name = rename.get(enum.name, enum.name)
        consts = enum_constants(name, enum.values)
        width = max((len(c) for c, _ in consts), default=0)
        body += ["", f"// {name} are the values of {doc(enum.name)}, by their wire value.", f"type {name} string"]
        if consts:
            body += ["", f"// The values of {name}.", "const ("]
            body += [f"\t{c.ljust(width)} {name} = {go_string(v)}" for c, v in consts]
            body.append(")")
    lines = [f"// Package {package} holds the contract records of {doc(source)}, generated from the TIBCO schema.",
             f"package {package}"]
    if imports:
        lines += ["", *import_block(imports, "")]
    return "\n".join(lines + body) + "\n"
