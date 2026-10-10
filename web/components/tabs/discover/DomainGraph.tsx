"use client";

// Cytoscape view of the estate graph for one domain: its processes, the tables, queues, topics
// and endpoints they touch, and the processes they call in other domains.
import { useEffect, useMemo, useRef } from "react";
import type { Core, ElementDefinition } from "cytoscape";
import type { EstateGraph, ProcessSummary } from "@/lib/types";

const SHOWN_EDGES = new Set(["calls", "reads", "writes", "publishes", "consumes", "exposes"]);

export interface GraphFilters {
  band: string;
  status: string;
}

export function graphElements(
  graph: EstateGraph | null,
  inDomain: ProcessSummary[],
  all: ProcessSummary[],
  filters: GraphFilters,
): ElementDefinition[] {
  const byIr = new Map(all.map((p) => [p.ir_id, p]));
  const keep = new Set(
    inDomain
      .filter((p) => (filters.band === "all" || p.complexity_band === filters.band) && (filters.status === "all" || p.migration_status === filters.status))
      .map((p) => `process:${p.ir_id}`),
  );
  const nodes = new Map<string, ElementDefinition>();
  const addProcess = (id: string, own: boolean) => {
    const p = byIr.get(id.replace(/^process:/, ""));
    nodes.set(id, {
      data: { id, label: p?.name ?? id.replace(/^process:/, ""), kind: own ? "process" : "process-ext", processId: p?.id ?? "" },
    });
  };
  keep.forEach((id) => addProcess(id, true));
  const edges: ElementDefinition[] = [];
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

const STYLE = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      "font-family": "Poppins, ui-sans-serif, sans-serif",
      "font-size": 12,
      color: "#425466",
      "text-valign": "bottom",
      "text-margin-y": 4,
      "background-color": "#F4F3EE",
      "border-width": 1,
      "border-color": "#B8B3A7",
      width: 18,
      height: 18,
    },
  },
  {
    selector: 'node[kind = "process"]',
    style: {
      shape: "round-rectangle",
      "background-color": "#9B5DE5",
      "border-color": "#7E3FCB",
      color: "#FFFFFF",
      "font-size": 13,
      "font-weight": 600,
      "text-valign": "center",
      "text-margin-y": 0,
      width: (ele: { data: (k: string) => string }) => Math.max(70, ele.data("label").length * 7.6 + 24),
      height: 30,
    },
  },
  {
    selector: 'node[kind = "process-ext"]',
    style: {
      shape: "round-rectangle",
      "background-color": "#F1E9FC",
      "border-color": "#9B5DE5",
      color: "#20063B",
      "text-valign": "center",
      "text-margin-y": 0,
      "font-size": 12,
      width: (ele: { data: (k: string) => string }) => Math.max(60, ele.data("label").length * 6.8 + 18),
      height: 26,
    },
  },
  { selector: 'node[kind = "table"]', style: { shape: "barrel", width: 30, height: 34, "background-color": "#E9E6DE" } },
  { selector: 'node[kind = "queue"]', style: { shape: "round-tag", width: 22, height: 22 } },
  { selector: 'node[kind = "topic"]', style: { shape: "diamond", width: 24, height: 24 } },
  { selector: 'node[kind = "endpoint"]', style: { shape: "ellipse", width: 18, height: 18 } },
  {
    selector: "edge",
    style: {
      width: 1.3,
      "line-color": "#C9C4B8",
      "target-arrow-color": "#C9C4B8",
      "target-arrow-shape": "triangle",
      "arrow-scale": 0.8,
      "curve-style": "bezier",
      label: "data(label)",
      "font-size": 10,
      color: "#425466",
      "text-background-color": "#FFFFFF",
      "text-background-opacity": 0.9,
      "text-background-padding": "1px",
      "text-rotation": "autorotate",
    },
  },
  { selector: 'edge[kind = "calls"]', style: { "line-color": "#9B5DE5", "target-arrow-color": "#9B5DE5" } },
  { selector: 'edge[kind = "writes"], edge[kind = "publishes"]', style: { "line-color": "#8C8676", "target-arrow-color": "#8C8676" } },
  { selector: "node:selected", style: { "border-width": 3, "border-color": "#20063B" } },
];

export default function DomainGraph({
  elements,
  selectedProcessId,
  onOpen,
}: {
  elements: ElementDefinition[];
  selectedProcessId: string | null;
  onOpen: (processId: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const openRef = useRef(onOpen);
  openRef.current = onOpen;
  const key = useMemo(() => elements.map((e) => e.data.id).join("|"), [elements]);

  useEffect(() => {
    let live = true;
    import("cytoscape").then(({ default: cytoscape }) => {
      if (!live || !box.current) return;
      cy.current?.destroy();
      const inst = cytoscape({
        container: box.current,
        elements,
        style: STYLE as never,
        layout: { name: "preset" },
        wheelSensitivity: 0.2,
        minZoom: 0.3,
        maxZoom: 2,
      });
      const layout = inst.layout({
        name: "cose",
        animate: false,
        fit: false,
        nodeRepulsion: () => 8000,
        idealEdgeLength: () => 70,
        gravity: 0.6,
        randomize: true,
        // Spread over the panel's own shape (wide and short), not a square.
        boundingBox: { x1: 0, y1: 0, w: box.current.clientWidth || 900, h: box.current.clientHeight || 380 },
        componentSpacing: 60,
      } as never);
      layout.one("layoutstop", () => {
        inst.resize();
        inst.fit(undefined, 28);
        if (inst.zoom() > 1.25) {
          inst.zoom(1.25);
          inst.center();
        }
      });
      layout.run();
      inst.on("tap", "node", (evt) => {
        const pid = evt.target.data("processId");
        if (pid) openRef.current(pid);
      });
      cy.current = inst;
    });
    return () => {
      live = false;
    };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => cy.current?.destroy(), []);

  useEffect(() => {
    const inst = cy.current;
    if (!inst) return;
    inst.nodes().unselect();
    if (selectedProcessId) inst.nodes(`[processId = "${selectedProcessId}"]`).select();
  }, [selectedProcessId, key]);

  return <div ref={box} className="h-full w-full" data-testid="domain-graph" />;
}
