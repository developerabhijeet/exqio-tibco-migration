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
NOTATION = "http://www.eclipse.org/gmf/runtime/1.0.2/notation"

# --- dialect vocabulary --------------------------------------------------------------
# VERIFIED: harvested from the public BW6 samples under samples/public (TN2018 credit
# application and tibcobwblog bw6.x projects). Seen in real Studio output.
VERIFIED_ACTIVITY_TYPES = frozenset({
    "bw.file.create", "bw.file.write",
    "bw.generalactivities.callprocess", "bw.generalactivities.log",
    "bw.generalactivities.mapper", "bw.generalactivities.timer",
    "bw.http.HTTPReceiver", "bw.http.sendHTTPRequest", "bw.http.sendHTTPResponse",
    "bw.internal.accumulateend", "bw.internal.end", "bw.internal.setEPR",
    "bw.java.javamethod", "bw.jdbc.JDBCQuery", "bw.jdbc.update", "bw.mail.receive",
    "bw.restjson.JsonParser", "bw.restjson.JsonRender", "bw.xml.renderxml",
})
VERIFIED_GROUP_VALUES = frozenset({"iterate", "groupStart", "groupEnd"})
VERIFIED_LINK_TYPES = frozenset({"SUCCESS", "SUCCESSWITHCONDITION", "SUCCESSWITHNOCONDITION"})

# UNVERIFIED: used by the synthetic estate but absent from every public sample. Spellings
# follow TIBCO documentation labels or BPEL 2.0; a TIBCO SME must confirm each one against
# real Studio output (samples/synthetic-gmf/estate.json carries the same checklist).
UNVERIFIED = (
    {"token": "bw.jms.receive", "kind": "activityTypeID",
     "what_to_confirm": "activityTypeID of the JMS Receive Message event source"},
    {"token": "bw.jms.send", "kind": "activityTypeID",
     "what_to_confirm": "activityTypeID of Send JMS Message"},
    {"token": "localTransaction", "kind": "tibex:group",
     "what_to_confirm": "tibex:group value Studio writes for a Local Transaction group"},
    {"token": "ERROR", "kind": "tibex:linkType",
     "what_to_confirm": "tibex:linkType of an error transition"},
    *({"token": attr, "kind": "bw.jms.* config attribute",
       "what_to_confirm": f"EMF attribute name '{attr}' on JMS activity config values "
                          f"(documented label: {label})"}
      for attr, label in (
          ("connectionReference", "JMS Connection"),
          ("destination", "Destination"),
          ("messagingStyle", "Messaging Style: Generic, Queue or Topic"),
          ("messageType", "Message Type, value XMLText for XML Text"),
          ("messageSelector", "Message Selector"),
          ("acknowledgeMode", "Acknowledge Mode, value 1 for Auto"),
          ("pollingInterval", "Polling Interval (sec)"),
          ("deliveryMode", "Delivery Mode, value 2 for Persistent"),
          ("expiration", "Expiration"),
          ("priority", "Priority"),
          ("bodyEditorElement", "XSD element of an XML Text body (child element)"),
      )),
)
UNVERIFIED_ACTIVITY_TYPES = frozenset(
    u["token"] for u in UNVERIFIED if u["kind"] == "activityTypeID")
UNVERIFIED_GROUP_VALUES = frozenset(u["token"] for u in UNVERIFIED if u["kind"] == "tibex:group")
UNVERIFIED_LINK_TYPES = frozenset(u["token"] for u in UNVERIFIED if u["kind"] == "tibex:linkType")
UNVERIFIED_JMS_CONFIG = frozenset(
    u["token"] for u in UNVERIFIED if u["kind"] == "bw.jms.* config attribute")
LINK_TYPES = VERIFIED_LINK_TYPES | UNVERIFIED_LINK_TYPES
BPEL_ACTIVITIES = frozenset({
    "assign", "compensate", "compensateScope", "empty", "exit", "extensionActivity", "flow",
    "forEach", "if", "invoke", "pick", "receive", "repeatUntil", "reply", "rethrow", "scope",
    "sequence", "throw", "validate", "wait", "while"})

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
            if link_types[name] is None:
                out.append(f"links: link '{name}' has no tibex:linkType")
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


def _vocab(kind: str, token: str, verified: frozenset, unverified: frozenset, where: str,
           out: list[str]) -> None:
    if token not in verified and token not in unverified:
        out.append(f"vocabulary: {kind} '{token}' ({where}) is neither verified from the public "
                   "samples nor listed as unverified")


def _check_vocabulary(root: etree._Element, out: list[str]) -> None:
    for el in root.iter(_q(BWEXT, "BWActivity")):
        type_id = el.get("activityTypeID")
        if not type_id:
            continue
        where = f"line {el.sourceline}"
        _vocab("activityTypeID", type_id, VERIFIED_ACTIVITY_TYPES, UNVERIFIED_ACTIVITY_TYPES,
               where, out)
        if type_id in UNVERIFIED_ACTIVITY_TYPES and type_id.startswith("bw.jms."):
            for value in el.iter("value"):
                for attr in value.attrib:
                    if attr.startswith("{"):
                        continue
                    _vocab("bw.jms.* config attribute", attr, frozenset(),
                           UNVERIFIED_JMS_CONFIG, where, out)
                for child in value:
                    if isinstance(child.tag, str):
                        _vocab("bw.jms.* config attribute", etree.QName(child).localname,
                               frozenset(), UNVERIFIED_JMS_CONFIG, where, out)
    for el in root.iter(_q(TIBEX, "extActivity")):
        if el.get("type"):
            _vocab("activityTypeID", el.get("type"), VERIFIED_ACTIVITY_TYPES,
                   UNVERIFIED_ACTIVITY_TYPES, _where(el), out)
    for el in root.iter():
        if isinstance(el.tag, str) and el.get(_q(TIBEX, "group")) is not None:
            _vocab("tibex:group", el.get(_q(TIBEX, "group")), VERIFIED_GROUP_VALUES,
                   UNVERIFIED_GROUP_VALUES, _where(el), out)
    for link in root.iter(_q(BPWS, "link")):
        kind = link.get(_q(TIBEX, "linkType"))
        if kind is not None:
            _vocab("tibex:linkType", kind, VERIFIED_LINK_TYPES, UNVERIFIED_LINK_TYPES,
                   f"link '{link.get('name')}'", out)


def unverified_tokens(path: Path) -> list[str]:
    """Unverified dialect tokens a file uses (informational, not failures)."""
    root = etree.parse(str(path)).getroot()
    used: set[str] = set()
    for el in root.iter(_q(BWEXT, "BWActivity")):
        type_id = el.get("activityTypeID", "")
        if type_id in UNVERIFIED_ACTIVITY_TYPES:
            used.add(type_id)
            for value in el.iter("value"):
                used |= {a for a in value.attrib if a in UNVERIFIED_JMS_CONFIG}
                used |= {etree.QName(c).localname for c in value if isinstance(c.tag, str)
                         and etree.QName(c).localname in UNVERIFIED_JMS_CONFIG}
    for el in root.iter():
        if isinstance(el.tag, str) and el.get(_q(TIBEX, "group")) in UNVERIFIED_GROUP_VALUES:
            used.add(el.get(_q(TIBEX, "group")))
    for link in root.iter(_q(BPWS, "link")):
        if link.get(_q(TIBEX, "linkType")) in UNVERIFIED_LINK_TYPES:
            used.add(link.get(_q(TIBEX, "linkType")))
    return [u["token"] for u in UNVERIFIED if u["token"] in used]


# --- notation diagram ------------------------------------------------------------------


def _bpel_activities(el: etree._Element) -> list[etree._Element]:
    return [c for c in el if isinstance(c.tag, str) and c.tag.startswith(f"{{{BPWS}}}")
            and etree.QName(c).localname in BPEL_ACTIVITIES]


def _unwrap(el: etree._Element) -> etree._Element:
    if el.tag == _q(BPWS, "extensionActivity") and len(el):
        return el[0]
    return el


def _resolve_href(root: etree._Element, href: str) -> etree._Element | None:
    """Resolve a GMF element href such as //0/@process/@activity/@activities.2."""
    parts = href.split("/")
    if parts[:4] != ["", "", "0", "@process"]:
        return root if href == "//0" else None
    cur = root
    try:
        for seg in parts[4:]:
            name, _, idx = seg[1:].partition(".")
            i = int(idx) if idx else 0
            if name == "activity":
                cur = _unwrap(_bpel_activities(cur)[0])
            elif name == "activities":
                cur = _unwrap(_bpel_activities(cur)[i])
            elif name == "messages":
                cur = cur.findall(_q(BPWS, "onMessage"))[i]
            elif name == "faultHandlers":
                cur = cur.find(_q(BPWS, "faultHandlers"))
            elif name == "catchAll":
                cur = cur.find(_q(BPWS, "catchAll"))
            elif name == "catch":
                cur = cur.findall(_q(BPWS, "catch"))[i]
            elif name == "links":
                cur = cur.find(_q(BPWS, "links"))
            elif name == "partnerLinks":
                cur = cur.find(_q(BPWS, "partnerLinks"))
            elif name == "children":
                cur = [c for c in cur if isinstance(c.tag, str)][i]
            else:
                return None
            if cur is None:
                return None
    except (IndexError, ValueError):
        return None
    return cur


def _resolve_path(diagram: etree._Element, path: str) -> etree._Element | None:
    cur = diagram
    try:
        for seg in path.strip("/").split("/"):
            name, _, idx = seg[1:].partition(".")
            cur = cur.findall(name)[int(idx)]
    except (IndexError, ValueError):
        return None
    return cur


def _check_diagram(root: etree._Element, out: list[str]) -> None:
    diagram = root.find(_q(NOTATION, "Diagram"))
    if diagram is None:
        out.append("diagram: notation:Diagram missing")
        return
    shown: set = set()
    for node in diagram.iter("children"):
        ref = node.find("element")
        if ref is None or node.get("type") == "4017":
            continue
        target = _resolve_href(root, ref.get("href", ""))
        if target is None:
            out.append(f"diagram: node href '{ref.get('href')}' does not resolve")
            continue
        if node.get("type", "").startswith(("4002", "4018")):
            shown.add(target)
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        group = el.tag == _q(BPWS, "scope") and el.get(_q(TIBEX, "group")) is not None
        boxed = el.tag in ACTIVITY_TAGS and el.tag not in {
            _q(BPWS, "pick"), _q(BPWS, "onMessage"), _q(BPWS, "catch"), _q(BPWS, "catchAll")}
        if (group or boxed) and el not in shown:
            out.append(f"diagram: {_where(el)} has no 4002/4018 diagram node")
    edges: dict = {}
    for edge in diagram.findall("edges"):
        if edge.get("type") != "4006":
            continue
        ref = edge.find("element")
        link = _resolve_href(root, ref.get("href", "")) if ref is not None else None
        if link is None or link.tag != _q(BPWS, "link"):
            out.append("diagram: edge element does not resolve to a bpws:link")
            continue
        name = link.get("name")
        edges[name] = edges.get(name, 0) + 1
        ends = []
        for attr in ("source", "target"):
            node = _resolve_path(diagram, edge.get(attr, ""))
            el_ref = node.find("element") if node is not None else None
            ends.append(_resolve_href(root, el_ref.get("href", "")) if el_ref is not None else None)
        src = root.find(f".//{_q(BPWS, 'source')}[@linkName='{name}']")
        tgt = root.find(f".//{_q(BPWS, 'target')}[@linkName='{name}']")
        want = [x.getparent().getparent() if x is not None else None for x in (src, tgt)]
        if ends != want:
            out.append(f"diagram: edge for link '{name}' does not join its source and target")
    for link in root.iter(_q(BPWS, "link")):
        if edges.get(link.get("name"), 0) != 1:
            out.append(f"diagram: link '{link.get('name')}' needs exactly one 4006 edge")


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
    _check_vocabulary(root, out)
    _check_diagram(root, out)
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
            notes = unverified_tokens(path)
            if notes:
                print(f"  unverified dialect: {', '.join(notes)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
