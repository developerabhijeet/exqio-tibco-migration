"use client";

import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, type Edge, type EdgeProps } from "@xyflow/react";

export type FlowEdgeData = { kind: string; expression: string | null; label: string | null };
export type FlowEdgeType = Edge<FlowEdgeData, "flow">;

export const EDGE_COLORS: Record<string, string> = {
  success: "#8C8676",
  conditional: "#9A5E0A",
  otherwise: "#425466",
  error: "#A33A32",
};

export function FlowEdge(props: EdgeProps<FlowEdgeType>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd } = props;
  const [path, lx, ly] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 10,
  });
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
        style={{ stroke: color, strokeWidth: 1.6, strokeDasharray: dashed ? "6 4" : undefined }}
      />
      {text ? (
        <EdgeLabelRenderer>
          <div
            data-edge-kind={kind}
            aria-label={kind === "conditional" ? `condition: ${text}` : text}
            className="nodrag nopan group pointer-events-auto absolute hover:z-50"
            style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}
          >
            <span
              className={`inline-flex max-w-[110px] items-center gap-1 whitespace-nowrap rounded-full border px-1.5 py-px text-[10px] leading-4 shadow-sm ${
                kind === "conditional"
                  ? "border-decision/40 bg-decision-bg font-mono text-decision"
                  : kind === "error"
                    ? "border-error/30 bg-error-bg text-error"
                    : "border-line bg-white italic text-slate"
              }`}
            >
              {kind === "conditional" ? <span className="font-sans font-semibold not-italic">if</span> : null}
              <span className="truncate">{pill}</span>
            </span>
            {kind === "conditional" ? (
              <span
                role="tooltip"
                className="pointer-events-none absolute left-1/2 top-full z-50 mt-1 hidden w-max max-w-[360px] -translate-x-1/2 whitespace-pre-wrap break-words rounded-btn border border-decision/40 bg-white px-2 py-1 font-mono text-[11px] text-decision shadow-lg group-hover:block"
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
