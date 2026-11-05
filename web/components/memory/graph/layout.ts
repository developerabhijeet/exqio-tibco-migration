// The memory subgraph as a React Flow scene. `buildModel` (synchronous) decides what is drawn:
// edges to nodes outside the set are dropped, a node with no edge in a focused neighbourhood links
// back to the focus with a dashed "filtered path", and in a neighbourhood each process (or class)
// becomes a container holding its activities (or methods) and the knowledge about only them.
// `layoutModel` (async, elkjs) places it: containers are packed as grids, then the top level is
// laid out layered, left to right.
import type { ElkNode } from "elkjs/lib/elk-api";
import type { Edge, Node } from "@xyflow/react";
import { displayName, type EdgeOut, type GraphMode, type MemNodeOut, type Side, type Subgraph } from "@/lib/memory";
import { LABEL_ORDER } from "./kinds";

export const CARD = { w: 216, h: 58 };
export const CHIP = { w: 216, h: 46 };
const GAP = 12;
const PAD = 14;
const HEADER = 62;
const SECTION = 26;

export interface ModelEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  status: string;
  /** Both ends in one container: drawn only when an end is picked or hovered. */
  internal: boolean;
  indirect: boolean;
}

export interface Model {
  nodes: MemNodeOut[];
  byId: Map<string, MemNodeOut>;
  edges: ModelEdge[];
  /** Member (activity, method) to its container. */
  parentOf: Map<string, string>;
  /** Knowledge about only one container's nodes, drawn inside it. */
  knowledgeOf: Map<string, string>;
  containers: Map<string, { members: string[]; knowledge: string[] }>;
  adjacency: Map<string, Set<string>>;
  nodeCount: number;
  dropped: number;
  indirect: number;
}

const CONTAINER_LABELS = new Set(["Process", "Class"]);
const MEMBER_LABELS = new Set(["Activity", "Method"]);

const rank = (label: string) => {
  const i = LABEL_ORDER.indexOf(label);
  return i < 0 ? LABEL_ORDER.length : i;
};
const byKind = (a: MemNodeOut, b: MemNodeOut) =>
  rank(a.label) - rank(b.label) || displayName(a).localeCompare(displayName(b));

export function buildModel(graph: Subgraph, mode: GraphMode, focus?: string | null): Model {
  const byId = new Map(graph.nodes.map((n) => [n.uid, n]));
  const kept: EdgeOut[] = [];
  let dropped = 0;
  for (const e of graph.edges) {
    if (!byId.has(e.src) || !byId.has(e.dst) || e.src === e.dst) {
      if (e.src !== e.dst) dropped += 1;
      continue;
    }
    kept.push(e);
  }

  const parentOf = new Map<string, string>();
  const knowledgeOf = new Map<string, string>();
  const containers = new Map<string, { members: string[]; knowledge: string[] }>();
  if (mode === "neighbourhood") {
    for (const e of kept) {
      if (e.type !== "CONTAINS") continue;
      const p = byId.get(e.src)!;
      const c = byId.get(e.dst)!;
      if (!CONTAINER_LABELS.has(p.label) || !MEMBER_LABELS.has(c.label) || parentOf.has(c.uid)) continue;
      parentOf.set(c.uid, p.uid);
    }
    for (const [c, p] of parentOf) {
      if (!containers.has(p)) containers.set(p, { members: [], knowledge: [] });
      containers.get(p)!.members.push(c);
    }
    const groupOf = (uid: string) => (containers.has(uid) ? uid : parentOf.get(uid));
    const neighbours = new Map<string, string[]>();
    for (const e of kept) {
      neighbours.set(e.src, [...(neighbours.get(e.src) ?? []), e.dst]);
      neighbours.set(e.dst, [...(neighbours.get(e.dst) ?? []), e.src]);
    }
    for (const n of graph.nodes) {
      if (n.side !== "knowledge" || n.uid === focus || containers.has(n.uid) || parentOf.has(n.uid)) continue;
      const ns = neighbours.get(n.uid) ?? [];
      if (!ns.length) continue;
      const g = groupOf(ns[0]);
      if (g && ns.every((x) => groupOf(x) === g)) {
        knowledgeOf.set(n.uid, g);
        containers.get(g)!.knowledge.push(n.uid);
      }
    }
    const sortIds = (ids: string[]) => ids.sort((a, b) => byKind(byId.get(a)!, byId.get(b)!));
    for (const c of containers.values()) {
      sortIds(c.members);
      sortIds(c.knowledge);
    }
  }
  const groupOf = (uid: string) => (containers.has(uid) ? uid : (parentOf.get(uid) ?? knowledgeOf.get(uid)));

  const edges: ModelEdge[] = kept.map((e) => {
    const gs = groupOf(e.src);
    return {
      id: e.uid,
      source: e.src,
      target: e.dst,
      type: e.type,
      status: e.status ?? "",
      internal: gs !== undefined && gs === groupOf(e.dst),
      indirect: false,
    };
  });
  const linked = new Set(kept.flatMap((e) => [e.src, e.dst]));
  let indirect = 0;
  if (mode === "neighbourhood" && focus && byId.has(focus)) {
    for (const n of graph.nodes) {
      if (n.uid === focus || linked.has(n.uid)) continue;
      indirect += 1;
      edges.push({
        id: `indirect:${focus}->${n.uid}`,
        source: focus,
        target: n.uid,
        type: "filtered path",
        status: "",
        internal: false,
        indirect: true,
      });
    }
  }
  const adjacency = new Map<string, Set<string>>();
  for (const e of edges) {
    if (!adjacency.has(e.source)) adjacency.set(e.source, new Set());
    if (!adjacency.has(e.target)) adjacency.set(e.target, new Set());
    adjacency.get(e.source)!.add(e.target);
    adjacency.get(e.target)!.add(e.source);
  }
  return {
    nodes: graph.nodes,
    byId,
    edges,
    parentOf,
    knowledgeOf,
    containers,
    adjacency,
    nodeCount: graph.nodes.length,
    dropped,
    indirect,
  };
}

export interface MemNodeData extends Record<string, unknown> {
  node: MemNodeOut;
  isFocus: boolean;
  compact: boolean;
  /** Containers: how many members and knowledge items they hold. */
  members?: number;
  knowledge?: number;
  /** Containers: where the knowledge section starts (px from the top), when it has one. */
  knowledgeTop?: number;
}

export type MemFlowNode = Node<MemNodeData, "entity" | "container">;

export interface MemEdgeData extends Record<string, unknown> {
  type: string;
  status: string;
  internal: boolean;
  indirect: boolean;
}

export type MemFlowEdge = Edge<MemEdgeData, "mem">;

const PARTITION: Record<Side, number> = { old: 0, new: 1, knowledge: 2 };

let elkInstance: Promise<{ layout: (g: ElkNode) => Promise<ElkNode> }> | null = null;
function elk() {
  elkInstance ??= import("elkjs/lib/elk.bundled.js").then(({ default: ELK }) => new ELK());
  return elkInstance;
}

/** A grid of `n` cells, `cols` wide: the offsets of each cell and the block's size. */
function grid(n: number, cols: number, cell: { w: number; h: number }) {
  const rows = Math.ceil(n / cols);
  return {
    at: (i: number) => ({ x: (i % cols) * (cell.w + GAP), y: Math.floor(i / cols) * (cell.h + GAP) }),
    w: cols * cell.w + (cols - 1) * GAP,
    h: rows ? rows * cell.h + (rows - 1) * GAP : 0,
  };
}

export async function layoutModel(
  model: Model,
  mode: GraphMode,
  focus?: string | null,
): Promise<{ nodes: MemFlowNode[]; edges: MemFlowEdge[] }> {
  // Containers first: their size comes from the grids inside them.
  const inner = new Map<string, { x: number; y: number }>();
  const boxes = new Map<string, { w: number; h: number; knowledgeTop?: number }>();
  for (const [id, c] of model.containers) {
    const cols = Math.max(1, Math.min(3, Math.ceil(Math.sqrt(c.members.length * 1.4)), Math.max(c.members.length, c.knowledge.length)));
    const m = grid(c.members.length, cols, CARD);
    const k = grid(c.knowledge.length, cols, CHIP);
    c.members.forEach((uid, i) => {
      const p = m.at(i);
      inner.set(uid, { x: PAD + p.x, y: HEADER + p.y });
    });
    const kTop = HEADER + (m.h ? m.h + GAP + 6 : 0);
    c.knowledge.forEach((uid, i) => {
      const p = k.at(i);
      inner.set(uid, { x: PAD + p.x, y: kTop + SECTION + p.y });
    });
    boxes.set(id, {
      w: Math.max(PAD * 2 + Math.max(m.w, k.w), CARD.w + PAD * 2),
      h: kTop + (k.h ? SECTION + k.h : 0) + PAD,
      knowledgeTop: c.knowledge.length ? kTop : undefined,
    });
  }

  const nested = (uid: string) => model.parentOf.has(uid) || model.knowledgeOf.has(uid);
  const top = model.nodes.filter((n) => !nested(n.uid)).sort(byKind);
  const rep = (uid: string) => model.parentOf.get(uid) ?? model.knowledgeOf.get(uid) ?? uid;
  const seen = new Set<string>();
  const elkEdges = [];
  for (const e of model.edges) {
    const s = rep(e.source);
    const t = rep(e.target);
    if (s === t || seen.has(`${s}>${t}`)) continue;
    seen.add(`${s}>${t}`);
    elkEdges.push({ id: `l:${s}>${t}`, sources: [s], targets: [t] });
  }
  const root: ElkNode = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "26",
      "elk.layered.spacing.nodeNodeBetweenLayers": "96",
      "elk.layered.spacing.edgeNodeBetweenLayers": "28",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      "elk.separateConnectedComponents": "true",
      "elk.spacing.componentComponent": "48",
      "elk.aspectRatio": "1.8",
      ...(mode === "lineage" ? { "elk.partitioning.activate": "true" } : {}),
    },
    children: top.map((n) => {
      const b = boxes.get(n.uid);
      return {
        id: n.uid,
        width: b?.w ?? CARD.w,
        height: b?.h ?? CARD.h,
        ...(mode === "lineage" ? { layoutOptions: { "elk.partitioning.partition": String(PARTITION[n.side] ?? 0) } } : {}),
      };
    }),
    edges: elkEdges,
  };
  const laid = await (await elk()).layout(root);
  const pos = new Map((laid.children ?? []).map((c) => [c.id, { x: c.x ?? 0, y: c.y ?? 0 }]));

  const nodes: MemFlowNode[] = [];
  for (const n of top) {
    const c = model.containers.get(n.uid);
    const b = boxes.get(n.uid);
    nodes.push({
      id: n.uid,
      type: c ? "container" : "entity",
      position: pos.get(n.uid) ?? { x: 0, y: 0 },
      width: b?.w ?? CARD.w,
      height: b?.h ?? CARD.h,
      data: {
        node: n,
        isFocus: n.uid === focus,
        compact: false,
        members: c?.members.length,
        knowledge: c?.knowledge.length,
        knowledgeTop: b?.knowledgeTop,
      },
    });
    if (!c) continue;
    for (const uid of [...c.members, ...c.knowledge]) {
      const m = model.byId.get(uid)!;
      nodes.push({
        id: uid,
        type: "entity",
        parentId: n.uid,
        extent: "parent",
        position: inner.get(uid) ?? { x: PAD, y: HEADER },
        width: model.knowledgeOf.has(uid) ? CHIP.w : CARD.w,
        height: model.knowledgeOf.has(uid) ? CHIP.h : CARD.h,
        data: { node: m, isFocus: uid === focus, compact: model.knowledgeOf.has(uid) },
      });
    }
  }
  const edges: MemFlowEdge[] = model.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    type: "mem",
    data: { type: e.type, status: e.status, internal: e.internal, indirect: e.indirect },
  }));
  return { nodes, edges };
}
