"""BW6 ``.bwp`` parser (WS-BPEL 2.0 with TIBCO ``tibex`` extensions) to the neutral IR.

Completeness contract (design spec 4.1): every element of the BPEL tree is
either consumed into the IR, explained through ``collector.explain`` (known
and intentionally not modelled, with a reason) or reported through
``collector.unknown``. A final sweep over the whole tree enforces this, so an
element the parser does not understand can never disappear silently.

Dialect notes (verified against samples/synthetic-gmf and the public BW6
samples under samples/public):

* Activities are ``bpws:extensionActivity`` wrappers around
  ``tibex:activityExtension`` (type in ``tibex:config/bwext:BWActivity/@activityTypeID``),
  ``tibex:extActivity`` (type in ``@type``, e.g. Call Process) or
  ``tibex:receiveEvent`` (event sources), plus plain BPEL elements
  (``bpws:reply``, ``bpws:throw``, ``bpws:invoke``, ``bpws:receive``, ``bpws:pick``,
  ``bpws:empty``, ``bpws:assign``, ``bpws:exit``).
* Links are declared in ``bpws:flow/bpws:links`` and attached through
  ``bpws:sources/bpws:source`` and ``bpws:targets/bpws:target``. Link names are
  NOT reliable (Studio keeps stale names), so endpoints always come from the
  source and target elements. ``tibex:linkType`` is SUCCESS,
  SUCCESSWITHCONDITION, SUCCESSWITHNOCONDITION (the otherwise branch, condition
  ``##otherwise##``) or ERROR.
* Groups are ``bpws:scope`` nodes with ``tibex:group`` (localTransaction,
  iterate, ...) whose body holds ``groupStart`` and ``groupEnd`` marker elements.
  Markers are not activities: links from a groupStart become the group's
  ``entry_ids`` and links into a groupEnd become its ``exit_ids``.
* Unlinked parallel branches (one fork, one join, no link between branches)
  are exposed as a synthesised ``Group(kind="parallel")``.

Structural BPEL nodes with no catalogue type of their own are typed here:
``bpws:empty`` (a no-op placeholder, e.g. an error-branch join) is ``noop``,
the sub-process Start event (``tibex:StartEvent``) and a ``bpws:receive`` or
``bpws:onMessage`` without a service binding are ``noop`` with
``config["role"] == "start"``; a ``bpws:invoke`` is refined from its partner link
binding (call.process, rest.invoke or soap.invoke).
"""
from __future__ import annotations

import hashlib
import re
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from lxml import etree

from engine.ir import catalogue
from engine.ir.complexity import score
from engine.ir.models import (
    Activity,
    FaultHandler,
    Group,
    InputBinding,
    Process,
    ProcessCall,
    Starter,
    Transition,
    UnknownElement,
    Variable,
)
from engine.tibco_parser.completeness import CompletenessCollector
from engine.tibco_parser.xmlutil import NS, local, normalize_bytes, read_xml

BPWS = NS["bpws"]
TIBEX = NS["tibex"]
XSL = NS["xsl"]
WSDL = NS["wsdl"]
BWEXT = "http://tns.tibco.com/bw/model/core/bwext"
NOTATION = "http://www.eclipse.org/gmf/runtime/1.0.2/notation"
SCA_BPEL = "http://docs.oasis-open.org/ns/opencsa/sca-bpel/200801"
XSI = "http://www.w3.org/2001/XMLSchema-instance"

_PREFIXES = {
    BPWS: "bpws", TIBEX: "tibex", XSL: "xsl", WSDL: "wsdl", NS["xsd"]: "xsd",
    BWEXT: "bwext", NOTATION: "notation", SCA_BPEL: "sca-bpel",
}

# Known elements that are intentionally not modelled, with the reason. The whole
# subtree of an explained element is covered by the explanation.
EXPLAINED: dict[str, str] = {
    "tibex:Types": (
        "inline XSD and WSDL definitions (variable, message and fault schemas); the REST "
        "portType attributes are read for the starter and schema files are parsed as contracts"
    ),
    "tibex:ProcessInfo": "process metadata (author, dates, product version, callable and stateless flags)",
    "tibex:ProcessInterface": "interface summary, duplicated by the Start/End variables and partner links",
    "tibex:ProcessTemplateConfigurations": "Studio process template configuration; no runtime behaviour",
    "tibex:NamespaceRegistry": "Studio prefix registry for the mapper; namespaces are resolved from the XML itself",
    "notation:Diagram": "GMF diagram layout only (positions, sizes, colours, fonts); no runtime meaning",
    "bpws:extensions": "BPEL extension namespace declarations (mustUnderstand=no); no runtime behaviour",
    "bpws:import": "schema namespace import; schema files are resolved by the contracts parser",
    "tibex:DesignExpression": "design-time copy of the adjacent bpws:transitionCondition kept for the Studio editor",
    "tibex:DesignNote": "Studio sticky note; documentation only",
    "bpws:correlations": "empty correlation-set placeholder; BW6 does not use BPEL correlation sets",
}
# Explained wherever they appear (everything else must sit where the parser expects it).
_EXPLAIN_ANYWHERE = {"tibex:DesignNote"}

_TIBEX_GROUP_KIND = {
    "localtransaction": "transaction", "xatransaction": "transaction", "jtatransaction": "transaction",
    "transaction": "transaction", "iterate": "iterate", "foreach": "iterate",
    "repeat": "loop", "repeatuntil": "loop", "while": "loop", "repeatonerror": "loop",
    "criticalsection": "critical", "critical": "critical",
}
_GROUP_MARKERS = {"groupStart", "groupEnd"}
_CONSTRUCTOR_MARKERS = {"onMessageStart", "onMessageEnd"}
_EVENT_STARTER_KIND = {
    "rest.receive": "rest", "http.receive": "http", "soap.receive": "soap",
    "jms.receive": "jms", "timer.start": "timer",
}
_HTTP_VERBS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
_SECRET = re.compile(r"password|credential|secret", re.IGNORECASE)
_FUNC_CALL = re.compile(r"(?<![\w$])([A-Za-z_][\w.-]*(?::[A-Za-z_][\w.-]*)?)\s*\(")
_NODE_TESTS = {"text", "node", "comment", "processing-instruction"}
_XSL_CONDITIONAL = {"if", "choose", "when", "otherwise", "for-each", "for-each-group"}
_PLATFORM_HREF = re.compile(r"^platform:/resource/([^/#]+)/([^#]+)")
_OTHERWISE = "##otherwise##"


# --------------------------------------------------------------------- helpers
def _qname(el) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        ns, name = tag[1:].split("}", 1)
        prefix = _PREFIXES.get(ns) or el.prefix
        return f"{prefix}:{name}" if prefix else tag
    return tag


def _children(el):
    return [c for c in el if isinstance(c.tag, str)]


def _attr(el, ns: str, name: str) -> str | None:
    return el.get(f"{{{ns}}}{name}")


def _clark(el, qname: str | None) -> str | None:
    """Resolve a prefixed QName attribute value against the element's namespace map."""
    if not qname:
        return None
    prefix, _, name = qname.rpartition(":")
    ns = (el.nsmap or {}).get(prefix or None)
    return f"{{{ns}}}{name}" if ns else qname


def _strip_prefix(qname: str | None) -> str | None:
    return qname.rsplit(":", 1)[-1] if qname else qname


def _to_raw(el) -> Any:
    """Lossless dict form of an EMF configuration element (attributes, children, text)."""
    out: dict[str, Any] = {}
    for k, v in el.attrib.items():
        out["xsi_type" if k == f"{{{XSI}}}type" else local(k)] = v
    for c in _children(el):
        out.setdefault(local(c.tag), []).append(_to_raw(c))
    text = (el.text or "").strip()
    if not out:
        return text
    if text:
        out["#text"] = text
    return out


def _mask(settings: dict[str, Any], masked: list[str], prefix: str = "") -> None:
    for k, v in list(settings.items()):
        if isinstance(v, str) and v and _SECRET.search(k):
            settings[k] = "***"
            masked.append(prefix + k)
        elif isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    _mask(item, masked, f"{prefix}{k}[{i}].")


def _hrefs(value: Any) -> list[str]:
    if isinstance(value, dict):
        out = [v for k, v in value.items() if k == "href" and isinstance(v, str)]
        for v in value.values():
            out.extend(_hrefs(v))
        return out
    if isinstance(value, list):
        return [h for item in value for h in _hrefs(item)]
    return []


def _uses_functions(expr: str) -> bool:
    return any(m.group(1).lower() not in _NODE_TESTS for m in _FUNC_CALL.finditer(expr))


def _quote(text: str) -> str:
    return f'"{text}"' if '"' not in text else f"'{text}'"


def _first(el, ns: str, name: str):
    return next((c for c in el if c.tag == f"{{{ns}}}{name}"), None)


# --------------------------------------------------------------- xslt mappings
def xslt_bindings(text: str, source_line: int | None) -> list[InputBinding]:
    """One InputBinding per target path produced by the stylesheet's templates.

    ``expression`` is the ``select`` text of ``xsl:value-of``/``copy-of``/``sequence``
    (or the quoted literal for constant content). The language is ``xslt`` when
    the target sits under a conditional or loop instruction or the expression
    calls a function, else ``xpath``. Raises ``etree.XMLSyntaxError`` on bad XSLT.
    """
    root = etree.fromstring(text.strip().encode("utf-8"), etree.XMLParser(resolve_entities=False, no_network=True))
    out: list[InputBinding] = []

    def emit(path: list[str], expr: str, cond: bool) -> None:
        lang = "xslt" if cond or _uses_functions(expr) else "xpath"
        out.append(InputBinding(target="/".join(path) or ".", expression=expr, language=lang,
                                source_line=source_line))

    def walk(el, path: list[str], cond: bool) -> None:
        for c in _children(el):
            if c.tag.startswith(f"{{{XSL}}}"):
                name = local(c.tag)
                if name in ("value-of", "copy-of", "sequence"):
                    expr = c.get("select")
                    if expr is None:
                        expr = _quote("".join(c.itertext()).strip())
                    emit(path, expr, cond)
                elif name in _XSL_CONDITIONAL:
                    walk(c, path, True)
                elif name == "attribute":
                    sub = path + ["@" + (c.get("name") or "")]
                    if _children(c):
                        walk(c, sub, cond)
                    else:
                        emit(sub, _quote((c.text or "").strip()), cond)
                elif name == "element":
                    walk(c, path + [c.get("name") or ""], cond)
                elif name == "text":
                    emit(path, _quote(c.text or ""), cond)
                elif name in ("call-template", "apply-templates"):
                    emit(path, c.get("select") or c.get("name") or "", True)
                else:  # variable, message, number, ...: needs the full stylesheet
                    walk(c, path, True)
            else:
                sub = path + [local(c.tag)]
                for k, v in c.attrib.items():
                    emit(sub + ["@" + local(k)], v if "{" in v else _quote(v), cond or "{" in v)
                if _children(c):
                    walk(c, sub, cond)
                elif (c.text or "").strip():
                    emit(sub, _quote(c.text.strip()), cond)

    for tpl in root.iter(f"{{{XSL}}}template"):
        walk(tpl, [], False)
    return out


# --------------------------------------------------------------- module.bwm
@dataclass
class _Binding:
    kind: str | None  # rest | soap | http | jms | None
    xsi_type: str
    path: str | None = None
    base_path: str | None = None
    connector: str | None = None
    operations: dict[str, str] = field(default_factory=dict)  # operationName -> httpMethod


def _binding_from(el) -> _Binding:
    xsi_type = el.get(f"{{{XSI}}}type") or ""
    low = xsi_type.lower()
    if "rest" in low:
        kind = "rest"
    elif "soap" in low:
        kind = "soap"
    elif "jms" in low:
        kind = "jms"
    elif "http" in low:
        kind = "http"
    else:
        kind = None
    ops = {o.get("operationName"): (o.get("httpMethod") or "") for o in _children(el)
           if local(o.tag) in ("operation", "operationConfiguration") and o.get("operationName")}
    return _Binding(kind=kind, xsi_type=xsi_type, path=el.get("path"), base_path=el.get("basePath"),
                    connector=el.get("connector"), operations=ops)


@lru_cache(maxsize=64)
def _module_bindings(bwm_path: str) -> dict[tuple[str, str, str], _Binding]:
    """(processName, 'service'|'reference', name) -> binding from a module.bwm composite."""
    try:
        root = read_xml(Path(bwm_path))
    except (OSError, etree.XMLSyntaxError):
        return {}
    comp_proc: dict[str, str] = {}
    for comp in _children(root):
        if local(comp.tag) != "component":
            continue
        for impl in _children(comp):
            if local(impl.tag) == "implementation" and impl.get("processName"):
                comp_proc[comp.get("name") or ""] = impl.get("processName")
    out: dict[tuple[str, str, str], _Binding] = {}
    for item in _children(root):
        role = local(item.tag)
        if role not in ("service", "reference") or not item.get("promote"):
            continue
        comp, _, name = item.get("promote").partition("/")
        binding = next((c for c in _children(item) if local(c.tag) == "binding"), None)
        if comp in comp_proc and binding is not None:
            out[(comp_proc[comp], role, name)] = _binding_from(binding)
    return out


def _find_bwm(path: Path) -> Path | None:
    for anc in path.parents:
        cand = anc / "META-INF" / "module.bwm"
        if cand.is_file():
            return cand
    return None


# --------------------------------------------------------------- parser state
@dataclass
class _Link:
    name: str
    decl: Any
    link_type: str | None
    source: str | None = None
    target: str | None = None
    source_el: Any = None
    condition: str | None = None


@dataclass
class _Ctx:
    group: str | None = None
    onmsg: str | None = None
    fault: FaultHandler | None = None


class _BwpParser:
    def __init__(self, path: Path, module: str, application: str, collector: CompletenessCollector,
                 root, raw: bytes) -> None:
        self.path = path
        self.spath = str(path)
        self.module = module
        self.application = application
        self.collector = collector
        self.root = root
        self.raw = raw
        self.done: set = set()  # elements consumed individually
        self.subtree: set = set()  # elements whose whole subtree is consumed or explained
        self.unknowns: list[UnknownElement] = []
        self.activities: dict[str, Activity] = {}
        self.groups: dict[str, Group] = {}
        self.ids: set[str] = set()
        self.node_ctx: dict[str, tuple[str | None, str | None, int | None]] = {}  # group, onMessage, handler
        self.links: list[_Link] = []
        self.flow_links: list[dict[str, _Link]] = []
        self.markers: dict[str, tuple[str, str]] = {}  # marker node -> (marker, owner id)
        self.variables: list[Variable] = []
        self.properties: dict[str, str] = {}  # module property variable -> literal value
        self.partner_links: dict[str, Any] = {}
        self.port_types: dict[str, Any] = {}
        self.fault_handlers: list[FaultHandler] = []
        self.calls: list[ProcessCall] = []
        self.contracts: list[str] = []
        self.starter_candidates: list[tuple[str, str | None, dict[str, Any], Any]] = []
        package, name = self._ids_from_path()
        self.package = package
        self.name = name
        self.pid = f"{package}.{name}" if package else name
        bwm = _find_bwm(path)
        self.bwm = _module_bindings(str(bwm)) if bwm else {}

    # ---- identity
    def _ids_from_path(self) -> tuple[str, str]:
        parts = self.path.parts
        if "Processes" in parts[:-1]:
            i = len(parts) - 2 - list(reversed(parts[:-1])).index("Processes")
            return ".".join(parts[i + 1:-1]), self.path.stem
        declared = self.root.get("name") or self.path.stem
        package, _, name = declared.rpartition(".")
        return package, name or self.path.stem

    def _unique(self, wanted: str | None, fallback: str) -> str:
        base = wanted or fallback
        out, n = base, 1
        while out in self.ids:
            n += 1
            out = f"{base}~{n}"
        self.ids.add(out)
        return out

    # ---- completeness bookkeeping
    def _xpath(self, el) -> str:
        steps = []
        cur = el
        while cur is not None:
            parent = cur.getparent()
            q = _qname(cur)
            if parent is None:
                steps.append(f"{q}[1]")
            else:
                same = [c for c in parent if c.tag == cur.tag]
                steps.append(f"{q}[{same.index(cur) + 1}]")
            cur = parent
        return f"{self.spath}#/" + "/".join(reversed(steps))

    def _explain(self, el, reason: str | None = None) -> None:
        self.subtree.add(el)
        self.collector.explain(self._xpath(el), reason or EXPLAINED[_qname(el)])

    def _unknown(self, el, reason: str) -> None:
        tag = _qname(el) or "?"
        item = self.collector.unknown(self._xpath(el), tag, f"line {el.sourceline}: {reason}")
        self.unknowns.append(item)
        self.subtree.add(el)

    def _sweep(self, el) -> None:
        if el in self.subtree:
            return
        if el not in self.done:
            q = _qname(el)
            if q in _EXPLAIN_ANYWHERE:
                self._explain(el)
            else:
                self._unknown(el, "element not consumed by the BW6 parser")
            return
        for c in _children(el):
            self._sweep(c)

    # ---- process level
    def parse(self) -> Process:
        root = self.root
        if _qname(root) != "bpws:process":
            raise ValueError(f"not a BW6 process: root element is {_qname(root)}")
        self.done.add(root)
        for c in _children(root):
            q = _qname(c)
            if q == "tibex:Types":
                self._types(c)
            elif q in ("tibex:ProcessInfo", "tibex:ProcessInterface", "tibex:ProcessTemplateConfigurations",
                       "tibex:NamespaceRegistry", "notation:Diagram", "bpws:extensions", "bpws:import"):
                self._explain(c)
            elif q == "bpws:partnerLinks":
                self._partner_links(c)
            elif q == "bpws:variables":
                self._variables(c, "process")
        for c in _children(root):
            if _qname(c) == "bpws:scope":
                self._structural_scope(c, _Ctx())
            elif _qname(c) == "bpws:faultHandlers":
                self._fault_handlers(c, _Ctx())
        transitions = self._finish_links()
        self._finish_onmessage(transitions)
        self._finish_groups(transitions)
        self._synthesise_parallel(transitions)
        starter = self._starter()
        self._sweep(root)
        proc = Process(
            id=self.pid, application=self.application, module=self.module, package=self.package,
            name=self.name, dialect="bw6", source_path=self.spath, starter=starter,
            activities=list(self.activities.values()), transitions=transitions,
            groups=list(self.groups.values()), variables=self.variables,
            fault_handlers=self.fault_handlers, calls=self.calls, unknown_elements=list(self.unknowns),
            contracts_used=sorted(set(self.contracts)), raw_xml_sha256=hashlib.sha256(self.raw).hexdigest(),
        )
        proc.complexity = score(proc)
        return proc

    def _types(self, el) -> None:
        self._explain(el)
        for pt in el.iter(f"{{{WSDL}}}portType"):
            if pt.get("name"):
                self.port_types[pt.get("name")] = pt
                resource = _attr(pt, TIBEX, "bw.rest.resource")
                if resource and _attr(pt, TIBEX, "source") == "bw.rest.service":
                    self.contracts.append(f"{self.module}/{resource}")

    def _partner_links(self, el) -> None:
        self.done.add(el)
        for pl in _children(el):
            if _qname(pl) == "bpws:partnerLink":
                # declarations; their binding children are read when an activity uses them
                self.subtree.add(pl)
                self.partner_links[pl.get("name") or ""] = pl

    def _variables(self, el, scope: str) -> None:
        self.done.add(el)
        for v in _children(el):
            if _qname(v) != "bpws:variable":
                continue
            self.done.add(v)
            schema = v.get("element") or v.get("messageType") or v.get("type")
            self.variables.append(Variable(name=v.get("name") or "", schema=_clark(v, schema), scope=scope))
            if _attr(v, SCA_BPEL, "property") == "yes":
                frm = _first(v, BPWS, "from")
                lit = _first(frm, BPWS, "literal") if frm is not None else None
                if lit is not None and not _children(lit) and len(_children(frm)) == 1:
                    self.subtree.add(frm)
                    self.properties[v.get("name") or ""] = (lit.text or "").strip()

    # ---- containers
    def _structural_scope(self, el, ctx: _Ctx) -> None:
        """A bpws:scope that only wraps a flow (process body, onMessage body, catch body, loop body)."""
        self.done.add(el)
        for c in _children(el):
            q = _qname(c)
            if q == "bpws:variables":
                self._variables(c, "scope")
            elif q == "bpws:faultHandlers":
                self._fault_handlers(c, ctx)
            elif q == "bpws:flow":
                self._flow(c, ctx)

    def _flow(self, el, ctx: _Ctx) -> None:
        self.done.add(el)
        declared: dict[str, _Link] = {}
        for c in _children(el):
            if _qname(c) == "bpws:links":
                self.done.add(c)
                for link in _children(c):
                    if _qname(link) == "bpws:link":
                        self.done.add(link)
                        rec = _Link(name=link.get("name") or "", decl=link,
                                    link_type=_attr(link, TIBEX, "linkType"))
                        declared[rec.name] = rec
                        self.links.append(rec)
        self.flow_links.append(declared)
        try:
            for c in _children(el):
                if _qname(c) != "bpws:links":
                    self._node(c, ctx)
        finally:
            self.flow_links.pop()

    def _resolve_link(self, name: str) -> _Link | None:
        for declared in reversed(self.flow_links):
            if name in declared:
                return declared[name]
        return None

    def _register_links(self, el, node_id: str) -> None:
        for c in _children(el):
            q = _qname(c)
            if q == "bpws:targets":
                self.done.add(c)
                for t in _children(c):
                    if _qname(t) == "bpws:target":
                        rec = self._resolve_link(t.get("linkName") or "")
                        if rec is None:
                            self._unknown(t, f"target link {t.get('linkName')!r} is not declared")
                            continue
                        self.done.add(t)
                        rec.target = node_id
            elif q == "bpws:sources":
                self.done.add(c)
                for s in _children(c):
                    if _qname(s) != "bpws:source":
                        continue
                    rec = self._resolve_link(s.get("linkName") or "")
                    if rec is None:
                        self._unknown(s, f"source link {s.get('linkName')!r} is not declared")
                        continue
                    self.done.add(s)
                    rec.source, rec.source_el = node_id, s
                    for sc in _children(s):
                        sq = _qname(sc)
                        if sq == "bpws:transitionCondition":
                            self.subtree.add(sc)
                            rec.condition = (sc.text or "").strip()
                        elif sq == "tibex:DesignExpression":
                            self._explain(sc)

    def _place(self, node_id: str, ctx: _Ctx, is_activity: bool) -> None:
        self.node_ctx[node_id] = (ctx.group, ctx.onmsg, id(ctx.fault) if ctx.fault else None)
        if ctx.group:
            self.groups[ctx.group].member_ids.append(node_id)
        if ctx.fault is not None and is_activity:
            ctx.fault.activity_ids.append(node_id)

    # ---- nodes
    def _node(self, el, ctx: _Ctx) -> None:
        q = _qname(el)
        if q == "bpws:extensionActivity":
            inner = _children(el)
            if len(inner) != 1:
                return  # left unconsumed; the sweep reports it
            self.done.add(el)
            iq = _qname(inner[0])
            if iq == "tibex:activityExtension":
                self._activity_extension(inner[0], ctx)
            elif iq == "tibex:extActivity":
                self._ext_activity(inner[0], ctx)
            elif iq == "tibex:receiveEvent":
                self._receive_event(inner[0], ctx)
        elif q in ("bpws:empty", "bpws:assign") and (
            el.get(f"{{{TIBEX}}}group") in _GROUP_MARKERS or el.get(f"{{{TIBEX}}}constructor") in _CONSTRUCTOR_MARKERS
        ):
            self._marker(el, ctx)
        elif q == "bpws:empty":
            self._simple(el, ctx, "noop", {})
        elif q == "bpws:exit":
            self._simple(el, ctx, "end", {"exit": True})
        elif q == "bpws:assign":
            self._assign(el, ctx)
        elif q in ("bpws:throw", "bpws:rethrow"):
            cfg = {"fault_name": _strip_prefix(el.get("faultName")), "fault_qname": _clark(el, el.get("faultName")),
                   "fault_variable": el.get("faultVariable")}
            self._simple(el, ctx, "throw", {k: v for k, v in cfg.items() if v})
        elif q == "bpws:reply":
            cfg = {"operation": el.get("operation"), "partner_link": el.get("partnerLink"),
                   "fault_name": el.get("faultName"), "variable": el.get("variable")}
            self._simple(el, ctx, "reply", {k: v for k, v in cfg.items() if v})
        elif q == "bpws:wait":
            self._wait(el, ctx)
        elif q == "bpws:invoke":
            self._invoke(el, ctx)
        elif q == "bpws:receive":
            self._receive(el, ctx)
        elif q == "bpws:pick":
            self._pick(el, ctx)
        elif q == "bpws:scope":
            self._group_scope(el, ctx)
        elif q in ("bpws:forEach", "bpws:repeatUntil", "bpws:while", "bpws:flow"):
            self._bare_group(el, ctx)
        # anything else stays unconsumed and is reported by the sweep

    def _new_activity(self, el, ctx: _Ctx, name: str | None, native: str, type_: str,
                      config: dict[str, Any], output_var: str | None = None) -> Activity:
        aid = self._unique(name, f"{local(el.tag)}@{el.sourceline}")
        act = Activity(id=aid, name=name or aid, type=type_, native_type=native, config=config,
                       output_schema=self._var_schema(output_var), source_line=el.sourceline,
                       group_id=ctx.group)
        self.activities[aid] = act
        self._place(aid, ctx, True)
        self._register_links(el, aid)
        return act

    def _var_schema(self, name: str | None) -> str | None:
        if not name:
            return None
        return next((v.schema for v in self.variables if v.name == name), None)

    def _simple(self, el, ctx: _Ctx, type_: str, config: dict[str, Any]) -> Activity:
        self.done.add(el)
        native = _qname(el)
        act = self._new_activity(el, ctx, el.get("name"), native, type_, config)
        self._correlations(el)
        self._bindings(el, act)
        return act

    def _marker(self, el, ctx: _Ctx) -> None:
        self.done.add(el)
        marker = el.get(f"{{{TIBEX}}}group") or el.get(f"{{{TIBEX}}}constructor")
        mid = self._unique(el.get("name"), f"marker@{el.sourceline}")
        owner = ctx.onmsg if marker in _CONSTRUCTOR_MARKERS else ctx.group
        if owner is None:
            self._unknown(el, f"{marker} marker outside a group or onMessage body")
            return
        self.markers[mid] = (marker, owner)
        self._register_links(el, mid)
        if _qname(el) == "bpws:assign":
            # the iterate group's current-element assignment
            for copy in _children(el):
                if _qname(copy) != "bpws:copy":
                    continue
                frm, to = _first(copy, BPWS, "from"), _first(copy, BPWS, "to")
                if frm is None or to is None or len(_children(copy)) != 2:
                    continue
                self.subtree.update((copy, frm, to))
                self.groups[owner].config["current_element"] = {
                    "variable": to.get("variable"), "expression": (frm.text or "").strip(),
                }

    def _assign(self, el, ctx: _Ctx) -> None:
        self.done.add(el)
        act = self._new_activity(el, ctx, el.get("name"), "bpws:assign", "assign", {})
        copies = []
        for copy in _children(el):
            if _qname(copy) != "bpws:copy":
                continue
            frm, to = _first(copy, BPWS, "from"), _first(copy, BPWS, "to")
            if frm is None or to is None or len(_children(copy)) != 2:
                continue
            self.subtree.update((copy, frm, to))
            target = to.get("variable") or ""
            if to.get("part"):
                target += "." + to.get("part")
            expr = (frm.text or "").strip() or (frm.get("variable") or "")
            copies.append({"from": expr, "to": target})
            act.input_bindings.append(InputBinding(
                target=target, expression=expr, language="xslt" if _uses_functions(expr) else "xpath",
                source_line=copy.sourceline))
        act.config["copies"] = copies
        self._bindings(el, act)

    def _wait(self, el, ctx: _Ctx) -> None:
        self.done.add(el)
        cfg: dict[str, Any] = {}
        for c in _children(el):
            if _qname(c) in ("bpws:for", "bpws:until"):
                self.subtree.add(c)
                cfg[local(c.tag)] = (c.text or "").strip()
        act = self._new_activity(el, ctx, el.get("name"), "bpws:wait", "wait", cfg)
        self._bindings(el, act)

    # ---- tibex activities
    def _bw_activity(self, bw) -> tuple[str, dict[str, Any]]:
        """Native type and raw settings from a bwext:BWActivity element."""
        self.done.add(bw)
        native = bw.get("activityTypeID") or ""
        settings: dict[str, Any] = {}
        meta: dict[str, Any] = {}
        if bw.get("version"):
            meta["palette_version"] = bw.get("version")
        for ac in _children(bw):
            if local(ac.tag) != "activityConfig":
                continue
            self.done.add(ac)
            for prop in _children(ac):
                if local(prop.tag) != "properties":
                    continue
                self.done.add(prop)
                pname = prop.get("name") or "config"
                for pc in _children(prop):
                    if local(pc.tag) == "type":
                        self.subtree.add(pc)
                        meta["palette_type"] = pc.get("href")
                    elif local(pc.tag) == "value":
                        self.subtree.add(pc)
                        raw = _to_raw(pc)
                        if pname == "config":
                            settings.update(raw if isinstance(raw, dict) else {"#text": raw})
                        else:
                            settings[pname] = raw
        masked: list[str] = []
        _mask(settings, masked)
        config: dict[str, Any] = {"settings": settings, **meta}
        if masked:
            config["masked_fields"] = masked
        for href in _hrefs(settings):
            m = _PLATFORM_HREF.match(href)
            if m:
                self.contracts.append(f"{m.group(1)}/{m.group(2)}")
        return native, config

    def _normalise(self, canon: str | None, config: dict[str, Any]) -> str | None:
        """Lift the well-known settings to top-level config keys; refine jms.send to a topic."""
        s = config.get("settings", {})

        def put(key: str, src: str) -> None:
            if s.get(src) not in (None, ""):
                config[key] = s[src]

        if canon and canon.startswith("jdbc."):
            put("sql", "sqlStatement")
            put("resource_property", "sharedResourceProperty")
            put("max_rows", "maxRows")
            put("timeout", "timeout")
            config["parameters"] = [
                {"name": p.get("ParameterName"), "type": p.get("DataTypeDisplayValue") or p.get("DataType")}
                for p in s.get("PreparedParameters", []) if isinstance(p, dict)
            ]
        elif canon and canon.startswith("jms."):
            put("destination", "destination")
            put("selector", "messageSelector")
            put("messaging_style", "messagingStyle")
            put("message_type", "messageType")
            put("resource_property", "connectionReference")
            if canon == "jms.send" and str(s.get("messagingStyle", "")).lower() == "topic":
                canon = "jms.publish"
        elif canon == "http.invoke":
            put("resource_property", "httpClientResource")
        elif canon == "http.receive":
            put("resource_property", "httpConnection")
        elif canon == "timer.start":
            put("start_time", "startTime")
            put("interval_unit", "intervalUnit")
            put("time_interval", "timeInterval")
            put("run_once", "runOnce")
        elif canon == "log":
            put("level", "role")
        elif canon == "java.invoke":
            put("class_name", "className")
            put("method_name", "methodName")
        elif canon == "assign":
            put("reference_name", "referenceName")
        prop = config.get("resource_property")
        if prop and self.properties.get(prop):
            config["resource_ref"] = f"{self.module}.{self.properties[prop]}"
        return canon

    def _typed(self, native: str, config: dict[str, Any]) -> str:
        canon = self._normalise(catalogue.canonical(native), config)
        if canon is None:
            self.collector.unknown_type(self.pid, native)
            return "unknown"
        return canon

    def _activity_extension(self, el, ctx: _Ctx) -> None:
        self.done.add(el)
        cfg_el = _first(el, TIBEX, "config")
        bws = _children(cfg_el) if cfg_el is not None else []
        native, config = "", {"settings": {}}
        if cfg_el is not None and len(bws) == 1 and local(bws[0].tag) == "BWActivity":
            self.done.add(cfg_el)
            native, config = self._bw_activity(bws[0])
        type_ = self._typed(native, config) if native else "unknown"
        if not native:
            self.collector.unknown_type(self.pid, "tibex:activityExtension")
        marker = _attr(el, TIBEX, "group")
        if marker:
            config["group_marker"] = marker
        act = self._new_activity(el, ctx, el.get("name"), native or "tibex:activityExtension", type_, config,
                                 el.get("outputVariable"))
        if marker == "groupEnd" and ctx.group:
            self.groups[ctx.group].config.setdefault("exit_ids", []).append(act.id)
        self._bindings(el, act)

    def _ext_activity(self, el, ctx: _Ctx) -> None:
        self.done.add(el)
        native = el.get("type") or ""
        config: dict[str, Any] = {}
        cp = _first(el, TIBEX, "CallProcess")
        if cp is not None and not _children(cp):
            self.done.add(cp)
            config["sub_process"] = cp.get("subProcessName")
        type_ = self._typed(native, config) if native else "unknown"
        if not native:
            self.collector.unknown_type(self.pid, "tibex:extActivity")
        act = self._new_activity(el, ctx, el.get("name"), native or "tibex:extActivity", type_, config,
                                 el.get("outputVariable"))
        if type_ == "call.process" and config.get("sub_process"):
            self.calls.append(ProcessCall(activity_id=act.id, target_process=config["sub_process"]))
        self._bindings(el, act)

    def _receive_event(self, el, ctx: _Ctx) -> None:
        self.done.add(el)
        src = _first(el, TIBEX, "eventSource")
        inner = _children(src) if src is not None else []
        native, config, type_, kind = "", {}, "unknown", None
        if src is not None and len(inner) == 1:
            self.done.add(src)
            iq = _qname(inner[0])
            if iq == "tibex:StartEvent" and not _children(inner[0]):
                self.done.add(inner[0])
                native, type_, kind, config = "tibex:StartEvent", "noop", "subprocess", {"role": "start"}
            elif local(inner[0].tag) == "BWActivity":
                native, config = self._bw_activity(inner[0])
                type_ = self._typed(native, config)
                kind = _EVENT_STARTER_KIND.get(type_)
        if not native:
            self.collector.unknown_type(self.pid, "tibex:receiveEvent")
            native = "tibex:receiveEvent"
        self._correlations(el)
        act = self._new_activity(el, ctx, el.get("name"), native, type_, config, el.get("variable"))
        self._bindings(el, act)
        self.starter_candidates.append((act.id, kind, self._starter_config(kind, act), el))

    def _correlations(self, el) -> None:
        for c in _children(el):
            if _qname(c) == "bpws:correlations" and not _children(c):
                self._explain(c)

    # ---- partner-link based activities
    def _service(self, partner_link: str | None, port_type: str | None, operation: str | None) -> dict[str, Any]:
        """Service binding of an inbound operation: module.bwm first, then the inline WSDL portType."""
        info: dict[str, Any] = {"partner_link": partner_link, "operation": operation}
        binding = self.bwm.get((self.pid, "service", partner_link or ""))
        pt = self.port_types.get(_strip_prefix(port_type) or "")
        if binding is not None:
            info["binding"] = binding.xsi_type
            info["kind"] = binding.kind
            verb = binding.operations.get(operation or "")
            if verb:
                info["verb"] = verb.upper()
            if binding.path:
                info["path"] = binding.path
            if binding.connector:
                info["connector"] = binding.connector
        elif pt is not None and (_attr(pt, TIBEX, "source") or "").startswith("bw.rest"):
            info["kind"] = "rest"
            info["binding"] = _attr(pt, TIBEX, "source")
        else:
            info["kind"] = None
        if pt is not None:
            if _attr(pt, TIBEX, "bw.rest.apipath") and "path" not in info:
                info["path"] = _attr(pt, TIBEX, "bw.rest.apipath")
            if _attr(pt, TIBEX, "bw.rest.resource"):
                info["service_descriptor"] = _attr(pt, TIBEX, "bw.rest.resource")
        if info["kind"] == "rest" and "verb" not in info and (operation or "").upper() in _HTTP_VERBS:
            info["verb"] = operation.upper()
        info["port_type"] = _strip_prefix(port_type)
        return {k: v for k, v in info.items() if v is not None}

    def _inbound(self, el, ctx: _Ctx, name: str | None, native: str) -> Activity:
        info = self._service(el.get("partnerLink"), el.get("portType"), el.get("operation"))
        kind = info.pop("kind", None)
        type_ = {"rest": "rest.receive", "soap": "soap.receive", "http": "http.receive", "jms": "jms.receive"}.get(
            kind or "", "noop")
        if type_ == "noop":
            info["role"] = "start"
        act = self._new_activity(el, ctx, name, native, type_, info, el.get("variable"))
        self._correlations(el)
        self._bindings(el, act)
        if el.get("createInstance") == "yes" or (el.getparent() is not None
                                                 and el.getparent().get("createInstance") == "yes"):
            self.starter_candidates.append((act.id, kind or "subprocess", self._starter_config(kind or "subprocess",
                                                                                              act), el))
        return act

    def _receive(self, el, ctx: _Ctx) -> None:
        self.done.add(el)
        self._inbound(el, ctx, el.get("name"), "bpws:receive")

    def _pick(self, el, ctx: _Ctx) -> None:
        self.done.add(el)
        first: str | None = None
        for om in _children(el):
            if _qname(om) != "bpws:onMessage":
                continue
            self.done.add(om)
            name = om.get("variable") or om.get("operation")
            act = self._inbound(om, ctx, name, "bpws:onMessage")
            first = first or act.id
            body = [c for c in _children(om) if _qname(c) == "bpws:scope"]
            if len(body) == 1:
                self._structural_scope(body[0], _Ctx(group=ctx.group, onmsg=act.id, fault=ctx.fault))
        if first:
            self._register_links(el, first)

    def _invoke(self, el, ctx: _Ctx) -> None:
        self.done.add(el)
        pl_name = el.get("partnerLink") or ""
        pl = self.partner_links.get(pl_name)
        operation = el.get("operation")
        config: dict[str, Any] = {"partner_link": pl_name, "operation": operation}
        type_ = catalogue.canonical("bpws:invoke") or "call.process"
        target = None
        wire = _first(pl, TIBEX, "ReferenceWire") if pl is not None else None
        refb = _first(pl, TIBEX, "ReferenceBinding") if pl is not None else None
        if wire is not None:
            type_ = "call.process"
            config["dynamic"] = wire.get("dynamic") == "true"
            target = wire.get("processName") or None
            if target:
                config["sub_process"] = target
        else:
            binding = None
            if refb is not None:
                inner = next((b for b in refb.iter() if local(b.tag) == "binding" and b.get(f"{{{XSI}}}type")), None)
                binding = _binding_from(inner) if inner is not None else None
            if binding is None and pl is not None:
                binding = self.bwm.get((self.pid, "reference", _attr(pl, SCA_BPEL, "reference") or pl_name))
            pt = self.port_types.get(_strip_prefix(el.get("portType")) or "")
            if binding is not None:
                config["binding"] = binding.xsi_type
                if binding.kind == "rest":
                    type_ = "rest.invoke"
                elif binding.kind == "soap":
                    type_ = "soap.invoke"
                if binding.path:
                    config["path"] = binding.path
                if binding.base_path:
                    config["base_path"] = binding.base_path
                if binding.connector:
                    config["connector"] = binding.connector
                if binding.operations.get(operation or ""):
                    config["verb"] = binding.operations[operation].upper()
            elif pt is not None and _attr(pt, TIBEX, "source") == "bw.rest.reference":
                type_ = "rest.invoke"
                config["binding"] = "bw.rest.reference"
                config["path"] = _attr(pt, TIBEX, "bw.rest.apipath")
                config["host"] = _attr(pt, TIBEX, "bw.rest.host")
                if (operation or "").upper() in _HTTP_VERBS:
                    config["verb"] = operation.upper()
            else:
                config["unresolved_partner_link"] = True
        act = self._new_activity(el, ctx, el.get("name"), "bpws:invoke", type_,
                                 {k: v for k, v in config.items() if v is not None}, el.get("outputVariable"))
        if target:
            self.calls.append(ProcessCall(activity_id=act.id, target_process=target))
        self._correlations(el)
        self._bindings(el, act)

    # ---- groups
    def _new_group(self, el, ctx: _Ctx, name: str | None, kind: str, config: dict[str, Any]) -> Group:
        gid = self._unique(name, f"group@{el.sourceline}")
        group = Group(id=gid, kind=kind, config={"source_line": el.sourceline, **config})
        self.groups[gid] = group
        self._place(gid, ctx, False)
        return group

    def _group_kind(self, el, marker: str | None, body_tag: str | None) -> str:
        if marker:
            kind = _TIBEX_GROUP_KIND.get(marker.lower()) or catalogue.group_kind(marker)
            if kind is None:
                self._unknown_attr(el, f"tibex:group={marker!r} has no IR group kind; modelled as scope")
                return "scope"
            return kind
        return {"bpws:forEach": "iterate", "bpws:repeatUntil": "loop", "bpws:while": "loop",
                "bpws:flow": "parallel"}.get(body_tag or "", "scope")

    def _unknown_attr(self, el, reason: str) -> None:
        item = self.collector.unknown(self._xpath(el), _qname(el), f"line {el.sourceline}: {reason}")
        self.unknowns.append(item)

    def _group_scope(self, el, ctx: _Ctx) -> None:
        self.done.add(el)
        marker = _attr(el, TIBEX, "group")
        bodies = [c for c in _children(el) if _qname(c) in ("bpws:flow", "bpws:forEach", "bpws:repeatUntil",
                                                             "bpws:while")]
        body = bodies[0] if len(bodies) == 1 else None
        kind = self._group_kind(el, marker, _qname(body) if body is not None and _qname(body) != "bpws:flow"
                                else None)
        cfg = {"tibex_group": marker} if marker else {}
        group = self._new_group(el, ctx, el.get("name"), kind, cfg)
        self._register_links(el, group.id)
        inner = _Ctx(group=group.id, onmsg=ctx.onmsg, fault=ctx.fault)
        for c in _children(el):
            q = _qname(c)
            if q == "bpws:variables":
                self._variables(c, "scope")
            elif q == "bpws:faultHandlers":
                self._fault_handlers(c, ctx)
            elif c is body:
                if q == "bpws:flow":
                    self._flow(c, inner)
                else:
                    self._loop_body(c, group, inner)

    def _bare_group(self, el, ctx: _Ctx) -> None:
        """A loop or nested flow placed directly in a flow, without a tibex group scope."""
        self.done.add(el)
        q = _qname(el)
        kind = self._group_kind(el, _attr(el, TIBEX, "group"), q)
        group = self._new_group(el, ctx, el.get("name"), kind, {})
        self._register_links(el, group.id)
        inner = _Ctx(group=group.id, onmsg=ctx.onmsg, fault=ctx.fault)
        if q == "bpws:flow":
            self.done.discard(el)
            self._flow(el, inner)
        else:
            self._loop_body(el, group, inner)

    def _loop_body(self, el, group: Group, ctx: _Ctx) -> None:
        self.done.add(el)
        q = _qname(el)
        group.config["loop"] = local(el.tag)
        if el.get("counterName"):
            group.config["counter"] = el.get("counterName")
        if el.get("parallel"):
            group.config["parallel"] = el.get("parallel") == "yes"
        for c in _children(el):
            cq = _qname(c)
            if cq == "tibex:DesignExpression" and c.get("name") == "variableList":
                exprs = [e for e in _children(c) if _qname(e) == "tibex:expression"]
                if len(exprs) == 1 and not _children(exprs[0]):
                    self.subtree.add(c)
                    group.config["variable_list"] = exprs[0].get("expression")
            elif cq in ("bpws:startCounterValue", "bpws:finalCounterValue", "bpws:condition"):
                self.subtree.add(c)
                key = {"bpws:startCounterValue": "start_counter", "bpws:finalCounterValue": "final_counter",
                       "bpws:condition": "condition"}[cq]
                group.config[key] = (c.text or "").strip()
            elif cq == "bpws:completionCondition":
                self.subtree.add(c)
                group.config["completion_condition"] = (etree.tostring(c, encoding="unicode", with_tail=False))
            elif cq == "bpws:scope":
                self._structural_scope(c, ctx)
            elif q != "bpws:forEach" and cq == "bpws:flow":
                self._flow(c, ctx)

    # ---- fault handlers
    def _fault_handlers(self, el, ctx: _Ctx) -> None:
        self.done.add(el)
        for c in _children(el):
            q = _qname(c)
            if q not in ("bpws:catch", "bpws:catchAll"):
                continue
            self.done.add(c)
            fault = _strip_prefix(c.get("faultName")) if q == "bpws:catch" else None
            fid = self._unique(f"catch:{fault}" if fault else "catchAll", "catch")
            handler = FaultHandler(id=fid, fault_name=fault)
            self.fault_handlers.append(handler)
            for body in _children(c):
                if _qname(body) == "bpws:scope":
                    self._structural_scope(body, _Ctx(group=ctx.group, onmsg=ctx.onmsg, fault=handler))

    # ---- bindings
    def _bindings(self, el, act: Activity) -> None:
        container = _first(el, TIBEX, "inputBindings")
        direct = [c for c in _children(el) if _qname(c) == "tibex:inputBinding"]
        parts: list = []
        if container is not None:
            self.done.add(container)
            parts = [c for c in _children(container) if _qname(c) in ("tibex:inputBinding", "tibex:partBinding")]
        for d in direct:
            text = d.get("expression") or d.text or ""
            self.done.add(d)
            if parts:
                act.config["message_xslt"] = text  # full-message form of the part bindings below
            else:
                parts.append(d)
        if not parts and el.get("expression") and "xslt" in (el.get("expressionLanguage") or ""):
            # older Studio exports keep the only copy of the mapping on the activity itself
            self._parse_binding(el, el.get("expression"), el.get("expressionLanguage") or "", act)
        for b in parts:
            self.done.add(b)
            self._parse_binding(b, b.get("expression") or b.text or "", b.get("expressionLanguage") or "", act)

    def _parse_binding(self, el, text: str, language: str, act: Activity) -> None:
        if not text.strip():
            return
        if "xslt" not in language.lower() and not (not language and text.lstrip().startswith("<")):
            act.input_bindings.append(InputBinding(target=".", expression=text.strip(), language="xpath",
                                                   source_line=el.sourceline))
            return
        act.config.setdefault("input_xslt", []).append(text)
        try:
            act.input_bindings.extend(xslt_bindings(text, el.sourceline))
        except etree.XMLSyntaxError as exc:
            self._unknown_attr(el, f"input binding XSLT does not parse: {exc}")

    # ---- links to transitions
    def _finish_links(self) -> list[Transition]:
        out: list[Transition] = []
        by_source: dict[str, list[_Link]] = {}
        for rec in self.links:
            if rec.source and rec.target:
                by_source.setdefault(rec.source, []).append(rec)
        tids: set[str] = set()
        for rec in self.links:
            if not (rec.source and rec.target):
                self._unknown_attr(rec.decl, f"link {rec.name!r} is missing its source or target")
                continue
            kind, expr = self._link_kind(rec, by_source[rec.source])
            src, tgt = rec.source, rec.target
            if src in self.markers:
                marker, owner = self.markers[src]
                if marker == "groupStart" and kind == "success":
                    self.groups[owner].config.setdefault("entry_ids", []).append(tgt)
                    continue
                if marker == "onMessageStart" and kind == "success":
                    src = owner
                else:
                    self._unknown_attr(rec.decl, f"unexpected {kind} link from a {marker} marker")
                    continue
            if tgt in self.markers:
                marker, owner = self.markers[tgt]
                if marker == "groupEnd" and kind in ("success", "otherwise"):
                    self.groups[owner].config.setdefault("exit_ids", []).append(src)
                    continue
                if marker == "onMessageEnd" and kind in ("success", "otherwise"):
                    self.activities[owner].config.setdefault("exit_ids", []).append(src)
                    continue
                self._unknown_attr(rec.decl, f"unexpected {kind} link into a {marker} marker")
                continue
            tid, n = rec.name, 1
            while tid in tids:
                n += 1
                tid = f"{rec.name}~{n}"
            tids.add(tid)
            out.append(Transition(id=tid, from_id=src, to_id=tgt, kind=kind, expression=expr,
                                  source_line=rec.source_el.sourceline if rec.source_el is not None else None))
        return out

    def _link_kind(self, rec: _Link, siblings: list[_Link]) -> tuple[str, str | None]:
        lt = (rec.link_type or "").upper()
        cond = rec.condition or None
        if lt.startswith("ERROR"):
            return "error", (cond if cond and cond != _OTHERWISE else None)
        if cond == _OTHERWISE or lt == "SUCCESSWITHNOCONDITION":
            return "otherwise", None
        if cond:
            return "conditional", cond
        if lt == "SUCCESSWITHCONDITION":
            self._unknown_attr(rec.decl, f"conditional link {rec.name!r} has no transitionCondition")
            return "conditional", None
        if lt not in ("", "SUCCESS"):
            self._unknown_attr(rec.decl, f"tibex:linkType={rec.link_type!r} is not a known link type")
        if any(s is not rec and s.condition and s.condition != _OTHERWISE for s in siblings):
            return "otherwise", None
        return "success", None

    def _finish_onmessage(self, transitions: list[Transition]) -> None:
        """Enter each onMessage body at its unlinked root nodes (BW6 starts them on receipt)."""
        incoming = {t.to_id for t in transitions}
        for aid, act in self.activities.items():
            if act.native_type != "bpws:onMessage":
                continue
            for nid, (grp, onmsg, fault) in self.node_ctx.items():
                if onmsg == aid and fault is None and grp == act.group_id and nid not in incoming \
                        and nid not in self.markers:
                    transitions.append(Transition(id=f"{aid}->{nid}", from_id=aid, to_id=nid, kind="success"))
                    incoming.add(nid)

    def _finish_groups(self, transitions: list[Transition]) -> None:
        for g in self.groups.values():
            members = set(g.member_ids)
            if "entry_ids" not in g.config:
                inner_targets = {t.to_id for t in transitions if t.from_id in members and t.to_id in members}
                g.config["entry_ids"] = [m for m in g.member_ids if m not in inner_targets]
            if "exit_ids" not in g.config:
                inner_sources = {t.from_id for t in transitions if t.from_id in members and t.to_id in members}
                g.config["exit_ids"] = [m for m in g.member_ids if m not in inner_sources]

    # ---- synthesised parallel branches
    def _synthesise_parallel(self, transitions: list[Transition]) -> None:
        succ: dict[str, list[str]] = {}
        for t in transitions:
            succ.setdefault(t.from_id, []).append(t.to_id)
        container = {**{a.id: a.group_id for a in self.activities.values()},
                     **{g.id: self.node_ctx[g.id][0] for g in self.groups.values()}}
        # outer forks first (they reach more nodes), so nested forks nest inside them
        forks = sorted(succ, key=lambda f: -len(self._bfs(f, succ, stop=None, avoid="")))
        for fork in forks:
            heads = [t.to_id for t in transitions if t.from_id == fork and t.kind == "success"]
            if len(heads) < 2 or any(container.get(h) != container.get(fork) for h in heads):
                continue
            dists = [self._bfs(h, succ, stop=None, avoid=fork) for h in heads]
            common = set(dists[0]).intersection(*dists[1:])
            if not common:
                continue
            join = min(common, key=lambda n: (max(d[n] for d in dists), n))
            branches = [list(self._bfs(h, succ, stop=join, avoid=fork)) for h in heads]
            flat = [n for b in branches for n in b]
            if len(flat) != len(set(flat)) or any(container.get(n, "?") != container.get(fork) for n in flat):
                continue
            owner = {n: i for i, b in enumerate(branches) for n in b}
            if any(t.from_id in owner and t.to_id in owner and owner[t.from_id] != owner[t.to_id]
                   for t in transitions):
                continue
            parent = container.get(fork)
            gid = self._unique(f"parallel:{fork}", "parallel")
            group = Group(id=gid, kind="parallel", member_ids=flat, config={
                "synthesised": True, "fork": fork, "join": join, "branches": branches, "source_line": None,
                "entry_ids": heads, "exit_ids": [t.from_id for t in transitions if t.to_id == join
                                                 and t.from_id in owner],
            })
            if parent:
                group.config["parent_group"] = parent
                pg = self.groups[parent]
                at = min(pg.member_ids.index(n) for n in flat)
                pg.member_ids = [m for m in pg.member_ids if m not in owner]
                pg.member_ids.insert(at, gid)
            for n in flat:
                container[n] = gid
                if n in self.activities:
                    self.activities[n].group_id = gid
            container[gid] = parent
            self.groups[gid] = group
            self.node_ctx[gid] = (parent, None, None)

    @staticmethod
    def _bfs(start: str, succ: dict[str, list[str]], stop: str | None, avoid: str) -> dict[str, int]:
        dist = {start: 0}
        queue = deque([start])
        while queue:
            n = queue.popleft()
            for m in succ.get(n, []):
                if m in dist or m == avoid or m == stop:
                    continue
                dist[m] = dist[n] + 1
                queue.append(m)
        return dist

    # ---- starter
    def _starter_config(self, kind: str | None, act: Activity) -> dict[str, Any]:
        c = act.config
        if kind == "jms":
            keys = ("destination", "selector", "messaging_style", "message_type", "resource_ref")
            return {k: c[k] for k in keys if k in c}
        if kind == "timer":
            out: dict[str, Any] = {k: c[k] for k in ("start_time", "interval_unit", "time_interval", "run_once")
                                   if k in c}
            m = re.search(r"T(\d{2}:\d{2})", str(c.get("start_time", "")))
            if m:
                out["start"] = m.group(1)
            if "time_interval" in c and "interval_unit" in c:
                out["interval"] = f"{c['time_interval']} {c['interval_unit']}"
            return out
        if kind in ("rest", "soap", "subprocess") and "partner_link" in c:
            keys = ("verb", "path", "operation", "partner_link", "port_type", "binding", "connector",
                    "service_descriptor")
            return {k: c[k] for k in keys if k in c}
        if kind == "http":
            keys = ("resource_property", "resource_ref")
            out = {k: c[k] for k in keys if k in c}
            out.update({k: v for k, v in c.get("settings", {}).items() if k in ("contextPath", "endpointUri")})
            return out
        return {}

    def _starter(self) -> Starter | None:
        if not self.starter_candidates:
            return None
        aid, kind, config, el = self.starter_candidates[0]
        if kind is None:
            act = self.activities[aid]
            if act.type != "unknown":
                self._unknown_attr(el, f"event source {act.native_type} ({act.type}) has no IR starter kind")
            return None
        return Starter(activity_id=aid, kind=kind, config=config)


def parse_bwp(path: Path, module: str, application: str, collector: CompletenessCollector) -> Process:
    """Parse one BW6 ``.bwp`` into a Process, recording completeness facts in ``collector``.

    A file that cannot be read or is not a BW6 process is recorded as a failure
    and the error is re-raised.
    """
    path = Path(path)
    collector.file_found(str(path))
    try:
        raw = path.read_bytes()
        root = etree.fromstring(normalize_bytes(raw), etree.XMLParser(
            resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True))
        proc = _BwpParser(path, module, application, collector, root, raw).parse()
    except Exception as exc:
        collector.failure(str(path), f"{type(exc).__name__}: {exc}")
        raise
    collector.file_parsed(str(path))
    return proc
