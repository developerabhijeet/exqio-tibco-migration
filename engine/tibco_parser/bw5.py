"""BW5 ``.process`` parser (``pd:ProcessDefinition``) to the neutral IR.

Completeness contract (design spec 4.1), as for BW6: every element of the
process tree is consumed into the IR, explained through ``collector.explain``
(known and intentionally not modelled, with a reason) or reported through
``collector.unknown``. A final sweep over the whole tree enforces this.

Dialect notes (verified against samples/synthetic-gmf/LegacyAddress and the
public BW5 projects under samples/public):

* ``pd:starter`` holds the process starter (an event source with ``pd:type``).
  A process without one is a sub-process: ``pd:startName`` names a Start node
  that has no element of its own; it becomes a ``noop`` activity with
  ``config["role"] == "start"`` and a ``subprocess`` starter (the BW6
  convention). ``pd:startType`` is that node's output schema.
* ``pd:endName`` names the End node; it becomes an ``end`` activity (native
  ``pd:end``) whose input bindings come from ``pd:returnBindings`` and whose
  output schema is ``pd:endType``.
* ``pd:activity`` carries ``pd:type`` (the native type), ``pd:resourceType``,
  a namespace-less ``config`` element (kept losslessly in ``config["settings"]``
  with well-known values lifted to top-level keys) and ``pd:inputBindings``
  (literal result elements plus XSLT instructions, parsed as an XSLT template).
* ``pd:transition`` has no name; its id is ``"<from> -> <to>"`` (the form the
  estate oracle uses). ``pd:conditionType`` is ``always`` (success), ``xpath``
  (conditional, expression in ``pd:xpath``), ``otherwise`` or ``error``. An
  ``always`` transition stays ``success`` even beside conditional siblings: in
  BW5 it fires regardless of them (unlike the BW6 unconditioned link).
* ``pd:group`` nests ``pd:activity``, ``pd:group`` and ``pd:transition``; the
  kind comes from ``config/pd:groupType`` (``catalogue.group_kind``). Inside a
  group, ``start`` and ``end`` are the group's own entry and exit pseudo-nodes:
  transitions from ``start`` become ``config["entry_ids"]`` and transitions into
  ``end`` become ``config["exit_ids"]`` (as for BW6 group markers). Transitions
  outside a group point at the group id.
* A Catch activity (``pd:handler`` true) starts a fault handler; the handler's
  activities are the nodes reachable from it that the normal flow does not reach.
* ``%%VAR%%`` global variable references are kept verbatim.
* The process id is the path relative to the project root without ``.process``
  with ``/`` replaced by ``.``; Call Process targets (``/Folder/Name.process``)
  use the same form, so they match the called process id.
"""
from __future__ import annotations

import copy
import hashlib
import re
from collections import deque
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
from engine.tibco_parser.bw6 import xslt_bindings
from engine.tibco_parser.completeness import CompletenessCollector
from engine.tibco_parser.contracts import _contract_id
from engine.tibco_parser.resources import _bw5_id
from engine.tibco_parser.xmlutil import NS, local, normalize_bytes

PD = NS["pd"]
XSL = NS["xsl"]
XSD = NS["xsd"]
WSDL = NS["wsdl"]
_PREFIXES = {PD: "pd", XSL: "xsl", XSD: "xsd", WSDL: "wsdl"}

# Diagram layout elements: consumed as a group and explained once per process.
_LAYOUT = {
    "pd:x", "pd:y", "pd:startX", "pd:startY", "pd:endX", "pd:endY", "pd:width", "pd:height",
    "pd:collapsedWidth", "pd:collapsedHeight", "pd:expanded", "pd:lineType", "pd:lineColor",
}
EXPLAINED: dict[str, str] = {
    "pd:description": "process description; documentation only",
    "pd:targetNamespace": "namespace for the schemas Studio generates for this process; no runtime behaviour",
    "pd:errorSchemas": "error schema declarations used by Generate Error at design time",
    "pd:label": "Studio sticky note; documentation only",
    "xsd:import": "schema namespace import; schema files are parsed as contracts (recorded in contracts_used)",
    "wsdl:import": "WSDL import; WSDL files are parsed as contracts (recorded in contracts_used)",
    "pd:xpathDescription": "Studio label of the adjacent pd:xpath transition condition; documentation only",
}
_STARTER_KIND = {
    "rest.receive": "rest", "http.receive": "http", "soap.receive": "soap",
    "jms.receive": "jms", "timer.start": "timer",
}
_CONDITION_KIND = {"always": "success", "xpath": "conditional", "otherwise": "otherwise", "error": "error"}
_SECRET = re.compile(r"password|credential|secret", re.IGNORECASE)
_SCHEMA_REF = re.compile(r"^/.+\.(xsd|wsdl)$", re.IGNORECASE)
_FAULT_LOCALNAME = re.compile(r"localname=(\S+)")
# An XPath 1.0 string literal: no escapes exist, so the delimiter cannot occur inside.
_XPATH_LITERAL = re.compile(r"'([^']*)'" + r'|"([^"]*)"')


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


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _clark(el, qname: str | None) -> str | None:
    if not qname:
        return None
    prefix, _, name = qname.rpartition(":")
    ns = (el.nsmap or {}).get(prefix or None)
    return f"{{{ns}}}{name}" if ns else qname


def _raw(el) -> Any:
    """Lossless dict form of a BW5 config element: one key per child (a list when
    repeated), ``@attr`` for attributes, ``#text`` for mixed text.

    Text values are stripped of outer whitespace only; inner whitespace and
    newlines (e.g. a multi-line SQL ``statement``) are kept verbatim.
    """
    attrs = {f"@{local(k)}": v for k, v in el.attrib.items()}
    kids: dict[str, Any] = {}
    for c in _children(el):
        name = local(c.tag)
        val = _raw(c)
        if name in kids:
            if not isinstance(kids[name], list):
                kids[name] = [kids[name]]
            kids[name].append(val)
        else:
            kids[name] = val
    text = (el.text or "").strip()
    if not attrs and not kids:
        return text
    out: dict[str, Any] = {**attrs, **kids}
    if text:
        out["#text"] = text
    return out


def _mask(value: Any, masked: list[str], prefix: str = "") -> None:
    if isinstance(value, dict):
        for k, v in list(value.items()):
            if isinstance(v, str) and v and _SECRET.search(k):
                value[k] = "***"
                masked.append(prefix + k)
            else:
                _mask(v, masked, f"{prefix}{k}.")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _mask(item, masked, f"{prefix.rstrip('.')}[{i}].")


def _get(d: Any, *path: str) -> Any:
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _as_list(v: Any) -> list:
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


def _strings(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [s for x in v.values() for s in _strings(x)]
    if isinstance(v, list):
        return [s for x in v for s in _strings(x)]
    return []


def process_id(rel_path: str) -> str:
    """``Folder/Name.process`` (or ``/Folder/Name.process``) -> ``Folder.Name``."""
    rel = rel_path.strip().lstrip("/")
    if rel.lower().endswith(".process"):
        rel = rel[: -len(".process")]
    return rel.replace("/", ".")


def find_project_root(path: Path) -> Path:
    """Nearest ancestor holding ``vcrepo.dat`` or ``defaultVars/`` (the BW5 project root)."""
    for anc in Path(path).parents:
        if (anc / "vcrepo.dat").is_file() or (anc / "defaultVars").is_dir():
            return anc
    return Path(path).parent


# ---------------------------------------------------------------------- parser
class _Bw5Parser:
    def __init__(self, path: Path, project: str, collector: CompletenessCollector, root, raw: bytes,
                 project_root: Path) -> None:
        self.path = path
        self.spath = str(path)
        self.project = project
        self.collector = collector
        self.root = root
        self.raw = raw
        self.project_root = project_root
        self.done: set = set()
        self.subtree: set = set()
        self.unknowns: list[UnknownElement] = []
        self.layout: dict[str, int] = {}
        self.activities: dict[str, Activity] = {}
        self.groups: dict[str, Group] = {}
        self.ids: set[str] = set()
        self.pending: list[tuple[Any, str | None]] = []  # (pd:transition, enclosing group id)
        self.variables: list[Variable] = []
        self.calls: list[ProcessCall] = []
        self.contracts: list[str] = []
        try:
            rel = path.relative_to(project_root).as_posix()
        except ValueError:
            rel = path.name
        self.pid = process_id(rel)
        self.package, _, self.name = self.pid.rpartition(".")

    # ---- bookkeeping (same scheme as the BW6 parser)
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
        item = self.collector.unknown(self._xpath(el), _qname(el) or "?", f"line {el.sourceline}: {reason}")
        self.unknowns.append(item)
        self.subtree.add(el)

    def _unknown_attr(self, el, reason: str) -> None:
        """An element that was consumed but whose value the parser cannot model."""
        item = self.collector.unknown(self._xpath(el), _qname(el) or "?", f"line {el.sourceline}: {reason}")
        self.unknowns.append(item)

    def _layout(self, el) -> None:
        self.subtree.add(el)
        q = _qname(el)
        self.layout[q] = self.layout.get(q, 0) + 1

    def _sweep(self, el) -> None:
        if el in self.subtree:
            return
        if el not in self.done:
            self._unknown(el, "element not consumed by the BW5 parser")
            return
        for c in _children(el):
            self._sweep(c)

    def _unique(self, wanted: str | None, fallback: str) -> str:
        base = wanted or fallback
        out, n = base, 1
        while out in self.ids:
            n += 1
            out = f"{base}~{n}"
        self.ids.add(out)
        return out

    def _contract_ref(self, location: str | None) -> None:
        if not location:
            return
        loc = location.strip()
        if loc.startswith("/"):
            target = self.project_root / loc.lstrip("/")
        elif "://" in loc:
            return
        else:
            target = self.path.parent / loc
        self.contracts.append(_contract_id(target))

    # ---- process level
    def parse(self) -> Process:
        root = self.root
        if _qname(root) != "pd:ProcessDefinition":
            raise ValueError(f"not a BW5 process: root element is {_qname(root)}")
        self.done.add(root)
        top: dict[str, list] = {}
        for c in _children(root):
            q = _qname(c)
            top.setdefault(q, []).append(c)
            if q in _LAYOUT:
                self._layout(c)
            elif q in ("pd:name", "pd:startName", "pd:endName"):
                self.subtree.add(c)  # identity and node names, read below
            elif q in ("xsd:import", "wsdl:import"):
                self._explain(c)
                self._contract_ref(c.get("schemaLocation") or c.get("location"))
            elif q in EXPLAINED:
                self._explain(c)
            elif q == "pd:processVariables":
                self._variables(c, "process")

        start_name = _text(next(iter(top.get("pd:startName", [])), None)) or "Start"
        starter_els = top.get("pd:starter", [])
        starter: Starter | None = None
        if starter_els:
            starter = self._starter(starter_els[0])
            first = next(a for a in self.activities.values())
            for st in top.get("pd:startType", []):  # unusual beside a starter; its output schema
                self.subtree.add(st)
                first.output_schema = first.output_schema or self._schema_of(st)
            for extra in starter_els[1:]:
                self._unknown(extra, "a second pd:starter; a BW5 process has one starter")
        else:
            starter = self._start_node(start_name, top)

        for c in _children(root):
            q = _qname(c)
            if q == "pd:activity":
                self._activity(c, None)
            elif q == "pd:group":
                self._group(c, None)
            elif q == "pd:transition":
                self.pending.append((c, None))

        end_names = top.get("pd:endName", [])
        if end_names:
            self._end_node(end_names[0], top)
        else:
            for c in top.get("pd:returnBindings", []) + top.get("pd:endType", []):
                self._unknown(c, "end bindings or schema without a pd:endName")

        transitions = self._transitions()
        self._finish_groups(transitions)
        fault_handlers = self._fault_handlers(transitions)
        if self.layout:
            counts = ", ".join(f"{k} x{v}" for k, v in sorted(self.layout.items()))
            self.collector.explain(f"{self.spath}#layout", f"diagram layout only ({counts}); no runtime meaning")
        self._sweep(root)
        proc = Process(
            id=self.pid, application=self.project, module=self.project, package=self.package,
            name=self.name, dialect="bw5", source_path=self.spath, starter=starter,
            activities=list(self.activities.values()), transitions=transitions,
            groups=list(self.groups.values()), variables=self.variables, fault_handlers=fault_handlers,
            calls=self.calls, unknown_elements=list(self.unknowns),
            contracts_used=sorted(set(self.contracts)), raw_xml_sha256=hashlib.sha256(self.raw).hexdigest(),
        )
        proc.complexity = score(proc)
        return proc

    def _schema_of(self, holder) -> str | None:
        """Schema of a variable, start or end type holder: a QName ref/type, else the inline XSD."""
        kids = _children(holder)
        if holder.get("ref"):
            return _clark(holder, holder.get("ref"))
        if len(kids) == 1 and not _children(kids[0]):
            k = kids[0]
            if k.get("ref"):
                return _clark(k, k.get("ref"))
            if k.get("type") and local(k.tag) == "element":
                return _clark(k, k.get("type"))
        if not kids:
            return None
        return "".join(re.sub(r'\s+xmlns(:\w+)?="[^"]*"', "", etree.tostring(k, encoding="unicode",
                                                                          with_tail=False)) for k in kids)

    def _variables(self, el, scope: str) -> None:
        self.subtree.add(el)
        for v in _children(el):
            self.variables.append(Variable(name=local(v.tag), schema=self._schema_of(v), scope=scope))

    # ---- start and end nodes
    def _start_node(self, start_name: str, top: dict[str, list]) -> Starter:
        names = top.get("pd:startName", [])
        line = names[0].sourceline if names else None
        aid = self._unique(start_name, "Start")
        schema = None
        for st in top.get("pd:startType", []):
            self.subtree.add(st)
            schema = self._schema_of(st)
        self.activities[aid] = Activity(id=aid, name=start_name, type="noop", native_type="pd:start",
                                        config={"role": "start"}, output_schema=schema, source_line=line)
        return Starter(activity_id=aid, kind="subprocess", config={})

    def _end_node(self, end_el, top: dict[str, list]) -> None:
        name = _text(end_el) or "End"
        aid = self._unique(name, "End")
        act = Activity(id=aid, name=name, type=catalogue.canonical("pd:end") or "end", native_type="pd:end",
                       config={}, source_line=end_el.sourceline)
        for et in top.get("pd:endType", []):
            self.subtree.add(et)
            act.output_schema = self._schema_of(et)
        for rb in top.get("pd:returnBindings", []):
            self.subtree.add(rb)
            self._bindings(rb, act)
        self.activities[aid] = act

    def _starter(self, el) -> Starter | None:
        act = self._activity(el, None)
        kind = _STARTER_KIND.get(act.type)
        if kind is None:
            if act.type != "unknown":
                self._unknown_attr(el, f"event source {act.native_type} ({act.type}) has no IR starter kind")
            return None
        return Starter(activity_id=act.id, kind=kind, config=self._starter_config(kind, act))

    @staticmethod
    def _starter_config(kind: str, act: Activity) -> dict[str, Any]:
        c = act.config
        keys = {
            "http": ("resource_path", "resource_ref", "parameters"),
            "soap": ("resource_path", "resource_ref", "service", "operation", "soap_action"),
            "rest": ("resource_path", "resource_ref", "verb", "path"),
            "jms": ("destination", "selector", "messaging_style", "message_type", "resource_path", "resource_ref"),
            "timer": ("start_time", "time_interval", "interval_unit", "run_once"),
        }[kind]
        out = {k: c[k] for k in keys if k in c}
        if kind == "timer" and "time_interval" in c and "interval_unit" in c:
            out["interval"] = f"{c['time_interval']} {c['interval_unit']}"
        return out

    # ---- activities
    def _activity(self, el, group: str | None) -> Activity:
        self.done.add(el)
        native = ""
        config: dict[str, Any] = {}
        settings: dict[str, Any] = {}
        cfg_el = None
        bindings = []
        for c in _children(el):
            q = _qname(c)
            if q == "pd:type":
                self.subtree.add(c)
                native = _text(c)
            elif q == "pd:resourceType":
                self.subtree.add(c)
                config["resource_type"] = _text(c)
            elif q in _LAYOUT:
                self._layout(c)
            elif q == "config":
                self.subtree.add(c)
                cfg_el = c
                raw = _raw(c)
                settings = raw if isinstance(raw, dict) else ({"#text": raw} if raw else {})
            elif q == "pd:inputBindings":
                self.subtree.add(c)
                bindings.append(c)
            elif q == "pd:coercions":
                self.subtree.add(c)
                config["coercions"] = [{"xpath": k.get("xpath"), "element": k.get("element")}
                                       for k in _children(c)]
                for k in _children(c):
                    if _qname(k) != "pd:coercion":
                        self._unknown_attr(k, "unexpected element in pd:coercions")
            elif q == "pd:description":
                self.subtree.add(c)
                config["description"] = _text(c)
            elif q == "pd:handler":
                self.subtree.add(c)
                config["handler"] = _text(c).lower() == "true"
            # anything else stays unconsumed and is reported by the sweep
        masked: list[str] = []
        _mask(settings, masked)
        config["settings"] = settings
        if masked:
            config["masked_fields"] = masked
        canon = catalogue.canonical(native) if native else None
        if canon is None:
            self.collector.unknown_type(self.pid, native or "pd:activity")
            type_ = "unknown"
        else:
            type_ = canon
        output_schema = self._lift(type_, native, settings, config, cfg_el)
        name = el.get("name")
        aid = self._unique(name, f"activity@{el.sourceline}")
        act = Activity(id=aid, name=name or aid, type=type_, native_type=native or "pd:activity", config=config,
                       output_schema=output_schema, source_line=el.sourceline, group_id=group)
        self.activities[aid] = act
        if group:
            self.groups[group].member_ids.append(aid)
        for b in bindings:
            self._bindings(b, act)
        if type_.startswith("jdbc.") and not config.get("sql"):
            self._lift_bound_sql(act)
        if type_ == "call.process" and config.get("sub_process") and not config.get("dynamic"):
            self.calls.append(ProcessCall(activity_id=aid, target_process=config["sub_process"]))
        for s in _strings(settings):
            if _SCHEMA_REF.match(s.strip()):
                self._contract_ref(s)
        return act

    def _lift(self, canon: str, native: str, s: dict[str, Any], config: dict[str, Any], cfg_el) -> str | None:
        """Lift the well-known config values to top-level keys; returns the output schema if declared."""

        def put(key: str, *path: str) -> None:
            v = _get(s, *path)
            if isinstance(v, str) and v != "":
                config[key] = v

        output_schema = None
        family = canon.split(".", 1)[0]
        if family == "jdbc":
            put("sql", "statement")
            put("resource_path", "jdbcSharedConfig")
            if "resource_path" not in config:
                put("resource_path", "sharedConfig")
            put("max_rows", "maxRows")
            put("timeout", "timeout")
            params = _as_list(_get(s, "Prepared_Param_DataType", "parameter"))
            config["parameters"] = [{"name": _get(p, "parameterName"), "type": _get(p, "dataType")}
                                    for p in params if isinstance(p, dict)]
        elif family == "jms":
            put("destination", "SessionAttributes", "destination")
            put("selector", "SessionAttributes", "selector")
            if "selector" not in config:
                put("selector", "selector")
            put("message_type", "PermittedMessageType")
            put("resource_path", "ConnectionReference")
            put("event_source", "ConfigurableHeaders", "EventSource")
            config["messaging_style"] = "Topic" if "topic" in native.lower() else "Queue"
        elif canon in ("http.receive", "soap.receive"):
            put("resource_path", "sharedChannel")
            put("service", "service")
            put("operation", "operation")
            put("soap_action", "soapAction")
            if canon == "http.receive":
                config["parameters"] = [
                    {"name": _get(p, "name"), "data_type": _get(p, "dataType"), "repeating": _get(p, "repeating")}
                    for p in _as_list(_get(s, "parameters", "parameter")) if isinstance(p, dict)
                ]
        elif canon == "http.invoke":
            put("resource_path", "sharedHttpClient")
            put("host", "serverhost")
            put("port", "serverport")
        elif canon == "soap.invoke":
            put("endpoint_url", "endpointURL")
            put("service", "service")
            put("operation", "operation")
            put("soap_action", "soapAction")
            put("timeout", "timeout")
        elif canon == "rest.invoke":
            put("verb", "restMethodUI")
            put("url", "restURI")
            put("event_source", "eventSource")
        elif canon == "timer.start":
            put("start_time", "StartTime")
            put("time_interval", "TimeInterval")
            put("interval_unit", "FrequencyIndex")
            freq = _get(s, "Frequency")
            if isinstance(freq, str) and freq:
                config["run_once"] = freq.lower() != "true"
        elif canon == "log":
            put("level", "role")
        elif canon == "java.invoke":
            pkg, cls = _get(s, "packageName"), _get(s, "fileName")
            if isinstance(cls, str) and cls:
                config["class_name"] = f"{pkg}.{cls}" if isinstance(pkg, str) and pkg else cls
            put("class_name", "MethodInfo", "classLocation")
            put("method_name", "MethodInfo", "methodName")
        elif canon == "call.process":
            put("process_name", "processName")
            if config.get("process_name"):
                config["sub_process"] = process_id(config["process_name"])
            put("process_name_xpath", "processNameXPath")
            config["dynamic"] = bool(config.get("process_name_xpath"))
        elif canon == "throw":
            put("fault_name", "faultName")
        elif canon == "catch":
            config["catch_all"] = str(_get(s, "catchAll")).lower() == "true"
            fault = _get(s, "fault")
            if isinstance(fault, str) and fault:
                m = _FAULT_LOCALNAME.search(fault)
                config["fault_name"] = m.group(1) if m else fault.rsplit(":", 1)[-1]
        elif canon in ("shared.get", "shared.set"):
            put("variable_ref", "variableConfig")
        elif canon == "reply":
            put("event_source", "eventSource")
        elif canon == "xslt.transform":
            put("stylesheet", "stylesheet")
        elif canon == "assign":
            put("variable", "variableName")
        elif canon == "mapper" and cfg_el is not None:
            el = next((c for c in _children(cfg_el) if local(c.tag) == "element"), None)
            if el is not None and el.get("ref"):
                output_schema = _clark(el, el.get("ref"))
        rp = config.get("resource_path")
        if rp and rp.startswith("/"):
            config["resource_ref"] = _bw5_id(self.project_root / rp.lstrip("/"), self.project_root)
        return output_schema

    @staticmethod
    def _lift_bound_sql(act: Activity) -> None:
        """JDBC General (and any JDBC activity without a configured statement) takes its SQL
        from the ``.../statement`` input binding: a plain XPath string literal becomes
        ``config["sql"]``; any other expression goes to ``config["sql_expression"]`` with
        ``config["sql"]`` left None."""
        binding = next((b for b in act.input_bindings
                        if b.target == "statement" or b.target.endswith("/statement")), None)
        if binding is None:
            return
        m = _XPATH_LITERAL.fullmatch(binding.expression.strip())
        if m:
            act.config["sql"] = m.group(1) if m.group(1) is not None else m.group(2)
        else:
            act.config["sql"] = None
            act.config["sql_expression"] = binding.expression

    # ---- bindings
    def _bindings(self, el, act: Activity) -> None:
        kids = _children(el)
        if not kids:
            text = (el.text or "").strip()
            if text:
                act.input_bindings.append(InputBinding(target=".", expression=text, language="xpath",
                                                       source_line=el.sourceline))
            return
        nsmap = {k: v for k, v in (el.nsmap or {}).items() if k}
        nsmap["xsl"] = XSL
        sheet = etree.Element(f"{{{XSL}}}stylesheet", nsmap=nsmap, version="1.0")
        tpl = etree.SubElement(sheet, f"{{{XSL}}}template", match="/")
        for c in el:
            tpl.append(copy.deepcopy(c))
        text = etree.tostring(sheet, encoding="unicode")
        act.config.setdefault("input_xslt", []).append(text)
        try:
            act.input_bindings.extend(xslt_bindings(text, el.sourceline))
        except etree.XMLSyntaxError as exc:  # pragma: no cover - rebuilt from a parsed tree
            self._unknown_attr(el, f"input binding XSLT does not parse: {exc}")

    # ---- groups
    def _group(self, el, parent: str | None) -> None:
        self.done.add(el)
        gid = self._unique(el.get("name"), f"group@{el.sourceline}")
        group = Group(id=gid, kind="scope", config={"source_line": el.sourceline})
        self.groups[gid] = group
        if parent:
            self.groups[parent].member_ids.append(gid)
            group.config["parent_group"] = parent
        native = ""
        group_type = None
        cfg_el = None
        for c in _children(el):
            q = _qname(c)
            if q == "pd:type":
                self.subtree.add(c)
                native = _text(c)
                group.config["native_type"] = native
            elif q == "pd:resourceType":
                self.subtree.add(c)
                group.config["resource_type"] = _text(c)
            elif q in _LAYOUT:
                self._layout(c)
            elif q == "pd:groupVariables":
                self._variables(c, "scope")
            elif q == "pd:joinToggle":
                self.subtree.add(c)
                group.config["join_toggle"] = _text(c)
            elif q == "config":
                self.subtree.add(c)
                cfg_el = c
                for k in _children(c):
                    key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", local(k.tag)).lower()
                    group.config[key] = _raw(k)
                group_type = group.config.get("group_type")
            elif q == "pd:inputBindings":
                self.subtree.add(c)
                if _children(c):
                    holder = Activity(id=gid, name=gid, type="noop", native_type="pd:group")
                    self._bindings(c, holder)
                    group.config["input_xslt"] = holder.config.get("input_xslt", [])
            elif q == "pd:activity":
                self._activity(c, gid)
            elif q == "pd:group":
                self._group(c, gid)
            elif q == "pd:transition":
                self.pending.append((c, gid))
        kind = catalogue.group_kind(group_type) if isinstance(group_type, str) and group_type else None
        kind = kind or (catalogue.group_kind(native) if native else None)
        if kind is None:
            self._unknown_attr(cfg_el if cfg_el is not None else el,
                               f"group type {group_type or native!r} has no IR group kind; modelled as scope")
            kind = "scope"
        group.kind = kind

    # ---- transitions
    def _transitions(self) -> list[Transition]:
        out: list[Transition] = []
        tids: set[str] = set()
        nodes = set(self.activities) | set(self.groups)
        for el, container in self.pending:
            self.done.add(el)
            fields: dict[str, Any] = {}
            line = xpath_line = el.sourceline
            for c in _children(el):
                q = _qname(c)
                if q in ("pd:from", "pd:to", "pd:conditionType", "pd:xpath"):
                    self.subtree.add(c)
                    fields[q] = c.text or ""
                    if q == "pd:xpath":
                        xpath_line = c.sourceline
                elif q in _LAYOUT:
                    self._layout(c)
                elif q == "pd:xpathDescription":
                    self._explain(c)
            src = fields.get("pd:from", "").strip()
            tgt = fields.get("pd:to", "").strip()
            ctype = fields.get("pd:conditionType", "always").strip() or "always"
            kind = _CONDITION_KIND.get(ctype)
            if kind is None:
                self._unknown_attr(el, f"pd:conditionType={ctype!r} is not a known condition type")
                kind = "success"
            expr = fields.get("pd:xpath", "").strip() or None
            if kind == "conditional" and not expr:
                self._unknown_attr(el, "xpath transition without a pd:xpath condition")
            if kind == "conditional" and expr:
                line = xpath_line  # where the condition lives (as the BW6 parser uses bpws:source)
            else:
                expr = None
            if container is not None:
                group = self.groups[container]
                if src == "start" and src not in nodes:
                    group.config.setdefault("entry_ids", []).append(tgt)
                    if kind != "success":
                        group.config.setdefault("entry_conditions", {})[tgt] = {"kind": kind, "expression": expr}
                    if tgt not in nodes and tgt != "end":
                        self._unknown_attr(el, f"transition target {tgt!r} is not a node of the process")
                    continue
                if tgt == "end" and tgt not in nodes:
                    group.config.setdefault("exit_ids", []).append(src)
                    if kind != "success":
                        group.config.setdefault("exit_conditions", {})[src] = {"kind": kind, "expression": expr}
                    if src not in nodes:
                        self._unknown_attr(el, f"transition source {src!r} is not a node of the process")
                    continue
            missing = [n for n in (src, tgt) if n not in nodes]
            if missing:
                self._unknown_attr(el, f"transition endpoint(s) {missing} are not nodes of the process")
                continue
            base = f"{src} -> {tgt}"
            tid, n = base, 1
            while tid in tids:
                n += 1
                tid = f"{base}~{n}"
            tids.add(tid)
            out.append(Transition(id=tid, from_id=src, to_id=tgt, kind=kind, expression=expr, source_line=line))
        return out

    def _finish_groups(self, transitions: list[Transition]) -> None:
        for g in self.groups.values():
            members = set(g.member_ids)
            inner = [t for t in transitions if t.from_id in members and t.to_id in members]
            if "entry_ids" not in g.config:
                targets = {t.to_id for t in inner}
                g.config["entry_ids"] = [m for m in g.member_ids if m not in targets]
            if "exit_ids" not in g.config:
                sources = {t.from_id for t in inner}
                g.config["exit_ids"] = [m for m in g.member_ids if m not in sources]

    # ---- fault handlers
    def _fault_handlers(self, transitions: list[Transition]) -> list[FaultHandler]:
        catches = [a for a in self.activities.values() if a.type == "catch"]
        if not catches:
            return []
        succ: dict[str, list[str]] = {}
        incoming: set[str] = set()
        for t in transitions:
            succ.setdefault(t.from_id, []).append(t.to_id)
            incoming.add(t.to_id)
        for g in self.groups.values():  # entering a group enters its entry nodes
            succ.setdefault(g.id, []).extend(g.config.get("entry_ids", []))
        catch_ids = {c.id for c in catches}
        roots = [n for n in list(self.activities) + list(self.groups) if n not in incoming and n not in catch_ids]
        normal = self._reach(roots, succ, avoid=catch_ids)
        out: list[FaultHandler] = []
        for c in catches:
            body = [n for n in self._reach([c.id], succ, avoid=set()) if n not in normal]
            ids: list[str] = []
            for n in body:
                ids.extend(self._activities_of(n))
            fault = c.config.get("fault_name") if not c.config.get("catch_all") else None
            fid = self._unique(f"catch:{fault}" if fault else "catchAll", "catch")
            out.append(FaultHandler(id=fid, fault_name=fault, activity_ids=list(dict.fromkeys(ids))))
        return out

    def _activities_of(self, node: str) -> list[str]:
        if node in self.activities:
            return [node]
        return [a for m in self.groups[node].member_ids for a in self._activities_of(m)]

    @staticmethod
    def _reach(starts: list[str], succ: dict[str, list[str]], avoid: set[str]) -> list[str]:
        seen: dict[str, None] = {}
        queue = deque(s for s in starts if s not in avoid)
        while queue:
            n = queue.popleft()
            if n in seen:
                continue
            seen[n] = None
            queue.extend(m for m in succ.get(n, []) if m not in seen and m not in avoid)
        return list(seen)


def parse_process(path: Path, project: str, collector: CompletenessCollector,
                  project_root: Path | None = None) -> Process:
    """Parse one BW5 ``.process`` into a Process, recording completeness facts in ``collector``.

    ``project`` is the project (Application) name; ``project_root`` is the project
    folder the process id and resource paths are relative to (default: the nearest
    ancestor holding ``vcrepo.dat`` or ``defaultVars/``). A file that cannot be
    read or is not a BW5 process is recorded as a failure and the error re-raised.
    """
    path = Path(path)
    collector.file_found(str(path))
    try:
        raw = path.read_bytes()
        root = etree.fromstring(normalize_bytes(raw), etree.XMLParser(
            resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True))
        proc = _Bw5Parser(path, project, collector, root, raw,
                          Path(project_root) if project_root else find_project_root(path)).parse()
    except Exception as exc:
        collector.failure(str(path), f"{type(exc).__name__}: {exc}")
        raise
    collector.file_parsed(str(path))
    return proc
