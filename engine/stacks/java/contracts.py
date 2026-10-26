"""The Java renderer of the neutral contract model (``engine.stacks.contracts``).

One file per contract, ``core/contracts/<module>/<Stem>Contract.java`` (``JavaNaming.contract_target``):
a final holder class with the contract's records and enums nested in it, since Java allows one public
top-level type per file. Records carry Jackson ``@JsonProperty`` with the wire name and ``@TibcoPath``
with the lineage path; a required scalar is a primitive, an optional one boxed (nullable), a repeated
one ``List<T>``; ``toString`` masks sensitive components (``Masking``).
"""
from __future__ import annotations

import re

from engine.stacks.contracts import ContractModel, ContractProp
from engine.stacks.java.naming import JAVA_KEYWORDS, lower_camel
from engine.stacks.java.text import doc, java_string

# Record component names Java forbids (JLS 8.10.1): they would clash with Object's methods.
_FORBIDDEN_COMPONENTS = frozenset({"clone", "finalize", "getClass", "hashCode", "notify", "notifyAll",
                                   "toString", "wait"})
_PRIMITIVE = {"int": "int", "long": "long", "double": "double", "float": "float", "bool": "boolean"}
_BOXED = {"int": "Integer", "long": "Long", "double": "Double", "float": "Float", "bool": "Boolean"}
_OBJECT = {"string": "String", "decimal": "BigDecimal", "ulong": "BigInteger", "date": "LocalDate",
           "datetime": "OffsetDateTime", "time": "LocalTime", "bytes": "byte[]", "any": "JsonNode"}
_IMPORTS = {"BigDecimal": "java.math.BigDecimal", "BigInteger": "java.math.BigInteger",
            "LocalDate": "java.time.LocalDate", "OffsetDateTime": "java.time.OffsetDateTime",
            "LocalTime": "java.time.LocalTime", "JsonNode": "com.fasterxml.jackson.databind.JsonNode"}


def component_name(prop: ContractProp, used: set[str]) -> str:
    name = lower_camel(prop.name) or "value"
    if name in JAVA_KEYWORDS or name in _FORBIDDEN_COMPONENTS:
        name = f"{name}_"
    base, n = name, 2
    while name in used:
        name, n = f"{base}{n}", n + 1
    used.add(name)
    return name


def java_type(prop: ContractProp) -> str:
    t = prop.type_ref
    if prop.repeated:
        inner = _BOXED.get(t) or _OBJECT.get(t) or t
        return f"List<{'byte[]' if inner == 'byte[]' else inner}>"
    if t in _PRIMITIVE:
        return _PRIMITIVE[t] if prop.required else _BOXED[t]
    return _OBJECT.get(t, t)


def enum_constants(values: list[str]) -> list[tuple[str, str]]:
    """(Java constant, wire value) for an enum: ``ON-HOLD`` -> ``ON_HOLD``, ``1`` -> ``V_1``."""
    out: list[tuple[str, str]] = []
    used: set[str] = set()
    for v in values:
        name = re.sub(r"[^A-Za-z0-9]+", "_", v).strip("_").upper() or "EMPTY"
        if name[0].isdigit():
            name = f"V_{name}"
        base, n = name, 2
        while name in used:
            name, n = f"{base}_{n}", n + 1
        used.add(name)
        out.append((name, v))
    return out


def _renamed(java: str, rename: dict[str, str]) -> str:
    for old, new in rename.items():
        java = re.sub(rf"\b{re.escape(old)}\b", new, java)
    return java


def render(model: ContractModel, package: str, holder: str, source: str, app_package: str) -> str:
    """The holder class of one contract's records and enums. A record or enum named like the holder
    (Java forbids a nested type with its enclosing type's name) is written ``<Name>Type``."""
    rename = {holder: f"{holder}Type"} if holder in model.records or holder in model.enums else {}
    imports = {"com.fasterxml.jackson.annotation.JsonProperty", f"{app_package}.core.contracts.TibcoPath"}
    body: list[str] = []
    for rec in model.records.values():
        imports.add(f"{app_package}.core.contracts.Masking")
        used: set[str] = set()
        comps: list[str] = []
        for p in rec.props:
            jt = _renamed(java_type(p), rename)
            if p.repeated:
                imports.add("java.util.List")
            for simple, full in _IMPORTS.items():
                if re.search(rf"\b{simple}\b", jt):
                    imports.add(full)
            path = f" @TibcoPath({java_string(p.tibco_path)})" if p.tibco_path else ""
            comps.append(f"            @JsonProperty({java_string(p.json_name)}){path} {jt} {component_name(p, used)}")
        body.append("")
        body.append(f"    /** {doc(rec.doc or f'TIBCO element {rec.tibco_path or rec.name}.')} */")
        if rec.tibco_path:
            body.append(f"    @TibcoPath({java_string(rec.tibco_path)})")
        name = rename.get(rec.name, rec.name)
        if comps:
            body.append(f"    public record {name}(")
            body.append(",\n".join(comps) + ") {")
        else:
            body.append(f"    public record {name}() {{")
        body += ["        @Override", "        public String toString() {",
                 "            return Masking.toMaskedString(this);", "        }", "    }"]
    for enum in model.enums.values():
        body.append("")
        body.append(f"    /** Values of {doc(enum.name)}, by their wire value. */")
        body.append(f"    public enum {rename.get(enum.name, enum.name)} {{")
        consts = enum_constants(enum.values)
        for i, (name, wire) in enumerate(consts):
            body.append(f"        @JsonProperty({java_string(wire)})")
            body.append(f"        {name}{',' if i < len(consts) - 1 else ''}")
        body.append("    }")
    lines = [f"package {package};", ""]
    lines += [f"import {i};" for i in sorted(imports)]
    lines += ["", f"/** Contract records of {doc(source)}, generated from the TIBCO schema. */",
              f"public final class {holder} {{", f"    private {holder}() {{}}"]
    lines += body
    lines.append("}")
    return "\n".join(lines) + "\n"
