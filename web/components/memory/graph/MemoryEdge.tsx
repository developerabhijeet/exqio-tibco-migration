"use client";

// A memory relationship: a smooth step between top-level cards (a bezier inside a container),
// coloured by relation, with a pill label. Structural relations (contains, about) stay unlabelled
// until an end is picked or hovered; the lit neighbourhood's edges are drawn strong and flow.
import { memo, useContext } from "react";
import { BaseEdge, EdgeLabelRenderer, getBezierPath, getSmoothStepPath, type EdgeProps } from "@xyflow/react";
import { CanvasStateContext } from "./MemoryNodes";
import { QUIET_RELS, relColor, relLabel, tok } from "./kinds";
import type { MemFlowEdge } from "./layout";

export const MemoryEdge = memo(function MemoryEdge(props: EdgeProps<MemFlowEdge>) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd } = props;
  const s = useContext(CanvasStateContext);
  const lit = s.litEdges?.has(id) ?? false;
  if (data?.internal && !lit) return null;
  const dim = s.litEdges !== null && !lit;
  const type = data?.type ?? "";
  const [path, lx, ly] = data?.internal
    ? getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })
    : getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 14, offset: 18 });
  const diff = s.added.has(id) ? tok("gate") : s.changed.has(id) ? tok("decision") : s.removed.has(id) ? tok("error") : null;
  const colour = diff ?? relColor(type, data?.status);
  const migrated = type === "MIGRATED_TO";
  const dashed = data?.indirect || data?.status === "rejected" || s.removed.has(id);
  const width = (migrated ? 2.6 : 1.4) + (lit ? 0.8 : 0) + (diff ? 0.8 : 0);
  const showLabel = !dim && (lit || !QUIET_RELS.has(type));
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={data?.indirect ? undefined : markerEnd}
        className={lit && !dashed ? "mem-edge-live" : undefined}
        style={{
          stroke: colour,
          strokeWidth: width,
          strokeDasharray: dashed ? "4 4" : undefined,
          opacity: dim ? 0.14 : 1,
          transition: "opacity var(--dur-fast) var(--ease)",
        }}
      />
      {showLabel ? (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan pointer-events-none absolute"
            style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)`, zIndex: lit ? 10 : 1 }}
          >
            <span
              className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border bg-surface px-2 text-2xs font-medium leading-4 shadow-e1 ${
                lit ? "border-line-strong text-heading" : "border-line text-slate"
              }`}
            >
              <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: colour }} />
              {data?.status && migrated ? `${relLabel(type)} · ${data.status}` : relLabel(type)}
            </span>
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
});

export const memoryEdgeTypes = { mem: MemoryEdge };
