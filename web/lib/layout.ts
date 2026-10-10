// ELK layered layout (left to right) of a process canvas into React Flow nodes and edges.
// Groups (scope, loops, synthesised parallel blocks) and fault handlers become dashed parent
// nodes; ELK positions children relative to their parent, which is what React Flow expects.
import type { ElkExtendedEdge, ElkNode } from "elkjs/lib/elk-api";
import { MarkerType, type Edge, type Node } from "@xyflow/react";
import { terminalKind } from "./activity";
import type { ProcessCanvasData } from "./types";
import { EDGE_COLORS, type FlowEdgeData } from "@/components/canvas/FlowEdge";

// elkjs is large; it loads with the first canvas, not with the page.
let elkInstance: Promise<{ layout: (g: ElkNode) => Promise<ElkNode> }> | null = null;
function elk() {
  elkInstance ??= import("elkjs/lib/elk.bundled.js").then(({ default: ELK }) => new ELK());
  return elkInstance;
}

const SIZE = { activity: { w: 230, h: 84 }, terminal: { w: 120, h: 40 } };

const ROOT_OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.hierarchyHandling": "INCLUDE_CHILDREN",
  "elk.spacing.nodeNode": "28",
  "elk.layered.spacing.nodeNodeBetweenLayers": "100",
  "elk.layered.spacing.edgeNodeBetweenLayers": "30",
  "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
  "elk.separateConnectedComponents": "true",
  "elk.spacing.componentComponent": "60",
};

const CONTAINER_OPTIONS = { "elk.padding": "[top=40,left=20,bottom=20,right=20]" };

export async function layoutCanvas(canvas: ProcessCanvasData): Promise<{ nodes: Node[]; edges: Edge<FlowEdgeData>[] }> {
  const parentOf = new Map<string, string>();
  const containers = new Map<string, { label: string; detail?: string; fault?: boolean }>();

  for (const g of canvas.groups) {
    containers.set(g.id, { label: g.synthesised ? "parallel" : g.kind, detail: g.id });
    for (const m of g.member_ids) parentOf.set(m, g.id);
  }
  for (const n of canvas.nodes) if (n.data.group_id && !parentOf.has(n.id)) parentOf.set(n.id, n.data.group_id);
  for (const fh of canvas.fault_handlers) {
    containers.set(fh.id, { label: "catch", detail: fh.fault_name ?? "all faults", fault: true });
    for (const a of fh.activity_ids) if (!parentOf.has(a)) parentOf.set(a, fh.id);
  }
  // A member id that names no node or container is dropped from the hierarchy.
  const known = new Set([...canvas.nodes.map((n) => n.id), ...containers.keys()]);
  for (const [child, parent] of [...parentOf]) if (!known.has(parent) || child === parent) parentOf.delete(child);

  const elkNodes = new Map<string, ElkNode>();
  for (const id of containers.keys()) elkNodes.set(id, { id, layoutOptions: CONTAINER_OPTIONS, children: [] });
  for (const n of canvas.nodes) {
    const t = terminalKind(n.id, n.data, canvas.starter_id);
    const s = t ? SIZE.terminal : SIZE.activity;
    elkNodes.set(n.id, { id: n.id, width: s.w, height: s.h });
  }
  const root: ElkNode = { id: "root", layoutOptions: ROOT_OPTIONS, children: [], edges: [] };
  for (const [id, node] of elkNodes) {
    const parent = parentOf.get(id);
    (parent ? elkNodes.get(parent)! : root).children!.push(node);
  }
  const edgeIds = new Set<string>();
  root.edges = canvas.edges
    .filter((e) => elkNodes.has(e.source) && elkNodes.has(e.target))
    .map((e): ElkExtendedEdge => {
      edgeIds.add(e.id);
      return { id: e.id, sources: [e.source], targets: [e.target] };
    });

  const laid = await (await elk()).layout(root);

  const nodes: Node[] = [];
  const byId = new Map(canvas.nodes.map((n) => [n.id, n]));
  const walk = (n: ElkNode, parent?: string) => {
    for (const c of n.children ?? []) {
      const base = { id: c.id, position: { x: c.x ?? 0, y: c.y ?? 0 }, ...(parent ? { parentId: parent, extent: "parent" as const } : {}) };
      const container = containers.get(c.id);
      if (container) {
        nodes.push({
          ...base,
          type: "container",
          data: container,
          style: { width: c.width, height: c.height },
          selectable: false,
          zIndex: -1,
        });
        walk(c, c.id);
        continue;
      }
      const src = byId.get(c.id)!;
      const t = terminalKind(c.id, src.data, canvas.starter_id);
      if (t) {
        nodes.push({
          ...base,
          type: "terminal",
          data: { label: t === "start" ? "Start" : "End", kind: t, native_type: src.data.native_type },
        });
      } else {
        nodes.push({
          ...base,
          type: "activity",
          data: { ...src.data, isStarter: c.id === canvas.starter_id || src.data.starter },
        });
      }
    }
  };
  walk(laid);

  const edges: Edge<FlowEdgeData>[] = canvas.edges
    .filter((e) => edgeIds.has(e.id))
    .map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: "flow",
      zIndex: 1,
      data: { kind: e.kind, expression: e.expression, label: e.label },
      markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: EDGE_COLORS[e.kind] ?? EDGE_COLORS.success },
    }));
  return { nodes, edges };
}
