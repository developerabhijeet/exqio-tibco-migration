"use client";

// The memory graph on React Flow. graph/layout.ts decides what is drawn (processes and classes
// become containers holding their steps and the knowledge about only them) and places it with
// elkjs, layered left to right (lineage mode partitions old code, new code, then knowledge).
// Hovering or selecting a node lights its neighbourhood (or the selected node's lineage chain)
// and dims the rest; the lit edges flow towards their targets.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  ControlButton,
  Controls,
  MarkerType,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Focus } from "lucide-react";
import { displayName, type GraphMode, type Subgraph } from "@/lib/memory";
import { kindOf, LABEL_ORDER, LINEAGE_TYPES, relColor, tok } from "./graph/kinds";
import { buildModel, CARD, layoutModel, type MemFlowEdge, type MemFlowNode, type MemNodeData, type Model } from "./graph/layout";
import { CanvasStateContext, memoryNodeTypes, type CanvasState } from "./graph/MemoryNodes";
import { memoryEdgeTypes } from "./graph/MemoryEdge";

export interface CanvasHighlight {
  added: string[];
  changed: string[];
  removed: string[];
}

export interface MemoryCanvasProps {
  graph: Subgraph;
  mode: GraphMode;
  /** The node open in the drawer. */
  selected?: string;
  /** Outlines from a revision's diff (added green, changed amber, removed red). */
  highlight?: CanvasHighlight;
  /** The selected node's lineage chain (GET graph mode=lineage); dims everything outside it. */
  lineage?: Subgraph;
  /** The node the neighbourhood was read around: unreachable-looking nodes link back to it. */
  focus?: string | null;
  /** Labels or side filters are on: some paths run through nodes the read left out. */
  filtered?: boolean;
  onSelect(uid: string): void;
  onExpand(uid: string): void;
}

/** Short transitions (fit, centre), none for a reader who asked for reduced motion. */
function motion(): number {
  if (typeof window === "undefined" || !window.matchMedia) return 280;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 280;
}

const fmt = (n: number) => n.toLocaleString("en-US");
const EMPTY = new Set<string>();
const FIT = { padding: 0.12, maxZoom: 1.1 };

/** A grid, when elk cannot place a scene: everything still drawn and reachable. */
function fallbackLayout(model: Model): { nodes: MemFlowNode[]; edges: MemFlowEdge[] } {
  const cols = Math.max(1, Math.ceil(Math.sqrt(model.nodes.length)));
  return {
    nodes: model.nodes.map((n, i) => ({
      id: n.uid,
      type: "entity",
      position: { x: (i % cols) * (CARD.w + 60), y: Math.floor(i / cols) * (CARD.h + 40) },
      width: CARD.w,
      height: CARD.h,
      data: { node: n, isFocus: false, compact: false },
    })),
    edges: model.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: "mem",
      data: { type: e.type, status: e.status, internal: false, indirect: e.indirect },
    })),
  };
}

function Inner({ graph, mode, selected, highlight, lineage, focus, filtered, onSelect, onExpand }: MemoryCanvasProps) {
  const flow = useReactFlow<MemFlowNode, MemFlowEdge>();
  const box = useRef<HTMLDivElement>(null);
  const model = useMemo(() => buildModel(graph, mode, focus), [graph, mode, focus]);
  // A scene is fitted once: a neighbourhood per focus (expansions and filter changes relayout in
  // place, keeping the reader's viewport once they have moved it), a lineage view per node set.
  const sceneKey =
    mode === "lineage" ? `lineage|${graph.nodes.map((n) => n.uid).join("|")}` : `neighbourhood|${focus ?? ""}`;
  const [laid, setLaid] = useState<{ nodes: MemFlowNode[]; edges: MemFlowEdge[]; scene: string; fresh: boolean } | null>(null);
  const [laidOut, setLaidOut] = useState("");
  const [hovered, setHovered] = useState<string | null>(null);
  const scene = useRef<string | null>(null);
  // Whether the reader has panned or zoomed since the scene was fitted.
  const moved = useRef(false);

  useEffect(() => {
    let live = true;
    const fresh = scene.current !== sceneKey;
    if (fresh) setLaidOut("");
    layoutModel(model, mode, focus)
      .then((r) => ({ ...r, ok: true }))
      .catch(() => ({ ...fallbackLayout(model), ok: false }))
      .then((r) => {
        if (!live) return;
        scene.current = sceneKey;
        if (fresh) moved.current = false;
        setLaid({ nodes: r.nodes, edges: r.edges, scene: sceneKey, fresh });
        setLaidOut(mode === "lineage" && model.nodeCount > 0 && !r.ok ? "lineage-fallback" : mode);
      });
    return () => {
      live = false;
    };
  }, [model]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fit a new scene (or a relayout the reader has not moved away from) once it is drawn.
  useEffect(() => {
    if (!laid || !laid.nodes.length) return;
    if (!laid.fresh && moved.current) return;
    const frame = requestAnimationFrame(() => void flow.fitView({ ...FIT, duration: laid.fresh ? 0 : motion() }));
    return () => cancelAnimationFrame(frame);
  }, [laid, flow]);

  // The drawer opening or closing resizes the canvas: re-fit until the reader takes over.
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let frame = 0;
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (!moved.current && flow.getNodes().length) void flow.fitView(FIT);
      });
    });
    ro.observe(el);
    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
    };
  }, [flow]);

  // A selected node off screen is brought into view.
  useEffect(() => {
    if (!selected || !laid) return;
    const frame = requestAnimationFrame(() => {
      const n = flow.getInternalNode(selected);
      const el = box.current;
      if (!n || !el) return;
      const { x, y, zoom } = flow.getViewport();
      const w = n.measured.width ?? CARD.w;
      const h = n.measured.height ?? CARD.h;
      const cx = n.internals.positionAbsolute.x + w / 2;
      const cy = n.internals.positionAbsolute.y + h / 2;
      const sx = cx * zoom + x;
      const sy = cy * zoom + y;
      const m = 40;
      if (sx >= m && sy >= m && sx <= el.clientWidth - m && sy <= el.clientHeight - m) return;
      void flow.setCenter(cx, cy, { zoom, duration: motion() });
    });
    return () => cancelAnimationFrame(frame);
  }, [selected, laid, flow]);

  const edges = useMemo(
    () =>
      (laid?.edges ?? []).map((e) =>
        e.data?.indirect
          ? e
          : {
              ...e,
              markerEnd: { type: MarkerType.ArrowClosed, color: relColor(e.data?.type ?? "", e.data?.status), width: 14, height: 14 },
            },
      ),
    [laid],
  );
  const nodes = useMemo(
    () => (laid?.nodes ?? []).map((n) => (n.id === selected ? { ...n, selected: true } : n)),
    [laid, selected],
  );

  // What the hovered (else the selected) node lights: its neighbours, or the selected node's
  // lineage chain when it has one. A container is lit with anything inside it.
  const state = useMemo<CanvasState>(() => {
    const hl = {
      added: highlight ? new Set(highlight.added) : EMPTY,
      changed: highlight ? new Set(highlight.changed) : EMPTY,
      removed: highlight ? new Set(highlight.removed) : EMPTY,
    };
    const centre = hovered ?? selected ?? null;
    if (!centre || !model.byId.has(centre)) return { lit: null, litEdges: null, selected, hovered, ...hl };
    const lit = new Set<string>([centre]);
    const litEdges = new Set<string>();
    const chain = !hovered && lineage && lineage.nodes.length > 1 ? lineage : null;
    if (chain) {
      const chainEdges = new Set(chain.edges.map((e) => e.uid));
      for (const n of chain.nodes) if (model.byId.has(n.uid)) lit.add(n.uid);
      for (const e of model.edges)
        if (chainEdges.has(e.id) || (lit.has(e.source) && lit.has(e.target) && LINEAGE_TYPES.has(e.type))) litEdges.add(e.id);
    } else {
      for (const e of model.edges) {
        if (e.source !== centre && e.target !== centre) continue;
        // A container's own steps are shown by containment, not by a line to each.
        if (e.internal && e.type === "CONTAINS" && model.containers.has(centre)) continue;
        litEdges.add(e.id);
        lit.add(e.source);
        lit.add(e.target);
      }
    }
    for (const uid of [...lit]) {
      const p = model.parentOf.get(uid) ?? model.knowledgeOf.get(uid);
      if (p) lit.add(p);
    }
    return { lit, litEdges, selected, hovered, ...hl };
  }, [hovered, selected, lineage, highlight, model]);

  const onNodeEnter = useCallback((_: unknown, n: Node) => setHovered(n.id), []);
  const onNodeLeave = useCallback(() => setHovered(null), []);
  const onNodeClick = useCallback((_: unknown, n: Node) => onSelect(n.id), [onSelect]);
  const onNodeDoubleClick = useCallback((_: unknown, n: Node) => onExpand(n.id), [onExpand]);
  const onMoveStart = useCallback((e: MouseEvent | TouchEvent | null) => {
    // Programmatic fits and centres carry no event; a wheel or a drag is the reader taking over.
    if (e) moved.current = true;
  }, []);
  const miniColor = useCallback((n: Node) => {
    const d = n.data as MemNodeData;
    const k = kindOf(d.node.label);
    return n.type === "container" ? tok(k.token, 0.28) : tok(k.token);
  }, []);
  const centreSelected = () => {
    if (!selected) return;
    const n = flow.getInternalNode(selected);
    if (!n) return;
    const w = n.measured.width ?? CARD.w;
    const h = n.measured.height ?? CARD.h;
    void flow.setCenter(n.internals.positionAbsolute.x + w / 2, n.internals.positionAbsolute.y + h / 2, {
      zoom: Math.max(flow.getViewport().zoom, 1),
      duration: motion(),
    });
  };

  const kinds = useMemo(() => {
    const present = [...new Set(graph.nodes.map((n) => n.label))];
    const at = (l: string) => (LABEL_ORDER.indexOf(l) < 0 ? 99 : LABEL_ORDER.indexOf(l));
    return present.sort((a, b) => at(a) - at(b));
  }, [graph]);
  const hidden = filtered ? "the filters hide" : "not shown";
  const hint =
    model.indirect > 0
      ? `${fmt(model.indirect)} linked via nodes ${hidden}`
      : model.dropped > 0
        ? `${fmt(model.dropped)} links to nodes ${hidden}`
        : null;
  const hintTitle = filtered
    ? "Label or side filters hide the nodes these paths run through."
    : "These paths run through nodes beyond the ones shown here.";
  const focusNode = graph.nodes.find((n) => n.uid === (mode === "lineage" ? graph.nodes[0]?.uid : focus));
  const migrated = graph.edges.some((e) => e.type === "MIGRATED_TO");

  return (
    <div
      ref={box}
      className="mem-flow absolute inset-0"
      role="group"
      aria-label={`Memory graph, ${mode} view${focusNode ? ` of ${displayName(focusNode)}` : ""}: ${fmt(model.nodeCount)} nodes`}
      data-testid="memory-canvas"
      data-node-count={model.nodeCount}
      data-mode={mode}
      data-laid-out={model.nodeCount === 0 ? mode : laidOut}
    >
      <CanvasStateContext.Provider value={state}>
        <ReactFlow<MemFlowNode, MemFlowEdge>
          nodes={nodes}
          edges={edges}
          nodeTypes={memoryNodeTypes}
          edgeTypes={memoryEdgeTypes}
          onNodeClick={onNodeClick}
          onNodeDoubleClick={onNodeDoubleClick}
          onNodeMouseEnter={onNodeEnter}
          onNodeMouseLeave={onNodeLeave}
          onMoveStart={onMoveStart}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          zoomOnDoubleClick={false}
          onlyRenderVisibleElements={model.nodeCount > 150}
          minZoom={0.08}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} color="rgb(var(--c-line-strong))" gap={22} size={1.4} />
          <Controls showInteractive={false} position="bottom-left" fitViewOptions={FIT}>
            {selected ? (
              <ControlButton onClick={centreSelected} title="Centre on the selected node" aria-label="Centre on the selected node">
                <Focus size={14} aria-hidden />
              </ControlButton>
            ) : null}
          </Controls>
          {model.nodeCount > 0 ? (
            <MiniMap
              position="bottom-right"
              pannable
              zoomable
              nodeColor={miniColor}
              nodeStrokeWidth={0}
              nodeBorderRadius={6}
              maskColor="rgb(var(--c-page)/0.72)"
              bgColor="rgb(var(--c-surface))"
              style={{ width: 168, height: 112 }}
              ariaLabel="Memory graph overview"
            />
          ) : null}
          {model.nodeCount > 0 ? (
            <Panel position="top-left" className="pointer-events-none !m-3">
              <div className="flex max-w-[min(560px,60vw)] flex-wrap items-center gap-x-3 gap-y-1.5 rounded-card bg-surface/90 px-3 py-2 text-2xs text-slate shadow-e1 ring-1 ring-line backdrop-blur-sm">
                {kinds.map((l) => {
                  const k = kindOf(l);
                  const Icon = k.icon;
                  return (
                    <span key={l} className="inline-flex items-center gap-1">
                      <span
                        aria-hidden
                        className="flex h-4 w-4 items-center justify-center rounded"
                        style={{ background: tok(k.token, 0.14), color: tok(k.token) }}
                      >
                        <Icon size={10} strokeWidth={2.4} />
                      </span>
                      {k.label}
                    </span>
                  );
                })}
                {migrated ? (
                  <>
                    <span className="inline-flex items-center gap-1.5">
                      <span className="h-[3px] w-5 rounded bg-accent" />
                      migrated to
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <span className="h-0 w-5 border-t-2 border-dashed border-error" />
                      rejected
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <span className="h-[2px] w-5 rounded bg-gate" />
                      corrected
                    </span>
                  </>
                ) : null}
                {hint ? (
                  <span className="inline-flex items-center gap-1.5" data-testid="memory-filtered-hint" title={hintTitle}>
                    <span className="h-0 w-5 border-t border-dashed border-line-strong" />
                    {hint}
                  </span>
                ) : null}
              </div>
            </Panel>
          ) : null}
        </ReactFlow>
      </CanvasStateContext.Provider>

      {graph.truncated ? (
        <div
          data-testid="memory-truncated"
          className="absolute left-1/2 top-4 z-10 -translate-x-1/2 rounded-full bg-decision-bg px-4 py-1.5 text-sm font-medium text-decision shadow-card ring-1 ring-inset ring-decision/20"
        >
          Showing {fmt(graph.nodes.length)} of {fmt(graph.total)}: narrow the filters
        </div>
      ) : null}

      {model.nodeCount === 0 ? (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <p className="max-w-sm text-center text-base text-slate">
            Nothing to show here{filtered ? " with these filters" : ""}.{" "}
            {filtered ? "Clear a filter or search for a node." : "Search for a node to explore around it."}
          </p>
        </div>
      ) : null}
    </div>
  );
}

export function MemoryCanvas(props: MemoryCanvasProps) {
  return (
    <div className="relative h-full w-full">
      <ReactFlowProvider>
        <Inner {...props} />
      </ReactFlowProvider>
    </div>
  );
}
