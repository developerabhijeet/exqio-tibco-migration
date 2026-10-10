"""GMF notation diagram for a .bwp, in the shape BusinessWorks Studio 6.5 writes.

The structure mirrors the public TN2018 LookupDatabase.bwp (process 2001 > 3004 > scope
4018 > 3018 > 4020 > 3020 > flow 4005 > 3007 > activity 4002 nodes, 4006 edges addressed by
@children index paths), MainProcess.bwp (pick 4010 / onMessage 4011), the CreditCheck
Process.bwp (fault handlers 4022 / 4029) and the tibcobwblog iterate group (group 4018 with
GroupStart/GroupEnd 4002 nodes beside the inner 4020). Layout is left to right: 180 px
between activity columns, 120 px between rows.
"""
from __future__ import annotations

from lxml import etree

from .bwlib import N

B = "http://docs.oasis-open.org/wsbpel/2.0/process/executable"
T = "http://www.tibco.com/bpel/2007/extensions"
BWEXT = "http://tns.tibco.com/bw/model/core/bwext"
FONT = "Segoe UI"
ACTIVITIES = {"assign", "empty", "exit", "extensionActivity", "flow", "forEach", "invoke",
              "pick", "receive", "reply", "scope", "throw"}
COL, ROW, BOX = 180, 120, 40


def _acts(el):
    return [c for c in el if isinstance(c.tag, str) and c.tag.startswith(f"{{{B}}}")
            and etree.QName(c).localname in ACTIVITIES]


def _unwrap(el):
    return el[0] if el.tag == f"{{{B}}}extensionActivity" else el


def _local(el):
    return etree.QName(el).localname


def _shape():
    return N("styles", {"fontName": FONT, "lineColor": "0", "xsi:type": "notation:ShapeStyle"})


def _background():
    return N("styles", {"backgroundColor": "16777215", "gradientEndColor": "50431",
                        "gradientStartColor": "16777215",
                        "xsi:type": "bwnotation:BackgroundStyle"})


def _style(kind):
    return N("styles", {"xsi:type": kind})


def _bounds(**kw):
    return N("layoutConstraint", {**{k: str(v) for k, v in kw.items()},
                                  "xsi:type": "notation:Bounds"})


def _el(href):
    return N("element", {"href": href})


def _type_of(act):
    cfg = act.find(f"{{{T}}}config/{{{BWEXT}}}BWActivity")
    if cfg is None:
        cfg = act.find(f"{{{T}}}eventSource/{{{BWEXT}}}BWActivity")
    type_id = cfg.get("activityTypeID") if cfg is not None else None
    if type_id and type_id != "bw.internal.end":
        return f"4002 {type_id}"
    return "4002"


class Diagram:
    def __init__(self, root):
        self.root = root
        self.node_of = {}      # BPEL element -> diagram N node (4002 / 4018)
        self.flows = []        # (flow element, flow href)

    # -- activity nodes -----------------------------------------------------------------
    def box(self, act, href, x, y):
        node = N("children", {"type": _type_of(act)}, [
            *[N("children", {"type": "4017"}, [_shape(), _bounds()]) for _ in range(4)],
            _shape(), _background(), _el(href),
            _bounds(height=BOX, width=BOX, x=x, y=y)])
        self.node_of[act] = node
        return node

    # -- flows ----------------------------------------------------------------------------
    def flow(self, flow, href, skip=()):
        """4005 node for a flow; returns (node, width, height)."""
        self.flows.append((flow, href))
        members = [(i, _unwrap(a)) for i, a in enumerate(_acts(flow))]
        shown = [(i, a) for i, a in members if a not in skip]
        preds = {a: [] for _i, a in shown}
        for _i, a in shown:
            for tgt in a.findall(f"{{{B}}}targets/{{{B}}}target"):
                src = self.root.find(f".//{{{B}}}source[@linkName='{tgt.get('linkName')}']")
                owner = src.getparent().getparent()
                if owner in preds:
                    preds[a].append(owner)
        depth = {}

        def col(a):
            if a not in depth:
                depth[a] = 0
                depth[a] = 1 + max((col(p) for p in preds[a]), default=-1)
            return depth[a]

        sized = []
        for i, a in shown:
            if _local(a) == "scope" and a.get(f"{{{T}}}group"):
                sized.append((i, a, *self.group_size(a, f"{href}/@activities.{i}")))
            elif _local(a) == "pick":
                sized.append((i, a, *self.pick_size(a, f"{href}/@activities.{i}")))
            else:
                sized.append((i, a, BOX, BOX))
        columns = {}
        for entry in sized:
            columns.setdefault(col(entry[1]), []).append(entry)
        kids, x, max_y = [], 20, 0
        for c in sorted(columns):
            y, widest = 20, 0
            for i, a, w, h in columns[c]:
                child_href = f"{href}/@activities.{i}"
                if _local(a) == "scope" and a.get(f"{{{T}}}group"):
                    kids.append(self.group(a, child_href, x, y))
                elif _local(a) == "pick":
                    kids.append(self.pick(a, child_href, x, y))
                else:
                    kids.append(self.box(a, child_href, x, y))
                y += h + ROW - BOX
                widest = max(widest, w)
            max_y = max(max_y, y)
            x += widest + COL - BOX
        width, height = x + 20, max_y + 20
        node = N("children", {"type": "4005"}, [
            N("children", {"type": "3007"}, [
                *kids, _style("notation:DrawerStyle"), _style("notation:SortingStyle"),
                _style("notation:FilteringStyle"), _el(href)]),
            _shape(), _background(), _style("bwnotation:ResizingStyle"), _el(href),
            _bounds(height=height, width=width)])
        return node, width, height

    # -- scopes and groups ----------------------------------------------------------------
    def scope(self, scope, href, *, x=None, y=None, skip=(), extra=None, type_="4018"):
        flow = _acts(scope)[0]
        inner, w, h = self.flow(flow, f"{href}/@activity", skip=skip)
        handlers, hw, hh = self.fault_handlers(scope, href)
        container = N("children", {"type": "4020"}, [
            N("children", {"type": "3020"}, [inner, _style("notation:SortingStyle"),
                                              _style("notation:FilteringStyle")]),
            _shape(), _style("bwnotation:BackgroundStyle"), _style("bwnotation:ResizingStyle"),
            _el(href), _bounds(height=h, width=w, x=30, y=20)])
        width = max(w, hw) + 60
        height = h + hh + 60
        pos = {} if x is None else {"x": x, "y": y}
        node = N("children", {"type": type_}, [
            N("children", {"type": "3018"}, [
                container, *(extra or []), *([handlers] if handlers is not None else []),
                _style("notation:SortingStyle"), _style("notation:FilteringStyle"), _el(href)]),
            _shape(), _background(), _style("bwnotation:ResizingStyle"), _el(href),
            _bounds(height=height, width=width, **pos)])
        self.node_of[scope] = node
        return node, width, height

    def fault_handlers(self, scope, href):
        fh = scope.find(f"{{{B}}}faultHandlers")
        if fh is None:
            return None, 0, 0
        kids, y, widest = [], 0, 0
        handlers = [(f"@catch.{i}", c) for i, c in enumerate(fh.findall(f"{{{B}}}catch"))]
        if fh.find(f"{{{B}}}catchAll") is not None:
            handlers.append(("@catchAll", fh.find(f"{{{B}}}catchAll")))
        for seg, handler in handlers:
            h_href = f"{href}/@faultHandlers/{seg}"
            body, w, h = self.flow(_acts(_acts(handler)[0])[0], f"{h_href}/@activity/@activity")
            kids.append(N("children", {"type": "4029"}, [
                N("children", {"type": "3031"}, [
                    N("children", {"type": "4033"}, [_shape(), _el(h_href),
                                                     _bounds(height=34, width=34, y=68)])]),
                N("children", {"type": "3032"}, [body]),
                _shape(), _el(h_href), _bounds(height=h + 20, width=w + 60, y=y)]))
            y += h + 40
            widest = max(widest, w + 60)
        node = N("children", {"type": "4022"}, [
            N("children", {"type": "3022"}, kids), _shape(), _el(f"{href}/@faultHandlers"),
            _bounds(height=y + 20, width=widest + 20)])
        return node, widest + 20, y + 20

    def _group_inner(self, group, href):
        """Inner flow and its href (iterate: scope > forEach > scope > flow)."""
        inner, inner_href = group, href
        while _local(inner) != "flow":
            inner = _acts(inner)[0]
            inner_href += "/@activity"
        return inner, inner_href

    def _edges_of_group(self, group):
        inner, _ = self._group_inner(group, "")
        return [_unwrap(a) for a in _acts(inner) if _unwrap(a).get(f"{{{T}}}group")
                in ("groupStart", "groupEnd")]

    def group_size(self, group, href):
        probe = Diagram(self.root)
        _node, w, h = probe.group(group, href, 0, 0, measure=True)
        return w, h

    def group(self, group, href, x, y, measure=False):
        inner, inner_href = self._group_inner(group, href)
        ends = self._edges_of_group(group)
        body, w, h = self.flow(inner, inner_href, skip=ends)
        width, height = w + 140, h + 60
        idx = {_unwrap(a): i for i, a in enumerate(_acts(inner))}
        markers = []
        for a in ends:
            mx = 2 if a.get(f"{{{T}}}group") == "groupStart" else width - 42
            markers.append(self.box(a, f"{inner_href}/@activities.{idx[a]}", mx, height // 2 - 20))
        container = N("children", {"type": "4020"}, [
            N("children", {"type": "3020"}, [body, _style("notation:SortingStyle"),
                                              _style("notation:FilteringStyle")]),
            _shape(), _style("bwnotation:BackgroundStyle"), _style("bwnotation:ResizingStyle"),
            _el(href), _bounds(height=h, width=w, x=70, y=20)])
        node = N("children", {"type": "4018"}, [
            N("children", {"type": "3018"}, [
                container, *markers, _style("notation:SortingStyle"),
                _style("notation:FilteringStyle"), _el(href)]),
            _shape(), _background(), _style("bwnotation:ResizingStyle"), _el(href),
            _bounds(height=height, width=width, x=x, y=y)])
        self.node_of[group] = node
        if measure:
            return node, width, height
        return node

    # -- pick / onMessage -------------------------------------------------------------------
    def pick_size(self, pick, href):
        probe = Diagram(self.root)
        _node, w, h = probe.pick(pick, href, 0, 0, measure=True)
        return w, h

    def pick(self, pick, href, x, y, measure=False):
        msg = pick.find(f"{{{B}}}onMessage")
        m_href = f"{href}/@messages.0"
        scope = _acts(msg)[0]
        flow = _acts(scope)[0]
        members = [_unwrap(a) for a in _acts(flow)]
        start = next(a for a in members if a.get(f"{{{T}}}constructor") == "onMessageStart")
        end = next(a for a in members if a.get(f"{{{T}}}constructor") == "onMessageEnd")
        f_href = f"{m_href}/@activity/@activity"
        start_node = self.box(start, f"{f_href}/@activities.{members.index(start)}", 0, 40)
        end_node = self.box(end, f"{f_href}/@activities.{members.index(end)}", 0, 40)
        scope_node, w, h = self.scope(scope, f"{m_href}/@activity", skip=(start, end))
        width, height = w + 120, h + 40
        node = N("children", {"type": "4010"}, [
            N("children", {"type": "5002"}),
            N("children", {"type": "3010"}, [
                N("children", {"type": "4013"}, [_shape(), _el(href),
                                                 _bounds(height=34, width=34, x=9, y=55)])]),
            N("children", {"type": "3011"}, [
                N("children", {"type": "4011"}, [
                    N("children", {"type": "3012"}, [start_node]),
                    N("children", {"type": "3013"}, [scope_node]),
                    N("children", {"type": "3016"}, [end_node]),
                    _shape(), _el(m_href), _bounds(height=h + 20, width=w + 60)])]),
            _shape(), _el(href), _bounds(height=height, width=width, x=x, y=y)])
        self.node_of[pick] = node
        if measure:
            return node, width, height
        return node

    # -- whole diagram ------------------------------------------------------------------------
    def build(self) -> N:
        top = _acts(self.root)[0]
        scope_node, w, h = self.scope(top, "//0/@process/@activity")
        process = N("children", {"type": "2001"}, [
            N("children", {"type": "5001"}),
            N("children", {"type": "3002"}, [_style("notation:SortingStyle"),
                                              _style("notation:FilteringStyle")]),
            N("children", {"type": "3003"}, [_style("notation:SortingStyle"),
                                              _style("notation:FilteringStyle")]),
            N("children", {"type": "3004"}, [scope_node, _style("notation:SortingStyle"),
                                              _style("notation:FilteringStyle"),
                                              _el("//0/@process")]),
            _shape(), _el("//0/@process"), _bounds(height=h + 120, width=w + 240)])
        paths = {}
        _index(process, "//@children.0", paths)
        edges = []
        for flow, href in self.flows:
            links = flow.findall(f"{{{B}}}links/{{{B}}}link")
            for k, link in enumerate(links):
                name = link.get("name")
                src = self.root.find(f".//{{{B}}}source[@linkName='{name}']").getparent().getparent()
                tgt = self.root.find(f".//{{{B}}}target[@linkName='{name}']").getparent().getparent()
                edges.append(N("edges", {
                    "source": paths[id(self.node_of[src])],
                    "target": paths[id(self.node_of[tgt])], "type": "4006"}, [
                    N("children", {"type": "6002"}, [
                        N("layoutConstraint", {"xsi:type": "notation:Location", "y": "40"})]),
                    N("styles", {"lineColor": "0", "xsi:type": "notation:ConnectorStyle"}),
                    N("styles", {"fontName": FONT, "xsi:type": "notation:FontStyle"}),
                    _el(f"{href}/@links/@children.{k}"),
                    N("bendpoints", {"points": "[0, 0, 0, 0]$[0, 0, 0, 0]",
                                     "xsi:type": "notation:RelativeBendpoints"})]))
        return N("notation:Diagram", {
            "measurementUnit": "Pixel", "type": "BWProcess",
            "xmlns:bwnotation": "http://tns.tibco.com/bw/runtime/BWNotation",
            "xmlns:notation": "http://www.eclipse.org/gmf/runtime/1.0.2/notation",
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"},
            [process, _style("notation:DiagramStyle"), _el("//0"), *edges])


def _index(node: N, path: str, paths: dict):
    paths[id(node)] = path
    kids = [c for c in node.children if isinstance(c, N) and c.tag == "children"]
    for i, child in enumerate(kids):
        _index(child, f"{path}/@children.{i}", paths)


def diagram_lines(process_xml: str, indent: int = 4) -> list[str]:
    root = etree.fromstring(process_xml.encode("utf-8"))
    return Diagram(root).build().lines(indent)
