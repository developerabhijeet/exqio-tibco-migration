"use client";

import { useEffect, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Loader2 } from "lucide-react";
import { layoutCanvas } from "@/lib/layout";
import { family, STRIP } from "@/lib/activity";
import type { ProcessCanvasData } from "@/lib/types";
import { ActivityNode, GroupNode, TerminalNode } from "./ActivityNode";
import { FlowEdge, type FlowEdgeData } from "./FlowEdge";

const nodeTypes = { activity: ActivityNode, terminal: TerminalNode, container: GroupNode };
const edgeTypes = { flow: FlowEdge };

/** The overview's colour per node: an activity's family strip, a terminal's tone, a group faint. */
function miniColor(n: Node): string {
  if (n.type === "container") return "rgb(var(--c-line-strong)/0.35)";
  if (n.type === "terminal") return (n.data as { kind?: string }).kind === "end" ? STRIP.end : STRIP.http;
  const fam = family(String((n.data as { canonical_type?: string }).canonical_type ?? ""));
  return fam === "other" ? "rgb(var(--c-line-strong))" : STRIP[fam];
}

function Inner({
  canvas,
  selected,
  onSelect,
}: {
  canvas: ProcessCanvasData;
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge<FlowEdgeData>[]>([]);
  const [error, setError] = useState<string | null>(null);
  const flow = useReactFlow();
  const box = useRef<HTMLDivElement>(null);
  // The selection can arrive before the layout (a citation opened the canvas on a step).
  const selectedRef = useRef(selected);
  useEffect(() => {
    selectedRef.current = selected;
  }, [selected]);

  useEffect(() => {
    let live = true;
    layoutCanvas(canvas)
      .then((r) => {
        if (!live) return;
        const sel = selectedRef.current;
        setNodes(r.nodes.map((n) => (n.type === "container" ? n : { ...n, selected: n.id === sel })));
        setEdges(r.edges);
        requestAnimationFrame(async () => {
          await flow.fitView({ padding: 0.08, maxZoom: 1 });
          // A long process would shrink below reading size: keep it readable and start at the left.
          const MIN = 0.55;
          if (flow.getViewport().zoom < MIN) {
            const b = flow.getNodesBounds(r.nodes.filter((n) => !n.parentId));
            const height = box.current?.clientHeight ?? 600;
            flow.setViewport({ x: 24 - b.x * MIN, y: (height - b.height * MIN) / 2 - b.y * MIN, zoom: MIN });
          }
          const target = sel ? flow.getInternalNode(sel) : undefined;
          if (target) {
            const { x, y } = target.internals.positionAbsolute;
            const w = target.measured.width ?? 200;
            const h = target.measured.height ?? 60;
            await flow.setCenter(x + w / 2, y + h / 2, { zoom: flow.getViewport().zoom });
          }
        });
      })
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [canvas, flow]);

  useEffect(() => {
    setNodes((ns) => ns.map((n) => (n.type === "container" ? n : { ...n, selected: n.id === selected })));
  }, [selected]);

  if (error) return <p className="p-4 text-md text-error">Layout failed: {error}</p>;
  return (
    <div ref={box} className="h-full w-full">
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodeClick={(_, n) => n.type !== "container" && onSelect(n.id)}
      onPaneClick={() => onSelect(null)}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable
      minZoom={0.2}
      maxZoom={1.6}
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Dots} color="rgb(var(--c-line-strong))" gap={22} size={1.4} />
      <Controls showInteractive={false} position="bottom-left" />
      <MiniMap
        position="bottom-right"
        pannable
        zoomable
        nodeColor={miniColor}
        nodeStrokeWidth={0}
        nodeBorderRadius={6}
        maskColor="rgb(var(--c-page)/0.72)"
        bgColor="rgb(var(--c-surface))"
        style={{ width: 160, height: 100 }}
        ariaLabel="Process overview"
      />
    </ReactFlow>
    </div>
  );
}

export function ProcessCanvas({
  canvas,
  loading,
  selected,
  onSelect,
}: {
  canvas: ProcessCanvasData | undefined;
  loading: boolean;
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  return (
    <div className="process-flow relative h-[600px] overflow-hidden rounded-card border border-line bg-page" data-testid="process-canvas">
      {loading || !canvas ? (
        <div className="flex h-full items-center justify-center gap-2 text-slate">
          <Loader2 size={18} className="animate-spin" aria-hidden /> Loading canvas
        </div>
      ) : (
        <ReactFlowProvider>
          <Inner canvas={canvas} selected={selected} onSelect={onSelect} />
        </ReactFlowProvider>
      )}
      <Legend />
    </div>
  );
}

function Legend() {
  const items: [string, string][] = [
    ["HTTP / REST", STRIP.http],
    ["JDBC", STRIP.jdbc],
    ["JMS", STRIP.jms],
    ["Mapper", STRIP.mapper],
    ["End", STRIP.end],
    ["Throw", STRIP.throw],
  ];
  return (
    <div className="pointer-events-none absolute left-3 top-3 flex max-w-[calc(100%-1.5rem)] flex-wrap items-center gap-x-3 gap-y-1 rounded-card bg-surface/90 px-3 py-1.5 text-2xs text-slate shadow-e1 ring-1 ring-line backdrop-blur-sm">
      {items.map(([l, c]) => (
        <span key={l} className="flex items-center gap-1">
          <span className="h-3 w-[3px] rounded-full" style={{ background: c }} />
          {l}
        </span>
      ))}
      <span className="flex items-center gap-1">
        <span className="rounded-full border border-decision/40 bg-decision-bg px-1.5 font-mono leading-4 text-decision">if</span> condition
      </span>
      <span className="flex items-center gap-1">
        <span className="inline-block w-4 border-t-2 border-dashed border-slate" /> otherwise
      </span>
    </div>
  );
}
