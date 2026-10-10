"""Estate graph and per-workflow I/O card, built by code from the parsed model (tab 2, Discover).

The graph is kept at process granularity unless ``include_activities=True``.
Node ids: ``domain:<id>``, ``app:<id>``, ``process:<id>``, ``activity:<process>/<activity>``,
``table:<name>``, ``queue:<name>``, ``topic:<name>``, ``endpoint:<VERB path>``, ``resource:<id>``.
"""
from __future__ import annotations

import json
from typing import Any

from lxml import etree

from engine.domains.discover import (
    activity_messages,
    activity_sql,
    discover_domains,
    resolve_call,
    sql_tables,
)
from engine.domains.models import DomainCandidate, Edge, Graph, IOCard, Node
from engine.ir.models import Application, Contract, Process, Resource

_XS = "http://www.w3.org/2001/XMLSchema"


# ------------------------------------------------------------------ resources
def _find_resource(app: Application, ref: Any) -> Resource | None:
    if not isinstance(ref, str) or not ref:
        return None
    for r in app.resources:
        if r.id == ref:
            return r
    hits = [r for r in app.resources if r.id.endswith("." + ref) or r.id.endswith("/" + ref)]
    return hits[0] if len(hits) == 1 else None


def _process_resource_refs(p: Process) -> list[tuple[str | None, Any]]:
    """(activity id or None for the starter, reference) pairs."""
    refs: list[tuple[str | None, Any]] = []
    if p.starter:
        for key in ("resource_ref", "connector"):
            if p.starter.config.get(key):
                refs.append((None, p.starter.config[key]))
    for act in p.activities:
        for key in ("resource_ref", "connector"):
            if act.config.get(key):
                refs.append((act.id, act.config[key]))
    return refs


# ------------------------------------------------------------------- endpoint
def _endpoint(p: Process) -> tuple[str, dict[str, Any]] | None:
    st = p.starter
    if not st:
        return None
    c = st.config
    if st.kind == "rest" and c.get("path"):
        verb = str(c.get("verb") or "").upper()
        return f"{verb} {c['path']}".strip(), {"verb": verb, "path": c["path"], "protocol": "rest"}
    if st.kind == "http" and c.get("resource_path"):
        return f"HTTP {c['resource_path']}", {"path": c["resource_path"], "protocol": "http"}
    if st.kind == "soap":
        op = c.get("operation") or c.get("port_type") or p.name
        return f"SOAP {op}", {"operation": op, "protocol": "soap"}
    return None


# ---------------------------------------------------------------------- graph
class _Builder:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str, str], Edge] = {}

    def node(self, nid: str, kind: str, name: str, **attrs: Any) -> str:
        if nid not in self.nodes:
            self.nodes[nid] = Node(id=nid, kind=kind, name=name, attrs=dict(attrs))  # type: ignore[arg-type]
        else:
            for k, v in attrs.items():
                self.nodes[nid].attrs.setdefault(k, v)
        return nid

    def edge(self, src: str, dst: str, kind: str, activity: str | None = None, **attrs: Any) -> None:
        key = (src, dst, kind)
        e = self.edges.get(key)
        if e is None:
            e = self.edges[key] = Edge(src=src, dst=dst, kind=kind, attrs={})  # type: ignore[arg-type]
        if activity:
            acts = e.attrs.setdefault("activities", [])
            if activity not in acts:
                acts.append(activity)
        for k, v in attrs.items():
            vals = e.attrs.setdefault(k, [])
            if v not in vals:
                vals.append(v)


def build_graph(apps: list[Application], domains: list[DomainCandidate] | None = None,
                include_activities: bool = False) -> Graph:
    """Process-level estate graph: domains, applications, processes and what they touch."""
    b = _Builder()
    by_id = {p.id: p for a in apps for p in a.processes}
    if domains is None:
        domains = discover_domains(apps)

    for d in domains:
        b.node(f"domain:{d.id}", "domain", d.suggested_name, suggested_name=d.suggested_name,
               process_count=len(d.process_ids))
        for pid in d.process_ids:
            if pid in by_id:
                b.edge(f"domain:{d.id}", f"process:{pid}", "contains")

    for app in apps:
        aid = b.node(f"app:{app.id}", "application", app.name, dialect=app.dialect, modules=list(app.modules))
        for r in app.resources:
            b.node(f"resource:{r.id}", "resource", r.id, resource_kind=r.kind, application=app.id)
        for p in app.processes:
            pid = b.node(f"process:{p.id}", "process", p.id, application=app.id, module=p.module,
                         dialect=p.dialect, trigger=p.starter.kind if p.starter else None,
                         activity_count=len(p.activities))
            b.edge(aid, pid, "contains")

            ep = _endpoint(p)
            if ep:
                name, attrs = ep
                eid = b.node(f"endpoint:{name}", "endpoint", name, **attrs)
                b.edge(pid, eid, "exposes")

            for _, ref in _process_resource_refs(p):
                res = _find_resource(app, ref)
                if res:
                    b.edge(pid, f"resource:{res.id}", "uses")

            for act in p.activities:
                if include_activities:
                    act_id = b.node(f"activity:{p.id}/{act.id}", "activity", act.name, type=act.type,
                                    process=p.id)
                    b.edge(pid, act_id, "contains")
                sql = activity_sql(act)
                if sql:
                    for ref in sql_tables(sql):
                        attrs = {"schema": ref.schema} if ref.schema else {}
                        tid = b.node(f"table:{ref.name}", "table", ref.name, **attrs)
                        kind = "reads" if ref.operation == "read" else "writes"
                        b.edge(pid, tid, kind, activity=act.id, operations=ref.operation)
                for m in activity_messages(act):
                    nk = "topic" if m.style == "Topic" else "queue"
                    qid = b.node(f"{nk}:{m.destination}", nk, m.destination)
                    b.edge(pid, qid, "consumes" if m.direction == "consume" else "publishes", activity=act.id)

            st = p.starter
            if st and st.kind == "jms" and isinstance(st.config.get("destination"), str):
                style = "topic" if str(st.config.get("messaging_style", "")).lower() == "topic" else "queue"
                qid = b.node(f"{style}:{st.config['destination']}", style, st.config["destination"])
                b.edge(pid, qid, "consumes", activity=st.activity_id)

            for c in p.calls:
                target = resolve_call(c.target_process, by_id)
                if target:
                    b.edge(pid, f"process:{target}", "calls", activity=c.activity_id)
    return Graph(nodes=list(b.nodes.values()), edges=list(b.edges.values()))


# ------------------------------------------------------------ contract trees
def _leaf(name: str, typ: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "type": typ, "children": [], **extra}


def _json_type(schema: dict) -> str:
    fmt = schema.get("format")
    return str(fmt if fmt in ("date", "date-time") else schema.get("type") or "object")


def _json_tree(name: str, schema: dict, defs: dict, depth: int = 0, seen: tuple = ()) -> dict[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str):
        key = ref.rsplit("/", 1)[-1]
        if key in seen or key not in defs or depth > 12:
            return _leaf(name, key)
        return _json_tree(name, defs[key], defs, depth + 1, seen + (key,))
    typ = schema.get("type")
    if typ == "array":
        item = _json_tree(name, schema.get("items") or {}, defs, depth + 1, seen)
        item["repeated"] = True
        return item
    props = schema.get("properties")
    if isinstance(props, dict):
        children = [_json_tree(n, s if isinstance(s, dict) else {}, defs, depth + 1, seen)
                    for n, s in props.items()]
        required = set(schema.get("required") or [])
        for ch in children:
            if required:
                ch["required"] = ch["name"] in required
        return {"name": name, "type": "object", "children": children}
    return _leaf(name, _json_type(schema))


def _swagger_contracts(p: Process, app: Application) -> tuple[dict, dict] | None:
    st = p.starter
    if not st or st.kind != "rest":
        return None
    by_id = {c.id: c for c in app.contracts}
    verb, path = str(st.config.get("verb", "")).lower(), st.config.get("path")
    for cid in p.contracts_used:
        c = by_id.get(cid)
        if not c or c.kind != "json":
            continue
        try:
            doc = json.loads(c.text.lstrip("﻿"))
        except ValueError:
            continue
        op = (doc.get("paths") or {}).get(path, {}).get(verb)
        if not isinstance(op, dict):
            continue
        defs = doc.get("definitions") or {}
        request: dict[str, Any] = {}
        params = op.get("parameters") or []
        body = next((x for x in params if x.get("in") == "body"), None)
        if body:
            request = _json_tree(_ref_name(body.get("schema")) or "body", body.get("schema") or {}, defs)
        elif params:
            request = {"name": "parameters", "type": "object", "children": [
                _leaf(x["name"], str(x.get("type", "string")), location=x.get("in"),
                      required=bool(x.get("required"))) for x in params if x.get("name")]}
        response: dict[str, Any] = {}
        ok = next((v for k, v in sorted((op.get("responses") or {}).items()) if str(k).startswith("2")), None)
        if ok and isinstance(ok.get("schema"), dict):
            response = _json_tree(_ref_name(ok["schema"]) or "response", ok["schema"], defs)
        return request, response
    return None


def _ref_name(schema: Any) -> str | None:
    if isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
        return schema["$ref"].rsplit("/", 1)[-1]
    return None


class _XsdIndex:
    def __init__(self, contracts: list[Contract]):
        self.types: dict[str, etree._Element] = {}
        self.elements: dict[str, etree._Element] = {}
        for c in contracts:
            try:
                root = etree.fromstring(c.text.lstrip("﻿").encode("utf-8"))
            except (etree.XMLSyntaxError, ValueError):
                continue
            for child in root:
                if not isinstance(child.tag, str):
                    continue
                tag, name = etree.QName(child).localname, child.get("name")
                if not name:
                    continue
                if tag in ("complexType", "simpleType"):
                    self.types.setdefault(name, child)
                elif tag == "element":
                    self.elements.setdefault(name, child)

    def tree(self, element: etree._Element, depth: int = 0, seen: tuple = ()) -> dict[str, Any]:
        ref = element.get("ref")
        if ref:
            target = self.elements.get(ref.split(":")[-1])
            if target is not None and depth < 12:
                return self.tree(target, depth + 1, seen)
            return _leaf(ref.split(":")[-1], "unknown")
        name = element.get("name") or "element"
        typ = (element.get("type") or "").split(":")[-1]
        max_occurs = element.get("maxOccurs", "1")
        extra: dict[str, Any] = {"required": element.get("minOccurs", "1") != "0"}
        if max_occurs == "unbounded" or (max_occurs.isdigit() and int(max_occurs) > 1):
            extra["repeated"] = True
        inline = next((c for c in element if isinstance(c.tag, str)
                       and etree.QName(c).localname in ("complexType", "simpleType")), None)
        ctype = inline if inline is not None else (self.types.get(typ) if typ else None)
        if ctype is not None and etree.QName(ctype).localname == "complexType":
            if typ in seen or depth > 12:
                return {"name": name, "type": typ or "object", "children": [], **extra}
            return {"name": name, "type": "object",
                    "children": self._children(ctype, depth + 1, seen + ((typ,) if typ else ())), **extra}
        if ctype is not None:  # simpleType: report its restriction base
            restr = next((c for c in ctype.iter() if isinstance(c.tag, str)
                          and etree.QName(c).localname == "restriction"), None)
            typ = (restr.get("base") or typ).split(":")[-1] if restr is not None else typ
        return {"name": name, "type": typ or "string", "children": [], **extra}

    def _children(self, ctype: etree._Element, depth: int, seen: tuple) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for node in ctype:
            if not isinstance(node.tag, str):
                continue
            local = etree.QName(node).localname
            if local == "element":
                out.append(self.tree(node, depth, seen))
            elif local in ("sequence", "choice", "all", "group"):
                out.extend(self._children(node, depth, seen))
            elif local in ("complexContent", "simpleContent"):
                for ext in node:
                    if not isinstance(ext.tag, str) or etree.QName(ext).localname not in ("extension", "restriction"):
                        continue
                    base = (ext.get("base") or "").split(":")[-1]
                    if base in self.types and base not in seen and depth < 12:
                        out.extend(self._children(self.types[base], depth + 1, seen + (base,)))
                    out.extend(self._children(ext, depth, seen))
        return out


_RESPONSE_SUFFIX = ("Response", "Result", "Output")
_REQUEST_SUFFIX = ("Request", "Message", "Input")


def _xsd_contracts(p: Process, app: Application) -> tuple[dict, dict]:
    by_id = {c.id: c for c in app.contracts}
    used = [by_id[cid] for cid in p.contracts_used if cid in by_id and by_id[cid].kind == "xsd"]
    if not used:
        return {}, {}
    idx = _XsdIndex(used + [c for c in app.contracts if c.kind == "xsd" and c not in used])
    roots = [r for c in used for r in c.root_elements if not r.endswith("Fault")]
    kind = p.starter.kind if p.starter else "subprocess"
    req = next((r for r in roots if r.endswith(_REQUEST_SUFFIX)), None)
    if req is None and kind == "jms":
        req = next((r for r in roots if r.lower() in p.name.lower()), None) or (roots[0] if roots else None)
    resp = next((r for r in roots if r.endswith(_RESPONSE_SUFFIX) and r != req), None)
    if resp is None and kind in ("rest", "http", "soap"):
        resp = next((r for r in roots if r != req), None)

    def build(name: str | None) -> dict[str, Any]:
        el = idx.elements.get(name) if name else None
        return idx.tree(el) if el is not None else {}

    return build(req), build(resp)


def _starter_params_contract(p: Process) -> dict[str, Any]:
    params = (p.starter.config.get("parameters") if p.starter else None) or []
    if not isinstance(params, list) or not params:
        return {}
    return {"name": "parameters", "type": "object", "children": [
        _leaf(str(x.get("name")), str(x.get("data_type", "string")),
              required=str(x.get("repeating", "")).lower() == "required")
        for x in params if isinstance(x, dict) and x.get("name")]}


def _contracts(p: Process, app: Application) -> tuple[dict, dict]:
    swagger = _swagger_contracts(p, app)
    if swagger:
        return swagger
    req, resp = _xsd_contracts(p, app)
    if not req and p.starter and p.starter.kind in ("http", "soap"):
        req = _starter_params_contract(p)
    return req, resp


# ------------------------------------------------------------------ I/O card
def _trigger(p: Process) -> dict[str, Any]:
    st = p.starter
    if st is None:
        return {"kind": "subprocess"}
    c = st.config
    if st.kind == "rest":
        return {"kind": "rest", "verb": str(c.get("verb") or "").upper(), "path": c.get("path")}
    if st.kind == "jms":
        dest = c.get("destination")
        key = "topic" if str(c.get("messaging_style", "")).lower() == "topic" else "queue"
        out: dict[str, Any] = {"kind": "jms", "destination": dest, key: dest}
        if c.get("selector"):
            out["selector"] = c["selector"]
        return out
    if st.kind == "timer":
        out = {"kind": "timer"}
        for key in ("start", "interval", "start_time", "time_interval", "interval_unit", "run_once"):
            if c.get(key) is not None:
                out[key] = c[key]
        return out
    if st.kind == "http":
        return {"kind": "http", "resource": c.get("resource_path")}
    if st.kind == "soap":
        return {"kind": "soap", **{k: c[k] for k in ("operation", "port_type", "binding") if c.get(k)}}
    return {"kind": st.kind}


def _users_of(g: Graph, node_id: str, kinds: tuple[str, ...]) -> list[str]:
    return sorted({e.src[len("process:"):] for e in g.edges if e.dst == node_id and e.kind in kinds})


def io_card(process: Process, app: Application, graph: Graph) -> IOCard:
    """Trigger, request/response contract trees, side effects, faults and subprocesses of one workflow."""
    known = {n.id for n in graph.nodes}
    effects: list[dict[str, Any]] = []
    throws: list[str] = []
    starter_id = process.starter.activity_id if process.starter else None
    for act in process.activities:
        sql = activity_sql(act)
        if sql:
            for ref in sql_tables(sql):
                others = [x for x in _users_of(graph, f"table:{ref.name}", ("reads", "writes"))
                          if x != process.id]
                effects.append({"kind": "table_read" if ref.operation == "read" else "table_write",
                                "activity": act.id, "table": ref.name, "operation": ref.operation,
                                "sql": sql.strip(), "shared_with": others})
        for m in activity_messages(act):
            if m.direction == "publish":
                effects.append({"kind": "publish", "activity": act.id, "destination": m.destination,
                                "messaging_style": m.style})
            elif act.id != starter_id:
                effects.append({"kind": "consume", "activity": act.id, "destination": m.destination,
                                "messaging_style": m.style})
        if act.type in ("http.invoke", "rest.invoke", "soap.invoke"):
            res = _find_resource(app, act.config.get("resource_ref"))
            effects.append({"kind": "service_call", "activity": act.id, "type": act.type,
                            "resource": res.id if res else act.config.get("resource_ref"),
                            "protocol": res.kind if res else act.type.split(".")[0]})
        if act.type == "throw" and act.config.get("fault_name"):
            throws.append(str(act.config["fault_name"]))
    subprocesses: list[str] = []
    for c in process.calls:
        if c.target_process not in subprocesses:
            subprocesses.append(c.target_process)
        effects.append({"kind": "subprocess", "activity": c.activity_id, "process": c.target_process,
                        "resolved": f"process:{c.target_process}" in known})
    caught = [h.fault_name for h in process.fault_handlers if h.fault_name]
    faults = list(dict.fromkeys(throws + caught))
    request, response = _contracts(process, app)
    return IOCard(process_id=process.id, trigger=_trigger(process), request_contract=request,
                  response_contract=response, side_effects=effects, faults=faults, subprocesses=subprocesses)
