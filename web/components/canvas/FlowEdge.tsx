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
  const short = text.length > 30 ? `${text.slice(0, 28)}…` : text;
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
            title={text}
            data-edge-kind={kind}
            className={`nodrag nopan pointer-events-auto absolute max-w-[200px] truncate rounded px-1.5 py-0.5 text-[10.5px] ${
              kind === "conditional"
                ? "border border-decision/30 bg-decision-bg font-mono text-decision"
                : kind === "error"
                  ? "bg-error-bg text-error"
                  : "bg-page italic text-slate"
            }`}
            style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}
          >
            {short}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}
