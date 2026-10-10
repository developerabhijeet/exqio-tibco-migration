"""Parser for TIBCO substitution-variable files.

BW6 `META-INF/default.substvar` and BW5 `defaultVars/defaultVars.substvar`
share the same `repository/globalVariables/globalVariable` shape. Names are
kept verbatim (module-qualified `//Module//NAME` forms included).
"""
from pathlib import Path

from engine.ir.models import SubstVar
from engine.tibco_parser.xmlutil import local, read_xml


def _child_text(el, name: str) -> str | None:
    for child in el:
        if local(child.tag) == name:
            return child.text or ""
    return None


def parse_substvar(path: Path) -> list[SubstVar]:
    root = read_xml(Path(path))
    out: list[SubstVar] = []
    for el in root.iter():
        if local(el.tag) != "globalVariable":
            continue
        name = _child_text(el, "name")
        if not name or not name.strip():
            continue
        value = _child_text(el, "value") or ""
        vtype = (_child_text(el, "type") or "String").strip() or "String"
        out.append(SubstVar(name=name.strip(), value=value, type=vtype, source_path=str(path)))
    return out
