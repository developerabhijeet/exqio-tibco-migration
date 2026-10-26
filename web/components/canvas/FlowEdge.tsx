"use client";

import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type Edge, type EdgeProps } from "@xyflow/react";

export type FlowEdgeData = { kind: string; expression: string | null; label: string | null };
export type FlowEdgeType = Edge<FlowEdgeData, "flow">;

// Theme tokens as CSS colours (strokes and arrow markers follow the theme without a re-render).
// No spaces: React Flow builds each marker's id from its colour.
export const EDGE_COLORS: Record<string, string> = {
  success: "rgb(var(--c-slate)/0.7)",
  conditional: "rgb(var(--c-decision))",
  otherwise: "rgb(var(--c-slate))",
  error: "rgb(var(--c-error))",
};

export function FlowEdge(props: EdgeProps<FlowEdgeType>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd } = props;
  const [path] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 12,
    offset: 22,
  });
  // The label sits on the edge's last run, just before the arrow: every branch out of one step
  // ends at a different target, so their pills never stack on the shared fork.
  const lx = targetX - 18;
  const ly = targetY;
  const kind = data?.kind ?? "success";
  const color = EDGE_COLORS[kind] ?? EDGE_COLORS.success;
  const dashed = kind === "otherwise" || kind === "error";
  const text = kind === "conditional" ? (data?.expression ?? data?.label ?? "") : kind === "success" ? "" : (data?.label ?? kind);
  // Short pills in the gap between layers; the full expression shows on hover.
  const pill = kind === "conditional" ? (text.length > 14 ? `${text.slice(0, 13)}…` : text) : text;
  return (
    <>
      <BaseEdge
        path={path}
        markerEnd={markerEnd}
        style={{ stroke: color, strokeWidth: kind === "success" ? 1.5 : 1.8, strokeDasharray: dashed ? "6 4" : undefined }}
      />
      {text ? (
        <EdgeLabelRenderer>
          <div
            data-edge-kind={kind}
            aria-label={kind === "conditional" ? `condition: ${text}` : text}
            className="nodrag nopan group pointer-events-auto absolute hover:z-50"
            style={{ transform: `translate(-100%, -50%) translate(${lx}px, ${ly}px)` }}
          >
            <span
              className={`inline-flex max-w-[112px] items-center gap-1 whitespace-nowrap rounded-full border px-2 py-px text-2xs leading-4 shadow-e1 ${
                kind === "conditional"
                  ? "border-decision/40 bg-decision-bg font-mono text-decision"
                  : kind === "error"
                    ? "border-error/30 bg-error-bg text-error"
                    : "border-line-strong bg-surface italic text-slate"
              }`}
            >
              {kind === "conditional" ? <span className="font-sans font-semibold not-italic">if</span> : null}
              <span className="truncate">{pill}</span>
            </span>
            {kind === "conditional" ? (
              <span
                role="tooltip"
                className="pointer-events-none absolute right-0 top-full z-50 mt-1 hidden w-max max-w-[360px] whitespace-pre-wrap break-words rounded-btn border border-decision/40 bg-surface px-2 py-1 font-mono text-2xs text-decision shadow-lg group-hover:block"
              >
                {text}
              </span>
            ) : null}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}
