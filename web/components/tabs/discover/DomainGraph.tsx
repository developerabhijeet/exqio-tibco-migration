"use client";

// React Flow view of the estate graph for one domain: its processes, the tables, queues, topics
// and endpoints they touch, and the processes they call in other domains. Each node is a card
// (an icon tile, the name and what it is); elk lays the graph out layered, left to right, and the
// edges are smooth steps coloured by relation with a pill label. Clicking a process opens it.
import { createContext, memo, useContext, useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getSmoothStepPath,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { ElkNode } from "elkjs/lib/elk-api";
import { Database, ExternalLink, ListOrdered, Loader2, Plug, Radio, Workflow, type LucideIcon } from "lucide-react";
import type { EstateGraph, ProcessSummary } from "@/lib/types";
import { tok } from "@/components/memory/graph/kinds";

const SHOWN_EDGES = new Set(["calls", "reads", "writes", "publishes", "consumes", "exposes"]);

export interface GraphFilters {
  band: string;
  status: string;
}

export interface DomainNodeData extends Record<string, unknown> {
  label: string;
  kind: string;
  processId?: string;
  band?: string | null;
  steps?: number;
}

export type DomainElement =
  | { data: DomainNodeData & { id: string } }
  | { data: { id: string; source: string; target: string; label: string; kind: string } };

const isEdge = (e: DomainElement): e is Extract<DomainElement, { data: { source: string } }> => "source" in e.data;

export function graphElements(
  graph: EstateGraph | null,
  inDomain: ProcessSummary[],
  all: ProcessSummary[],
  filters: GraphFilters,
): DomainElement[] {
  const byIr = new Map(all.map((p) => [p.ir_id, p]));
  const keep = new Set(
    inDomain
      .filter((p) => (filters.band === "all" || p.complexity_band === filters.band) && (filters.status === "all" || p.migration_status === filters.status))
      .map((p) => `process:${p.ir_id}`),
  );
  const nodes = new Map<string, DomainElement>();
  const addProcess = (id: string, own: boolean) => {
    const p = byIr.get(id.replace(/^process:/, ""));
    nodes.set(id, {
      data: {
        id,
        label: p?.name ?? id.replace(/^process:/, ""),
        kind: own ? "process" : "process-ext",
        processId: p?.id ?? "",
        band: p?.complexity_band ?? null,
        steps: p?.activity_count,
      },
    });
  };
  keep.forEach((id) => addProcess(id, true));
  const edges: DomainElement[] = [];
  const edgeIds = new Set<string>();
  if (graph) {
    const meta = new Map(graph.nodes.map((n) => [n.id, n]));
    for (const e of graph.edges) {
      if (!SHOWN_EDGES.has(e.kind)) continue;
      if (!keep.has(e.src) && !keep.has(e.dst)) continue;
      for (const end of [e.src, e.dst]) {
        if (nodes.has(end)) continue;
        const n = meta.get(end);
        if (!n) continue;
        if (n.kind === "process") addProcess(end, false);
        else nodes.set(end, { data: { id: end, label: n.name, kind: n.kind } });
      }
      const id = `${e.src}->${e.dst}:${e.kind}`;
      if (!edgeIds.has(id)) {
        edgeIds.add(id);
        edges.push({ data: { id, source: e.src, target: e.dst, label: e.kind, kind: e.kind } });
      }
    }
  }
  return [...nodes.values(), ...edges];
}

// ---- look ---------------------------------------------------------------------------------------

const KIND: Record<string, { icon: LucideIcon; token: string; label: string }> = {
  process: { icon: Workflow, token: "accent", label: "Process" },
  "process-ext": { icon: ExternalLink, token: "accent-ink", label: "Other domain" },
  table: { icon: Database, token: "gate", label: "Table" },
  queue: { icon: ListOrdered, token: "coral-dk", label: "Queue" },
  topic: { icon: Radio, token: "coral-dk", label: "Topic" },
  endpoint: { icon: Plug, token: "ai", label: "Endpoint" },
};
const kindOf = (k: string) => KIND[k] ?? { icon: Database, token: "slate", label: k };

const REL: Record<string, string> = {
  calls: "accent",
  reads: "gate",
  writes: "coral-dk",
  publishes: "coral-dk",
  consumes: "gate",
  exposes: "ai",
};
const relColour = (k: string) => tok(REL[k] ?? "line-strong");

const BAND_CLS: Record<string, string> = {
  high: "bg-error-bg text-error",
  medium: "bg-decision-bg text-decision",
  low: "bg-sunken text-slate",
};

const SIZE = { process: { w: 300, h: 66 }, other: { w: 220, h: 52 } };
const sizeOf = (kind: string) => (kind === "process" ? SIZE.process : SIZE.other);

/** What the pointer or the selection lights: a node and its direct neighbours. */
const LitContext = createContext<{ nodes: Set<string> | null; edges: Set<string> | null }>({ nodes: null, edges: null });

type DomainFlowNode = Node<DomainNodeData, "domain">;
type DomainFlowEdge = Edge<{ kind: string }, "rel">;

const DomainNode = memo(function DomainNode({ id, data, selected }: NodeProps<DomainFlowNode>) {
  const lit = useContext(LitContext);
  const k = kindOf(data.kind);
  const Icon = k.icon;
  const own = data.kind === "process";
  const ext = data.kind === "process-ext";
  const dim = lit.nodes !== null && !lit.nodes.has(id);
  const { w, h } = sizeOf(data.kind);
  return (
    <div
      data-testid="domain-node"
      data-kind={data.kind}
      className={`group relative flex items-center gap-2.5 overflow-hidden rounded-xl border bg-surface pl-3 pr-3 shadow-e1 transition-all duration-150 ${
        own ? "cursor-pointer hover:-translate-y-px hover:shadow-e2" : ext ? "cursor-pointer border-dashed" : ""
      } ${selected ? "ring-2 ring-accent ring-offset-2 ring-offset-page" : ""}`}
      style={{
        width: w,
        height: h,
        borderColor: selected ? tok(k.token) : own ? tok(k.token, 0.35) : undefined,
        opacity: dim ? 0.3 : 1,
      }}
      title={data.label}
    >
      {own ? <span aria-hidden className="absolute inset-y-0 left-0 w-1" style={{ background: tok(k.token) }} /> : null}
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-0 !bg-transparent" />
      <span
        aria-hidden
        className={`flex shrink-0 items-center justify-center rounded-lg ${own ? "h-9 w-9" : "h-7 w-7"}`}
        style={{ background: tok(k.token, 0.13), color: tok(k.token) }}
      >
        <Icon size={own ? 18 : 14} strokeWidth={2.2} />
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span data-testid="domain-node-label" className={`truncate font-semibold text-ink ${own ? "text-sm" : "text-xs"}`}>
          {data.label}
        </span>
        <span className="flex items-center gap-1.5 whitespace-nowrap text-2xs text-slate">
          <span className="uppercase tracking-wide">{k.label}</span>
          {own && data.steps !== undefined ? <span>· {data.steps} steps</span> : null}
        </span>
      </span>
      {own && data.band ? (
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-2xs font-semibold ${BAND_CLS[data.band] ?? BAND_CLS.low}`}>{data.band}</span>
      ) : null}
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-0 !bg-transparent" />
    </div>
  );
});

const RelEdge = memo(function RelEdge(props: EdgeProps<DomainFlowEdge>) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd } = props;
  const lit = useContext(LitContext);
  const on = lit.edges?.has(id) ?? false;
  const dim = lit.edges !== null && !on;
  const kind = data?.kind ?? "";
  const colour = relColour(kind);
  const [path, lx, ly] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 16, offset: 20 });
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        className={on ? "mem-edge-live" : undefined}
        style={{ stroke: colour, strokeWidth: on ? 2.4 : 1.6, opacity: dim ? 0.12 : 0.9, transition: "opacity var(--dur-fast) var(--ease)" }}
      />
      {dim ? null : (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan pointer-events-none absolute"
            style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)`, zIndex: on ? 10 : 1 }}
          >
            <span
              className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border bg-surface px-2 text-2xs font-medium leading-4 shadow-e1 ${
                on ? "border-line-strong text-heading" : "border-line text-slate"
              }`}
            >
              <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: colour }} />
              {kind}
            </span>
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
});

const nodeTypes = { domain: DomainNode };
const edgeTypes = { rel: RelEdge };

// ---- layout -------------------------------------------------------------------------------------

let elkInstance: Promise<{ layout: (g: ElkNode) => Promise<ElkNode> }> | null = null;
function elk() {
  elkInstance ??= import("elkjs/lib/elk.bundled.js").then(({ default: ELK }) => new ELK());
  return elkInstance;
}

async function layout(elements: DomainElement[]): Promise<{ nodes: DomainFlowNode[]; edges: DomainFlowEdge[] }> {
  const ns = elements.filter((e) => !isEdge(e)).map((e) => e.data as DomainNodeData & { id: string });
  const es = elements.filter(isEdge).map((e) => e.data);
  const res = await (await elk()).layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "28",
      "elk.layered.spacing.nodeNodeBetweenLayers": "110",
      "elk.layered.spacing.edgeNodeBetweenLayers": "30",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      "elk.separateConnectedComponents": "true",
      "elk.spacing.componentComponent": "56",
    },
    children: ns.map((n) => ({ id: n.id, width: sizeOf(n.kind).w, height: sizeOf(n.kind).h })),
    edges: es.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  });
  const pos = new Map((res.children ?? []).map((c) => [c.id, { x: c.x ?? 0, y: c.y ?? 0 }]));
  return {
    nodes: ns.map(({ id, ...data }) => ({ id, type: "domain", position: pos.get(id) ?? { x: 0, y: 0 }, data: { ...data } })),
    edges: es.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: "rel",
      data: { kind: e.kind },
      markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: relColour(e.kind) },
    })),
  };
}

// ---- canvas -------------------------------------------------------------------------------------

function Inner({
  elements,
  selectedProcessId,
  onOpen,
}: {
  elements: DomainElement[];
  selectedProcessId: string | null;
  onOpen: (processId: string) => void;
}) {
  const [scene, setScene] = useState<{ nodes: DomainFlowNode[]; edges: DomainFlowEdge[] } | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const key = useMemo(() => elements.map((e) => e.data.id).join("|"), [elements]);

  useEffect(() => {
    let live = true;
    layout(elements).then((s) => {
      if (!live) return;
      setScene(s);
    });
    return () => {
      live = false;
    };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedId = useMemo(
    () => scene?.nodes.find((n) => selectedProcessId && n.data.processId === selectedProcessId)?.id ?? null,
    [scene, selectedProcessId],
  );
  const focus = hover ?? selectedId;
  const lit = useMemo(() => {
    if (!focus || !scene) return { nodes: null, edges: null };
    const nodes = new Set([focus]);
    const edges = new Set<string>();
    for (const e of scene.edges) {
      if (e.source === focus || e.target === focus) {
        edges.add(e.id);
        nodes.add(e.source);
        nodes.add(e.target);
      }
    }
    return { nodes, edges };
  }, [focus, scene]);
  const nodes = useMemo(() => scene?.nodes.map((n) => ({ ...n, selected: n.id === selectedId })) ?? [], [scene, selectedId]);

  if (!scene) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate">
        <Loader2 size={16} className="mr-2 animate-spin" aria-hidden /> Laying out the domain…
      </div>
    );
  }
  return (
    <LitContext.Provider value={lit}>
      <ReactFlow
        key={key}
        fitView
        fitViewOptions={{ padding: { top: 0.08, right: 0.08, bottom: "56px", left: 0.08 }, maxZoom: 1.1 }}
        nodes={nodes}
        edges={scene.edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable={false}
        minZoom={0.25}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        onNodeMouseEnter={(_, n) => setHover(n.id)}
        onNodeMouseLeave={() => setHover(null)}
        onNodeClick={(_, n) => {
          const pid = (n.data as DomainNodeData).processId;
          if (pid) onOpen(pid);
        }}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1.2} color="rgb(var(--c-line-strong)/0.55)" />
        <Controls showInteractive={false} position="top-right" className="!shadow-e1" />
      </ReactFlow>
    </LitContext.Provider>
  );
}

/** The legend: one chip per kind of node, coloured as on the canvas. */
export function DomainLegend() {
  return (
    <div className="pointer-events-none absolute bottom-2 left-3 flex flex-wrap gap-1.5 text-2xs text-slate">
      {Object.entries(KIND).map(([k, v]) => {
        const Icon = v.icon;
        return (
          <span key={k} className="inline-flex items-center gap-1 rounded-full border border-line bg-surface/90 px-2 py-0.5 shadow-e1">
            <Icon size={11} style={{ color: tok(v.token) }} aria-hidden />
            {v.label}
          </span>
        );
      })}
    </div>
  );
}

export default function DomainGraph(props: {
  elements: DomainElement[];
  selectedProcessId: string | null;
  onOpen: (processId: string) => void;
}) {
  return (
    <div className="h-full w-full" data-testid="domain-graph">
      <ReactFlowProvider>
        <Inner {...props} />
      </ReactFlowProvider>
    </div>
  );
}
