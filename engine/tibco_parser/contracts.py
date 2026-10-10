"""Parser for schema and service-descriptor contracts (XSD, WSDL, JSON)."""
import json
from pathlib import Path

from engine.ir.models import Contract
from engine.tibco_parser.xmlutil import local, normalize_bytes, read_xml

_MARKER_DIRS = {"Schemas", "Service Descriptors"}


def _contract_id(path: Path) -> str:
    for anc in path.parents:
        if anc.name in _MARKER_DIRS:
            return f"{anc.parent.name}/{path.relative_to(anc.parent).as_posix()}"
    return f"{path.parent.name}/{path.name}"


def _xsd_roots(root) -> list[str]:
    return [c.get("name") for c in root if local(c.tag) == "element" and c.get("name")]


def _wsdl_roots(root) -> list[str]:
    roots: list[str] = []
    for pt in root:
        if local(pt.tag) != "portType":
            continue
        pt_name = pt.get("name") or ""
        for op in pt:
            if local(op.tag) == "operation" and op.get("name"):
                roots.append(f"{pt_name}.{op.get('name')}")
    return roots


def _json_roots(doc) -> list[str]:
    roots: list[str] = []
    if not isinstance(doc, dict):
        return roots
    if isinstance(doc.get("title"), str) and doc["title"]:
        roots.append(doc["title"])
    # `$defs` is JSON Schema 2019+; `definitions` is draft-07 and Swagger 2.0
    # (the shape the BW6 Studio service descriptors use).
    for key in ("$defs", "definitions"):
        defs = doc.get(key)
        if isinstance(defs, dict):
            roots.extend(n for n in defs if n not in roots)
    return roots


def parse_contract(path: Path) -> Contract:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in {".xsd", ".wsdl", ".json"}:
        raise ValueError(f"Unsupported contract file type: {path.name}")
    raw = normalize_bytes(path.read_bytes())
    text = raw.decode("utf-8-sig", errors="replace")
    if suffix == ".json":
        kind, roots = "json", _json_roots(json.loads(text))
    elif suffix == ".xsd":
        kind, roots = "xsd", _xsd_roots(read_xml(path))
    else:
        kind, roots = "wsdl", _wsdl_roots(read_xml(path))
    return Contract(id=_contract_id(path), kind=kind, path=str(path), root_elements=roots, text=text)
