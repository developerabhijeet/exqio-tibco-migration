"""The Python renderer of the neutral contract model (``engine.stacks.contracts``).

One module per contract, ``core/contracts/<module>/<stem>.py`` (``PythonNaming.contract_target``):
pydantic v2 models with ``model_config = ConfigDict(populate_by_name=True)``, each field carrying
its wire name as ``alias`` and the lineage path as ``json_schema_extra={"tibco_path": ...}``; a
required field has no default, an optional one defaults to ``None``, a repeated one is a
``list[T]``; enums are ``str`` enums of the wire values. ``repr`` masks sensitive fields
(``masking.masked_repr_args``). Forward references resolve through ``model_rebuild`` at the end.
"""
from __future__ import annotations

from engine.stacks.contracts import ContractModel, ContractProp
from engine.stacks.java.contracts import enum_constants
from engine.stacks.python.naming import PYTHON_KEYWORDS, snake
from engine.stacks.python.text import doc, py_string

# Names a pydantic field may not take: BaseModel's own attributes, and the type names the
# annotations use (a field named like one would shadow it inside the class body).
_FORBIDDEN_FIELDS = frozenset({
    "copy", "dict", "json", "parse_obj", "parse_raw", "parse_file", "schema", "schema_json", "construct",
    "validate", "fields", "from_orm", "update_forward_refs", "str", "int", "float", "bool", "bytes", "list",
    "any", "decimal", "dt", "field", "masking", "enum", "base_model", "config_dict",
})
_SCALAR = {"string": "str", "int": "int", "long": "int", "ulong": "int", "decimal": "Decimal", "double": "float",
           "float": "float", "bool": "bool", "date": "_dt.date", "datetime": "_dt.datetime", "time": "_dt.time",
           "bytes": "bytes", "any": "Any"}


def field_name(prop: ContractProp, used: set[str]) -> str:
    name = snake(prop.name) or "value"
    if name[0].isdigit():
        name = f"f_{name}"
    if name in PYTHON_KEYWORDS or name in _FORBIDDEN_FIELDS or name.startswith("model_"):
        name = f"{name}_"
    base, n = name, 2
    while name in used:
        name, n = f"{base}{n}", n + 1
    used.add(name)
    return name


def python_type(prop: ContractProp) -> str:
    inner = _SCALAR.get(prop.type_ref, prop.type_ref)
    if prop.repeated:
        return f"list[{inner}]"
    return inner if prop.required else f"{inner} | None"


def render(model: ContractModel, source: str, app_package: str) -> str:
    """The module of one contract's records and enums."""
    body: list[str] = []
    names: list[str] = []
    uses: set[str] = set()
    for enum in model.enums.values():
        body += ["", "", f"class {enum.name}(str, Enum):", f'    """Values of {doc(enum.name)}, by their wire value."""', ""]
        for name, wire in enum_constants(enum.values):
            body.append(f"    {name} = {py_string(wire)}")
        uses.add("enum")
    for rec in model.records.values():
        names.append(rec.name)
        used: set[str] = set()
        body += ["", "", f"class {rec.name}(BaseModel):",
                 f'    """{doc(rec.doc or f"TIBCO element {rec.tibco_path or rec.name}.")}"""', "",
                 ("    model_config = ConfigDict(populate_by_name=True, json_schema_extra="
                 f"{{\"tibco_path\": {py_string(rec.tibco_path or '/' + rec.name)}}})"), ""]
        for p in rec.props:
            pt = python_type(p)
            uses |= {k for k in ("Decimal", "_dt", "Any") if k in pt}
            args = [f"alias={py_string(p.json_name)}"]
            if p.tibco_path:
                args.append(f"json_schema_extra={{\"tibco_path\": {py_string(p.tibco_path)}}}")
            if p.repeated and not p.required:
                args.insert(0, "default_factory=list")
            elif not p.required:
                args.insert(0, "default=None")
            body.append(f"    {field_name(p, used)}: {pt} = Field({', '.join(args)})")
        body += ["", "    __repr_args__ = masking.masked_repr_args"]
    lines = [f'"""Contract models of {doc(source)}, generated from the TIBCO schema."""', "",
             "from __future__ import annotations", ""]
    std = []
    if "_dt" in uses:
        std.append("import datetime as _dt")
    if "Decimal" in uses:
        std.append("from decimal import Decimal")
    if "enum" in uses:
        std.append("from enum import Enum")
    if "Any" in uses:
        std.append("from typing import Any")
    if std:
        lines += std + [""]
    lines += ["from pydantic import BaseModel, ConfigDict, Field", "", f"from {app_package}.core import masking"]
    lines += body
    if names:
        lines += ["", ""] + [f"{n}.model_rebuild()" for n in names]
    return "\n".join(lines) + "\n"
