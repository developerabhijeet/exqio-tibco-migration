"use client";

// The memory graph on a cytoscape canvas. Neighbourhood mode lays the subgraph out with `cose`;
// lineage mode places it with elkjs (layered, left to right: old code, then new code, then
// knowledge) and hands cytoscape the positions as a `preset`. Selecting a node highlights its
// lineage chain (the `lineage` subgraph) and dims the rest.
import { useEffect, useMemo, useRef } from "react";
import type { Core, ElementDefinition } from "cytoscape";
import type { ElkNode } from "elkjs/lib/elk-api";
import { Focus, Maximize, Minus, Plus } from "lucide-react";
import {
  GROUP_HEX,
  GROUP_LABEL,
  displayName,
  type GraphMode,
  type Side,
  type Subgraph,
} from "@/lib/memory";

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

type Size = { w: number; h: number };

// Shape by label family: containers and flows are rounded boxes, steps and methods ellipses,
// data stores barrels, messaging diamonds, configuration tags and knowledge its own set.
const SHAPE: Record<string, string> = {
  Domain: "round-rectangle",
  Application: "round-rectangle",
  Solution: "round-rectangle",
  CodeProject: "round-rectangle",
  Process: "round-rectangle",
  Class: "round-rectangle",
  CodeFile: "cut-rectangle",
  Activity: "ellipse",
  Method: "ellipse",
  Contract: "round-tag",
  SubstVar: "round-tag",
  ConfigKey: "round-tag",
  SharedResource: "round-hexagon",
  Endpoint: "hexagon",
  Table: "barrel",
  Queue: "round-diamond",
  Topic: "round-diamond",
  MessagingEntity: "round-diamond",
  Test: "round-triangle",
  Chunk: "round-pentagon",
  Memory: "round-heptagon",
  GateDecision: "star",
  Error: "vee",
  Curation: "round-tag",
};

const CONTAINERS = new Set([
  "Domain",
  "Application",
  "Solution",
  "CodeProject",
]);
const FLOWS = new Set(["Process", "Class"]);
const LINEAGE_TYPES = new Set(["MIGRATED_TO", "TESTS", "FAILED_AT"]);

function sizeOf(label: string): Size {
  if (CONTAINERS.has(label)) return { w: 46, h: 32 };
  if (FLOWS.has(label)) return { w: 38, h: 26 };
  return { w: 24, h: 24 };
}

const short = (s: string, n = 30) =>
  s.length > n ? `${s.slice(0, n - 1)}…` : s;

export interface CanvasElements {
  elements: ElementDefinition[];
  nodeCount: number;
  /** Edges the read returned whose other end is not among the nodes (filtered or truncated). */
  dropped: number;
  /** Dashed links drawn from the focus to nodes whose own path to it was filtered out. */
  indirect: number;
}

/** Cytoscape elements for a subgraph: edges to nodes outside the set are dropped, never drawn
 * to nowhere; a node left with no edge in a focused neighbourhood links back to the focus with a
 * dashed "filtered path" edge, since the read guarantees it lies within reach of it. */
export function canvasElements(
  graph: Subgraph,
  mode: GraphMode,
  focus?: string | null,
): CanvasElements {
  const uids = new Set(graph.nodes.map((n) => n.uid));
  const nodes: ElementDefinition[] = graph.nodes.map((n) => {
    const size = sizeOf(n.label);
    const scale = n.uid === focus ? 1.3 : 1;
    return {
      data: {
        id: n.uid,
        label: short(displayName(n)),
        kind: n.label,
        side: n.side,
        shape: SHAPE[n.label] ?? "ellipse",
        fill: GROUP_HEX[n.side].fill,
        border: GROUP_HEX[n.side].border,
        strong: GROUP_HEX[n.side].strong,
        w: size.w * scale,
        h: size.h * scale,
      },
      classes: [
        n.curated ? "curated" : "",
        n.obsolete ? "obsolete" : "",
        n.uid === focus ? "focus" : "",
      ]
        .filter(Boolean)
        .join(" "),
    };
  });
  const edges: ElementDefinition[] = [];
  const linked = new Set<string>();
  let dropped = 0;
  for (const e of graph.edges) {
    if (!uids.has(e.src) || !uids.has(e.dst)) {
      dropped += 1;
      continue;
    }
    linked.add(e.src);
    linked.add(e.dst);
    edges.push({
      data: {
        id: e.uid,
        source: e.src,
        target: e.dst,
        type: e.type,
        status: e.status ?? "",
      },
      classes: [
        e.type === "MIGRATED_TO" ? "migrated" : "",
        e.status ?? "",
        LINEAGE_TYPES.has(e.type) ? "lineage-type" : "",
      ]
        .filter(Boolean)
        .join(" "),
    });
  }
  let indirect = 0;
  if (mode === "neighbourhood" && focus && uids.has(focus)) {
    for (const n of graph.nodes) {
      if (n.uid === focus || linked.has(n.uid)) continue;
      indirect += 1;
      edges.push({
        data: {
          id: `indirect:${focus}->${n.uid}`,
          source: focus,
          target: n.uid,
          type: "filtered path",
          status: "",
        },
        classes: "indirect",
      });
    }
  }
  return {
    elements: [...nodes, ...edges],
    nodeCount: nodes.length,
    dropped,
    indirect,
  };
}

const PARTITION: Record<Side, number> = { old: 0, new: 1, knowledge: 2 };

/** Lineage positions from elkjs: layered, left to right, old code in the first partition. */
async function lineagePositions(
  graph: Subgraph,
  els: ElementDefinition[],
): Promise<Record<string, { x: number; y: number }>> {
  const { default: ELK } = await import("elkjs/lib/elk.bundled.js");
  const elk = new ELK();
  const nodeEls = els.filter((e) => !("source" in e.data));
  const edgeEls = els.filter((e) => "source" in e.data);
  const sideOf = new Map(graph.nodes.map((n) => [n.uid, n.side]));
  // Room for the label under each node, so neighbouring labels do not overlap.
  const root: ElkNode = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.partitioning.activate": "true",
      "elk.spacing.nodeNode": "34",
      "elk.layered.spacing.nodeNodeBetweenLayers": "120",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      "elk.spacing.componentComponent": "60",
    },
    children: nodeEls.map((n) => ({
      id: String(n.data.id),
      width: Math.max(Number(n.data.w), 150),
      height: Number(n.data.h) + 22,
      layoutOptions: {
        "elk.partitioning.partition": String(
          PARTITION[sideOf.get(String(n.data.id)) ?? "old"],
        ),
      },
    })),
    edges: edgeEls.map((e) => ({
      id: String(e.data.id),
      sources: [String(e.data.source)],
      targets: [String(e.data.target)],
    })),
  };
  const out = await elk.layout(root);
  const pos: Record<string, { x: number; y: number }> = {};
  for (const c of out.children ?? [])
    pos[c.id] = { x: (c.x ?? 0) + (c.width ?? 0) / 2, y: (c.y ?? 0) + 14 };
  return pos;
}

const FONT = "Inter, ui-sans-serif, system-ui, sans-serif";

const STYLE = [
  {
    selector: "node",
    style: {
      shape: "data(shape)",
      width: "data(w)",
      height: "data(h)",
      "background-color": "data(fill)",
      "border-width": 1.5,
      "border-color": "data(border)",
      label: "data(label)",
      "font-family": FONT,
      "font-size": 11.5,
      color: "#425466",
      "text-valign": "bottom",
      "text-margin-y": 6,
      "text-wrap": "ellipsis",
      "text-max-width": "140px",
      "min-zoomed-font-size": 7,
      "transition-property": "opacity, border-width, background-color",
      "transition-duration": 150,
    },
  },
  {
    selector: "node.focus",
    style: {
      "border-width": 2.5,
      "font-weight": 600,
      color: "#20063B",
      "font-size": 12.5,
    },
  },
  {
    selector: "node.curated",
    style: {
      "underlay-color": "#9B5DE5",
      "underlay-padding": 6,
      "underlay-opacity": 0.22,
      "underlay-shape": "ellipse",
    },
  },
  {
    selector: "node.obsolete",
    style: { "border-style": "dashed", opacity: 0.6 },
  },
  {
    selector: "node.picked",
    style: {
      "background-color": "data(strong)",
      "border-color": "data(strong)",
      "border-width": 3,
      color: "#20063B",
      "font-weight": 600,
      "underlay-color": "#9B5DE5",
      "underlay-padding": 7,
      "underlay-opacity": 0.18,
    },
  },
  {
    selector: "node.added",
    style: { "border-color": "#1E7B3C", "border-width": 3.5 },
  },
  {
    selector: "node.changed",
    style: { "border-color": "#9A5E0A", "border-width": 3.5 },
  },
  {
    selector: "node.removed",
    style: {
      "border-color": "#A33A32",
      "border-width": 3.5,
      "border-style": "dashed",
      opacity: 0.4,
    },
  },
  {
    selector: "edge",
    style: {
      width: 1.2,
      "line-color": "#CFCABB",
      "target-arrow-color": "#CFCABB",
      "target-arrow-shape": "triangle",
      "arrow-scale": 0.75,
      "curve-style": "bezier",
      "font-family": FONT,
      "font-size": 9.5,
      color: "#425466",
      "text-background-color": "#FFFFFF",
      "text-background-opacity": 0.92,
      "text-background-padding": "2px",
      "text-rotation": "autorotate",
      "transition-property": "opacity, line-color",
      "transition-duration": 150,
    },
  },
  {
    selector: "edge.lineage-type",
    style: { "line-color": "#B9A4DA", "target-arrow-color": "#B9A4DA" },
  },
  {
    selector: "edge.migrated",
    style: {
      width: 3,
      "line-color": "#9B5DE5",
      "target-arrow-color": "#9B5DE5",
    },
  },
  {
    selector: "edge.rejected",
    style: {
      "line-style": "dashed",
      "line-color": "#A33A32",
      "target-arrow-color": "#A33A32",
    },
  },
  {
    selector: "edge.corrected",
    style: {
      "line-style": "solid",
      "line-color": "#1E7B3C",
      "target-arrow-color": "#1E7B3C",
    },
  },
  {
    selector: "edge.indirect",
    style: {
      "line-style": "dashed",
      "line-dash-pattern": [3, 4],
      "line-color": "#CFCABB",
      "target-arrow-shape": "none",
      width: 1,
    },
  },
  {
    selector: "edge.added",
    style: {
      "line-color": "#1E7B3C",
      "target-arrow-color": "#1E7B3C",
      width: 2.5,
    },
  },
  {
    selector: "edge.changed",
    style: {
      "line-color": "#9A5E0A",
      "target-arrow-color": "#9A5E0A",
      width: 2.5,
    },
  },
  {
    selector: "edge.removed",
    style: {
      "line-color": "#A33A32",
      "target-arrow-color": "#A33A32",
      "line-style": "dashed",
      opacity: 0.4,
    },
  },
  // The selected node's chain: labelled edges, everything else at 25 %.
  { selector: "edge.chain", style: { label: "data(type)", width: 2 } },
  { selector: "edge.chain.migrated", style: { width: 3.5 } },
  { selector: ".dim", style: { opacity: 0.25 } },
  { selector: "edge.lineage-mode", style: { label: "data(type)" } },
];

function ToolButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="flex h-9 w-9 items-center justify-center text-slate transition-colors hover:bg-warm hover:text-plum"
    >
      {children}
    </button>
  );
}

export function MemoryCanvas({
  graph,
  mode,
  selected,
  highlight,
  lineage,
  focus,
  filtered,
  onSelect,
  onExpand,
}: MemoryCanvasProps) {
  const box = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const handlers = useRef({ onSelect, onExpand });
  handlers.current = { onSelect, onExpand };

  const built = useMemo(
    () => canvasElements(graph, mode, focus),
    [graph, mode, focus],
  );
  const key = useMemo(
    () => `${mode}|${built.elements.map((e) => e.data.id).join("|")}`,
    [built, mode],
  );

  useEffect(() => {
    let live = true;
    (async () => {
      const { default: cytoscape } = await import("cytoscape");
      const positions =
        mode === "lineage" && built.nodeCount > 0
          ? await lineagePositions(graph, built.elements).catch(() => null)
          : null;
      if (!live || !box.current) return;
      cy.current?.destroy();
      const inst = cytoscape({
        container: box.current,
        elements: built.elements,
        style: STYLE as never,
        layout: { name: "preset" },
        wheelSensitivity: 0.2,
        minZoom: 0.15,
        maxZoom: 2.5,
        boxSelectionEnabled: false,
        // Selection is the drawer's (the `picked` class), not cytoscape's own tap-to-select.
        autounselectify: true,
      });
      if (mode === "lineage") inst.edges().addClass("lineage-mode");
      const fit = () => {
        inst.resize();
        inst.fit(undefined, 48);
        if (inst.zoom() > 1.3) {
          inst.zoom(1.3);
          inst.center();
        }
      };
      if (positions) {
        inst
          .layout({
            name: "preset",
            positions: (n: { id: () => string }) =>
              positions[n.id()] ?? { x: 0, y: 0 },
            fit: false,
          } as never)
          .run();
        fit();
      } else {
        const layout = inst.layout({
          name: mode === "lineage" ? "breadthfirst" : "cose",
          animate: false,
          fit: false,
          randomize: true,
          nodeRepulsion: () => 9000,
          idealEdgeLength: () => 90,
          gravity: 0.5,
          componentSpacing: 80,
          nodeDimensionsIncludeLabels: true,
          boundingBox: {
            x1: 0,
            y1: 0,
            w: box.current.clientWidth || 900,
            h: box.current.clientHeight || 560,
          },
        } as never);
        layout.one("layoutstop", fit);
        layout.run();
      }
      inst.on("tap", "node", (evt) =>
        handlers.current.onSelect(evt.target.id()),
      );
      inst.on("dbltap", "node", (evt) =>
        handlers.current.onExpand(evt.target.id()),
      );
      cy.current = inst;
      applyClasses(inst, selected, lineage, highlight);
    })();
    return () => {
      live = false;
    };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => cy.current?.destroy(), []);

  // Selection, chain and diff outlines change without a new layout.
  useEffect(() => {
    if (cy.current) applyClasses(cy.current, selected, lineage, highlight);
  }, [selected, lineage, highlight, key]);

  const zoomBy = (f: number) => {
    const inst = cy.current;
    if (!inst) return;
    inst.zoom({
      level: inst.zoom() * f,
      renderedPosition: { x: inst.width() / 2, y: inst.height() / 2 },
    });
  };
  const fitAll = () => cy.current?.fit(undefined, 48);
  const centreSelected = () => {
    const inst = cy.current;
    if (!inst || !selected) return;
    const n = inst.getElementById(selected);
    if (n.nonempty())
      inst.animate(
        { center: { eles: n }, zoom: Math.max(inst.zoom(), 1) },
        { duration: 200 },
      );
  };

  const sides = useMemo(
    () => [...new Set(graph.nodes.map((n) => n.side))],
    [graph],
  );

  return (
    <div className="relative h-full w-full">
      <div
        ref={box}
        className="absolute inset-0"
        data-testid="memory-canvas"
        data-node-count={built.nodeCount}
        data-mode={mode}
      />

      {graph.truncated ? (
        <div
          data-testid="memory-truncated"
          className="absolute left-1/2 top-4 -translate-x-1/2 rounded-full bg-decision-bg px-4 py-1.5 text-[13px] font-medium text-decision shadow-card ring-1 ring-inset ring-decision/20"
        >
          Showing {fmt(graph.nodes.length)} of {fmt(graph.total)}: narrow the
          filters
        </div>
      ) : null}

      {built.nodeCount === 0 ? (
        <div className="absolute inset-0 flex items-center justify-center">
          <p className="max-w-sm text-center text-[14.5px] text-slate">
            Nothing to show here{filtered ? " with these filters" : ""}.{" "}
            {filtered
              ? "Clear a filter or search for a node."
              : "Search for a node to explore around it."}
          </p>
        </div>
      ) : null}

      {/* Legend: the groups present, then what the edge styles mean. */}
      {built.nodeCount > 0 ? (
        <div className="pointer-events-none absolute bottom-4 left-4 flex max-w-[70%] flex-wrap items-center gap-x-4 gap-y-1.5 rounded-card bg-white/90 px-3.5 py-2 text-[12px] text-slate shadow-card ring-1 ring-line">
          {sides.map((s) => (
            <span key={s} className="inline-flex items-center gap-1.5">
              <span
                className="h-2.5 w-2.5 rounded-full ring-1"
                style={{
                  background: GROUP_HEX[s].fill,
                  boxShadow: `inset 0 0 0 1.5px ${GROUP_HEX[s].border}`,
                }}
              />
              {GROUP_LABEL[s]}
            </span>
          ))}
          {graph.edges.some((e) => e.type === "MIGRATED_TO") ? (
            <>
              <span className="inline-flex items-center gap-1.5">
                <span className="h-[3px] w-5 rounded bg-purple" />
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
          {built.indirect > 0 || built.dropped > 0 ? (
            <span
              className="inline-flex items-center gap-1.5"
              data-testid="memory-filtered-hint"
              title="The filters hide nodes these paths run through."
            >
              <span className="h-0 w-5 border-t border-dashed border-line-dk" />
              {built.indirect > 0
                ? `${built.indirect} via hidden nodes`
                : `${built.dropped} edges to hidden nodes`}
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="absolute bottom-4 right-4 flex flex-col divide-y divide-line overflow-hidden rounded-card bg-white shadow-card ring-1 ring-line">
        <ToolButton label="Zoom in" onClick={() => zoomBy(1.25)}>
          <Plus size={16} aria-hidden />
        </ToolButton>
        <ToolButton label="Zoom out" onClick={() => zoomBy(0.8)}>
          <Minus size={16} aria-hidden />
        </ToolButton>
        <ToolButton label="Fit to view" onClick={fitAll}>
          <Maximize size={15} aria-hidden />
        </ToolButton>
        {selected ? (
          <ToolButton
            label="Centre on the selected node"
            onClick={centreSelected}
          >
            <Focus size={15} aria-hidden />
          </ToolButton>
        ) : null}
      </div>
    </div>
  );
}

const fmt = (n: number) =>
  new Intl.NumberFormat("en-US").format(n).replace(/,/g, " ");

function applyClasses(
  inst: Core,
  selected: string | undefined,
  lineage: Subgraph | undefined,
  highlight: CanvasHighlight | undefined,
) {
  inst.batch(() => {
    inst.elements().removeClass("dim chain picked added changed removed");
    if (highlight) {
      for (const k of ["added", "changed", "removed"] as const) {
        for (const uid of highlight[k]) inst.getElementById(uid).addClass(k);
      }
    }
    if (!selected) return;
    const node = inst.getElementById(selected);
    node.addClass("picked");
    // Dim only when there is a chain to show; a node with no lineage just reads as selected.
    const chainNodes = new Set(lineage?.nodes.map((n) => n.uid) ?? []);
    chainNodes.add(selected);
    if (chainNodes.size <= 1) {
      node.connectedEdges().addClass("chain");
      return;
    }
    const chainEdges = new Set(lineage?.edges.map((e) => e.uid) ?? []);
    inst.nodes().forEach((n) => {
      if (!chainNodes.has(n.id())) n.addClass("dim");
    });
    inst.edges().forEach((e) => {
      const inChain =
        chainEdges.has(e.id()) ||
        (chainNodes.has(e.source().id()) &&
          chainNodes.has(e.target().id()) &&
          LINEAGE_TYPES.has(e.data("type")));
      e.addClass(inChain ? "chain" : "dim");
    });
  });
}
