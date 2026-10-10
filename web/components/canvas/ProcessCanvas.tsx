"use client";

import { useEffect, useRef, useState } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Loader2 } from "lucide-react";
import { layoutCanvas } from "@/lib/layout";
import { STRIP } from "@/lib/activity";
import type { ProcessCanvasData } from "@/lib/types";
import { ActivityNode, GroupNode, TerminalNode } from "./ActivityNode";
import { FlowEdge, type FlowEdgeData } from "./FlowEdge";

const nodeTypes = { activity: ActivityNode, terminal: TerminalNode, container: GroupNode };
const edgeTypes = { flow: FlowEdge };

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

  useEffect(() => {
    let live = true;
    layoutCanvas(canvas)
      .then((r) => {
        if (!live) return;
        setNodes(r.nodes);
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

  if (error) return <p className="p-4 text-[14px] text-error">Layout failed: {error}</p>;
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
      <Background color="#E2DFD6" gap={20} />
      <Controls showInteractive={false} position="bottom-right" />
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
    <div className="relative h-[600px] overflow-hidden rounded-card border border-line bg-page" data-testid="process-canvas">
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
    <div className="pointer-events-none absolute left-3 top-3 flex flex-wrap gap-2 rounded-btn bg-white/90 px-2.5 py-1.5 text-[11px] text-slate shadow-sm">
      {items.map(([l, c]) => (
        <span key={l} className="flex items-center gap-1">
          <span className="h-2.5 w-1 rounded-sm" style={{ background: c }} />
          {l}
        </span>
      ))}
      <span className="flex items-center gap-1">
        <span className="font-mono text-decision">expr</span> condition
      </span>
      <span className="flex items-center gap-1">
        <span className="inline-block w-4 border-t-2 border-dashed border-slate" /> otherwise
      </span>
    </div>
  );
}
