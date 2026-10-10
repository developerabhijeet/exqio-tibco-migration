#!/usr/bin/env python3
"""Structural fidelity check for TIBCO BusinessWorks 6 process files (.bwp).

The rules below were recorded from TIBCO's public BW6 samples (the TN2018 credit
application: ``LookupDatabase.bwp``, ``MainProcess.bwp``, ``EquifaxScore.bwp`` and
the tibcobwblog BW6 projects). A ``.bwp`` written by BusinessWorks Studio 6.x:

* has a root ``bpws:process`` (WS-BPEL 2.0 executable namespace) whose ``name`` is
  the dotted package path under ``Processes/`` plus the file stem, and declares the
  ``tibex`` extension namespace;
* carries ``tibex:ProcessInfo`` and ``tibex:ProcessInterface``;
* imports the schemas it uses with ``bpws:import`` (module XSDs under ``Schemas/``,
  JSON schemas converted through ``.WebResources/schemaConversion.json``, or schemas
  inlined in ``tibex:Types``);
* declares ``bpws:partnerLinks`` for every partner link an activity uses;
* declares every variable an activity reads or writes in ``bpws:variables``;
* lists ``bpws:extensions`` including the ``tibex`` namespace;
* wraps its body in ``bpws:scope`` > ``bpws:flow`` > ``bpws:links``;
* starts with exactly one instance-creating starter (``tibex:receiveEvent``,
  ``bpws:pick`` or ``bpws:receive`` with ``createInstance="yes"``);
* gives every activity a ``tibex:xpdlId``: ``tibex:receiveEvent``,
  ``tibex:activityExtension``, ``tibex:extActivity``, ``bpws:invoke``,
  ``bpws:reply``, ``bpws:throw``, ``bpws:assign``, ``bpws:receive``, ``bpws:pick``,
  ``bpws:onMessage``, ``bpws:empty``, ``bpws:exit``, fault handlers and groups
  (``bpws:scope`` with ``tibex:group``);
* types palette activities through ``tibex:config/bwext:BWActivity/@activityTypeID``,
  event sources through ``tibex:eventSource``, and call-process activities through
  ``tibex:extActivity/@type`` plus ``tibex:CallProcess/@subProcessName``;
* carries mappings as XSLT stylesheets in ``tibex:inputBinding`` /
  ``tibex:partBinding`` (``expression`` attribute or element text);
* expresses transitions as ``bpws:link`` with a ``tibex:linkType`` of ``SUCCESS``,
  ``SUCCESSWITHCONDITION`` (``bpws:transitionCondition`` holds an XPath),
  ``SUCCESSWITHNOCONDITION`` (``##otherwise##``) or ``ERROR``; every link has
  exactly one ``bpws:source`` and one ``bpws:target``;
* references shared resources through process properties (``bpws:variable`` with
  ``sca-bpel:property="yes"``), never by file path.

The profile is BusinessWorks 6.4/6.5 Studio output (the TN2018 sample is 6.5.0 V63).
Files saved by 6.2/6.3 Studio omit ``tibex:ProcessInterface`` and ``tibex:linkType``
and are reported as such.

``check(path)`` returns a list of messages; an empty list means the file passes.
"""
from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

from lxml import etree

BPWS = "http://docs.oasis-open.org/wsbpel/2.0/process/executable"
TIBEX = "http://www.tibco.com/bpel/2007/extensions"
BWEXT = "http://tns.tibco.com/bw/model/core/bwext"
SCA_BPEL = "http://docs.oasis-open.org/ns/opencsa/sca-bpel/200801"
XSL = "http://www.w3.org/1999/XSL/Transform"
XSD_NS = "http://www.w3.org/2001/XMLSchema"
WSDL_NS = "http://schemas.xmlsoap.org/wsdl/"

XSLT_LANG = "urn:oasis:names:tc:wsbpel:2.0:sublang:xslt1.0"
XPATH_LANG = "urn:oasis:names:tc:wsbpel:2.0:sublang:xpath2.0"
OTHERWISE = "##otherwise##"
LINK_TYPES = {"SUCCESS", "SUCCESSWITHCONDITION", "SUCCESSWITHNOCONDITION", "ERROR"}

ACTIVITY_TAGS = {
    f"{{{TIBEX}}}receiveEvent",
    f"{{{TIBEX}}}activityExtension",
    f"{{{TIBEX}}}extActivity",
    f"{{{BPWS}}}invoke",
    f"{{{BPWS}}}reply",
    f"{{{BPWS}}}throw",
    f"{{{BPWS}}}assign",
    f"{{{BPWS}}}receive",
    f"{{{BPWS}}}pick",
    f"{{{BPWS}}}onMessage",
    f"{{{BPWS}}}empty",
    f"{{{BPWS}}}exit",
    f"{{{BPWS}}}catch",
    f"{{{BPWS}}}catchAll",
}
STARTER_TAGS = {f"{{{TIBEX}}}receiveEvent", f"{{{BPWS}}}pick", f"{{{BPWS}}}receive"}
VARIABLE_ATTRS = ("inputVariable", "outputVariable", "variable", "faultVariable")
TIBEX_VARIABLE_ATTRS = ("faultDetailsVar", "faultNameVar")
QNAME_ATTRS = ("element", "messageType", "faultName", "portType", "partnerLinkType")
SHARED_RESOURCE_ATTRS = (
    "sharedResourceProperty",
    "httpClientResource",
    "httpConnection",
    "connectionReference",
)
KNOWN_EXTERNAL_NAMESPACES = {
    "http://www.w3.org/2001/XMLSchema",
    "http://www.tibco.com/pe/EngineTypes",
}


def _q(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


def _where(el: etree._Element) -> str:
    name = el.get("name")
    local = etree.QName(el).localname
    label = f"{local} '{name}'" if name else local
    return f"{label} (line {el.sourceline})"


def _module_root(path: Path) -> Path | None:
    parts = path.resolve().parts
    if "Processes" not in parts:
        return None
    idx = len(parts) - 1 - list(reversed(parts)).index("Processes")
    return Path(*parts[:idx])


def _expected_process_name(path: Path) -> str | None:
    root = _module_root(path)
    if root is None:
        return None
    rel = path.resolve().relative_to(root / "Processes")
    return ".".join([*rel.parts[:-1], rel.stem])


@lru_cache(maxsize=64)
def _module_namespaces(module_root: Path) -> frozenset[str]:
    """Namespaces a module makes available to its processes."""
    found: set[str] = set()
    for folder in ("Schemas", "Service Descriptors"):
        base = module_root / folder
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if f.suffix.lower() not in {".xsd", ".wsdl"}:
                continue
            try:
                tree = etree.parse(str(f))
            except etree.XMLSyntaxError:
                continue
            tns = tree.getroot().get("targetNamespace")
            if tns:
                found.add(tns)
    conv = module_root / ".WebResources" / "schemaConversion.json"
    if conv.is_file():
        try:
            records = json.loads(conv.read_text(encoding="utf-8")).get("records", [])
        except (ValueError, AttributeError):
            records = []
        for rec in records:
            ns = rec.get("xsdSchemaTargetNamespace")
            if ns:
                found.add(ns)
    return frozenset(found)


def _check_root(root: etree._Element, path: Path, out: list[str]) -> None:
    if root.tag != _q(BPWS, "process"):
        out.append(f"root: expected bpws:process, found {root.tag}")
        return
    if root.nsmap.get("tibex") != TIBEX:
        out.append("root: tibex namespace is not declared on bpws:process")
    name = root.get("name")
    if not name:
        out.append("root: bpws:process has no name")
    expected = _expected_process_name(path)
    if expected and name and name != expected:
        out.append(f"root: process name '{name}' does not match path ('{expected}')")
    for local in ("ProcessInfo", "ProcessInterface"):
        if root.find(_q(TIBEX, local)) is None:
            out.append(f"root: tibex:{local} missing")


def _check_imports(root: etree._Element, path: Path, out: list[str]) -> None:
    imports = root.findall(_q(BPWS, "import"))
    if not imports:
        out.append("imports: no bpws:import")
    inline: set[str] = set()
    types = root.find(_q(TIBEX, "Types"))
    if types is not None:
        for child in types:
            tns = child.get("targetNamespace")
            if tns:
                inline.add(tns)
    module = _module_root(path)
    available = set(_module_namespaces(module)) if module else set()
    for imp in imports:
        ns = imp.get("namespace")
        if imp.get("importType") is None or ns is None:
            out.append(f"imports: bpws:import without importType/namespace (line {imp.sourceline})")
            continue
        if module is None:
            continue
        if ns in available or ns in inline or ns in KNOWN_EXTERNAL_NAMESPACES:
            continue
        if ns == "" or ns.startswith("http://www.tibco.com/namespaces/tnt/plugins/"):
            continue
        out.append(f"imports: namespace '{ns}' is not provided by the module (Schemas, "
                   "Service Descriptors, schemaConversion) or tibex:Types")


def _check_sections(root: etree._Element, out: list[str]) -> etree._Element | None:
    if root.find(_q(BPWS, "variables")) is None:
        out.append("sections: bpws:variables missing")
    exts = root.find(_q(BPWS, "extensions"))
    if exts is None:
        out.append("sections: bpws:extensions missing")
    elif not any(e.get("namespace") == TIBEX for e in exts.findall(_q(BPWS, "extension"))):
        out.append("sections: bpws:extensions has no tibex extension")
    scope = root.find(_q(BPWS, "scope"))
    if scope is None:
        out.append("sections: top-level bpws:scope missing")
        return None
    flow = scope.find(_q(BPWS, "flow"))
    if flow is None:
        out.append("sections: top-level bpws:scope has no bpws:flow")
        return None
    if flow.find(_q(BPWS, "links")) is None:
        out.append("sections: top-level bpws:flow has no bpws:links")
    return flow


def _check_starter(root: etree._Element, out: list[str]) -> None:
    starters = [
        el for el in root.iter(*STARTER_TAGS) if el.get("createInstance") == "yes"
    ]
    if len(starters) != 1:
        out.append(f"starter: expected exactly one createInstance starter, found {len(starters)}")
    for el in root.iter(_q(TIBEX, "receiveEvent")):
        src = el.find(_q(TIBEX, "eventSource"))
        if src is None or len(src) == 0:
            out.append(f"starter: {_where(el)} has no tibex:eventSource")


def _check_activities(root: etree._Element, path: Path, out: list[str]) -> None:
    names: dict[str, int] = {}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        is_group = el.tag == _q(BPWS, "scope") and el.get(_q(TIBEX, "group")) is not None
        if el.tag not in ACTIVITY_TAGS and not is_group:
            continue
        if el.get(_q(TIBEX, "xpdlId")) is None:
            out.append(f"activity: {_where(el)} has no tibex:xpdlId")
        name = el.get("name")
        if name and el.tag not in {_q(BPWS, "catch"), _q(BPWS, "catchAll")}:
            if name in names:
                out.append(f"activity: duplicate activity name '{name}' (line {el.sourceline})")
            names[name] = el.sourceline
        if el.tag == _q(TIBEX, "activityExtension"):
            act = el.find(f"{_q(TIBEX, 'config')}/{_q(BWEXT, 'BWActivity')}")
            if act is None or not act.get("activityTypeID"):
                out.append(f"activity: {_where(el)} has no bwext:BWActivity/@activityTypeID")
        if el.tag == _q(TIBEX, "extActivity"):
            kind = el.get("type")
            if not kind:
                out.append(f"activity: {_where(el)} has no type")
            elif kind == "bw.generalactivities.callprocess":
                call = el.find(_q(TIBEX, "CallProcess"))
                if call is None or not call.get("subProcessName"):
                    out.append(f"activity: {_where(el)} has no tibex:CallProcess/@subProcessName")
                else:
                    _check_subprocess_exists(call.get("subProcessName"), path, el, out)
        if el.tag == _q(BPWS, "throw") and not el.get("faultName"):
            out.append(f"activity: {_where(el)} has no faultName")
        needs_partner = {_q(BPWS, "invoke"), _q(BPWS, "reply"), _q(BPWS, "onMessage")}
        if el.tag in needs_partner and not (el.get("partnerLink") and el.get("operation")):
            out.append(f"activity: {_where(el)} needs partnerLink and operation")


def _check_subprocess_exists(name: str, path: Path, el: etree._Element, out: list[str]) -> None:
    module = _module_root(path)
    if module is None:
        return
    parts = name.split(".")
    target = module / "Processes" / Path(*parts[:-1]) / f"{parts[-1]}.bwp"
    if not target.is_file():
        out.append(f"activity: {_where(el)} calls '{name}' but {target.name} is not in the module")


def _check_partner_links(root: etree._Element, out: list[str]) -> None:
    declared = {
        pl.get("name")
        for pl in root.iterfind(f"{_q(BPWS, 'partnerLinks')}/{_q(BPWS, 'partnerLink')}")
    }
    for el in root.iter():
        if not isinstance(el.tag, str) or el.tag == _q(BPWS, "partnerLink"):
            continue
        used = el.get("partnerLink")
        if used and used not in declared:
            out.append(f"partnerLinks: {_where(el)} uses undeclared partnerLink '{used}'")


def _check_variables(root: etree._Element, out: list[str]) -> None:
    declared = {v.get("name") for v in root.iter(_q(BPWS, "variable"))}
    declared |= {f.get("counterName") for f in root.iter(_q(BPWS, "forEach"))}
    for el in root.iter():
        if not isinstance(el.tag, str) or el.tag == _q(BPWS, "variable"):
            continue
        if not el.tag.startswith(f"{{{BPWS}}}") and not el.tag.startswith(f"{{{TIBEX}}}"):
            continue
        for attr in VARIABLE_ATTRS:
            val = el.get(attr)
            if val and val not in declared:
                out.append(f"variables: {_where(el)} @{attr} '{val}' is not declared")
        for attr in TIBEX_VARIABLE_ATTRS:
            val = el.get(_q(TIBEX, attr))
            if val and val not in declared:
                out.append(f"variables: {_where(el)} @tibex:{attr} '{val}' is not declared")


def _check_qnames(root: etree._Element, out: list[str]) -> None:
    for el in root.iter():
        if not isinstance(el.tag, str) or not el.tag.startswith(f"{{{BPWS}}}"):
            continue
        attrs = list(QNAME_ATTRS)
        if el.tag == _q(BPWS, "variable"):
            attrs.append("type")
        for attr in attrs:
            val = el.get(attr)
            if not val or ":" not in val:
                continue
            prefix = val.split(":", 1)[0]
            if prefix not in el.nsmap:
                out.append(f"qname: {_where(el)} @{attr} '{val}' uses undeclared prefix '{prefix}'")


def _check_shared_resources(root: etree._Element, out: list[str]) -> None:
    props = {
        v.get("name")
        for v in root.iter(_q(BPWS, "variable"))
        if v.get(_q(SCA_BPEL, "property")) == "yes"
    }
    for holder in (*root.iter(_q(TIBEX, "config")), *root.iter(_q(TIBEX, "eventSource"))):
        for el in holder.iter():
            if not isinstance(el.tag, str):
                continue
            for attr in SHARED_RESOURCE_ATTRS:
                val = el.get(attr)
                if val and val not in props:
                    out.append(f"resources: @{attr} '{val}' (line {el.sourceline}) is not a "
                               "declared process property")


def _check_links(root: etree._Element, out: list[str]) -> None:
    link_types: dict[str, str | None] = {}
    declared_in: dict[str, etree._Element] = {}
    for flow in root.iter(_q(BPWS, "flow")):
        for link in flow.iterfind(f"{_q(BPWS, 'links')}/{_q(BPWS, 'link')}"):
            name = link.get("name")
            if name in declared_in:
                out.append(f"links: link '{name}' declared twice")
            declared_in[name] = flow
            link_types[name] = link.get(_q(TIBEX, "linkType"))
            if link_types[name] not in LINK_TYPES:
                out.append(f"links: link '{name}' has tibex:linkType "
                           f"'{link_types[name]}' (expected one of {sorted(LINK_TYPES)})")
    sources: dict[str, int] = {n: 0 for n in declared_in}
    targets: dict[str, int] = {n: 0 for n in declared_in}
    for tag, counter in ((_q(BPWS, "source"), sources), (_q(BPWS, "target"), targets)):
        for ref in root.iter(tag):
            name = ref.get("linkName")
            if name not in declared_in:
                out.append(f"links: {etree.QName(ref).localname} references undeclared link "
                           f"'{name}' (line {ref.sourceline})")
                continue
            if declared_in[name] not in ref.iterancestors(_q(BPWS, "flow")):
                out.append(f"links: link '{name}' is used outside the flow that declares it")
            counter[name] += 1
            if tag == _q(BPWS, "source"):
                _check_condition(ref, name, link_types.get(name), out)
    for name in declared_in:
        if sources[name] != 1 or targets[name] != 1:
            out.append(f"links: link '{name}' has {sources[name]} source(s) and "
                       f"{targets[name]} target(s); expected 1 and 1")


def _check_condition(src: etree._Element, name: str, kind: str | None, out: list[str]) -> None:
    cond = src.find(_q(BPWS, "transitionCondition"))
    text = (cond.text or "").strip() if cond is not None else ""
    if cond is not None and cond.get("expressionLanguage") != XPATH_LANG:
        out.append(f"links: transitionCondition on '{name}' is not XPath 2.0")
    if kind == "SUCCESSWITHCONDITION":
        if not text or text == OTHERWISE:
            out.append(f"links: conditional link '{name}' has no transitionCondition")
    elif kind == "SUCCESSWITHNOCONDITION":
        if text != OTHERWISE:
            out.append(f"links: otherwise link '{name}' must carry '{OTHERWISE}'")
    elif cond is not None and text:
        out.append(f"links: unconditional link '{name}' carries a transitionCondition")


def _binding_text(el: etree._Element) -> str:
    expr = el.get("expression")
    if expr is not None:
        return expr
    return el.text or ""


def _check_bindings(root: etree._Element, out: list[str]) -> None:
    tags = (_q(TIBEX, "inputBinding"), _q(TIBEX, "partBinding"))
    for el in root.iter(*tags):
        where = f"{etree.QName(el).localname} (line {el.sourceline})"
        if el.get("expressionLanguage") != XSLT_LANG:
            out.append(f"bindings: {where} expressionLanguage is not XSLT")
        text = _binding_text(el).strip()
        if not text:
            out.append(f"bindings: {where} is empty")
            continue
        try:
            sheet = etree.fromstring(text.encode("utf-8"))
        except etree.XMLSyntaxError as exc:
            out.append(f"bindings: {where} is not well-formed XSLT ({exc})")
            continue
        if sheet.tag != _q(XSL, "stylesheet"):
            out.append(f"bindings: {where} root is {sheet.tag}, expected xsl:stylesheet")
        elif sheet.find(_q(XSL, "template")) is None:
            out.append(f"bindings: {where} has no xsl:template")


def check(path: Path) -> list[str]:
    """Return structural problems for one .bwp file; empty list means it passes."""
    path = Path(path)
    try:
        tree = etree.parse(str(path))
    except (OSError, etree.XMLSyntaxError) as exc:
        return [f"xml: cannot parse {path.name}: {exc}"]
    root = tree.getroot()
    out: list[str] = []
    _check_root(root, path, out)
    if root.tag != _q(BPWS, "process"):
        return out
    _check_imports(root, path, out)
    _check_sections(root, out)
    _check_starter(root, out)
    _check_activities(root, path, out)
    _check_partner_links(root, out)
    _check_variables(root, out)
    _check_qnames(root, out)
    _check_shared_resources(root, out)
    _check_links(root, out)
    _check_bindings(root, out)
    return out


def _targets(arg: Path) -> list[Path]:
    if arg.is_dir():
        return sorted(arg.rglob("*.bwp"))
    return [arg]


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: bw6_structure_check.py <file.bwp | directory> [...]", file=sys.stderr)
        return 2
    failed = 0
    for arg in argv:
        for path in _targets(Path(arg)):
            problems = check(path)
            if problems:
                failed += 1
                print(f"FAIL {path}")
                for p in problems:
                    print(f"  - {p}")
            else:
                print(f"OK   {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
