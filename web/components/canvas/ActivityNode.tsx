"use client";

import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import {
  AlertOctagon,
  Braces,
  Circle,
  CircleStop,
  Clock,
  Database,
  FileText,
  Globe,
  Play,
  Reply,
  Send,
  Shuffle,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { family, STRIP } from "@/lib/activity";
import type { CanvasNodeData } from "@/lib/types";

function iconFor(canonical: string): LucideIcon {
  if (canonical === "reply") return Reply;
  if (canonical.startsWith("http") || canonical.startsWith("rest") || canonical.startsWith("soap")) return Globe;
  if (canonical.startsWith("jdbc")) return Database;
  if (canonical.startsWith("jms")) return Send;
  if (canonical === "mapper") return Shuffle;
  if (canonical === "throw") return AlertOctagon;
  if (canonical === "end") return CircleStop;
  if (canonical.startsWith("call")) return Workflow;
  if (canonical === "log") return FileText;
  if (canonical.startsWith("json") || canonical.startsWith("xml")) return Braces;
  if (canonical.startsWith("timer")) return Clock;
  return Circle;
}

export type ActivityFlowNode = Node<CanvasNodeData & { isStarter: boolean }, "activity">;

export const ActivityNode = memo(function ActivityNode({ data }: NodeProps<ActivityFlowNode>) {
  const fam = family(data.canonical_type);
  const Icon = iconFor(data.canonical_type);
  return (
    <div
      className="activity-card relative flex h-[84px] w-[230px] overflow-hidden rounded-node border border-line bg-white shadow-sm"
      data-canonical={data.canonical_type}
    >
      <Handle type="target" position={Position.Left} />
      <span aria-hidden className="w-1 shrink-0" style={{ background: STRIP[fam] }} />
      <div className="min-w-0 flex-1 px-3 py-2">
        <div className="flex items-center gap-1.5">
          <Icon size={15} style={{ color: fam === "other" ? "#425466" : STRIP[fam] }} aria-hidden />
          <span className="truncate text-[13.5px] font-semibold text-plum" title={data.name}>
            {data.name}
          </span>
        </div>
        <p className="mt-0.5 truncate font-mono text-[11px] text-slate" title={data.native_type}>
          {data.native_type}
        </p>
        <div className="mt-1 flex flex-wrap gap-1">
          {data.isStarter ? (
            <span className="inline-flex items-center gap-0.5 rounded bg-purple-lt px-1.5 text-[10.5px] font-medium text-purple-dk">
              <Play size={9} aria-hidden /> starter
            </span>
          ) : null}
          {data.rule_ids.length > 0 ? (
            <span className="rounded bg-ai-bg px-1.5 text-[10.5px] font-medium text-ai" title={data.rule_ids.join(", ")}>
              {data.rule_ids.length} {data.rule_ids.length === 1 ? "rule" : "rules"}
            </span>
          ) : null}
          {data.finding_ids.length > 0 ? (
            <span
              className="rounded bg-decision-bg px-1.5 text-[10.5px] font-medium text-decision"
              title={data.finding_ids.join(", ")}
            >
              {data.finding_ids.length} {data.finding_ids.length === 1 ? "finding" : "findings"}
            </span>
          ) : null}
          {data.unknown ? (
            <span className="rounded bg-error-bg px-1.5 text-[10.5px] font-medium text-error">unknown type</span>
          ) : null}
        </div>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

export type TerminalFlowNode = Node<{ label: string; kind: "start" | "end"; native_type: string }, "terminal">;

export const TerminalNode = memo(function TerminalNode({ data }: NodeProps<TerminalFlowNode>) {
  const end = data.kind === "end";
  return (
    <div
      title={data.native_type}
      className={`terminal-pill flex h-[40px] w-[120px] items-center justify-center rounded-full border-2 text-[13px] font-semibold ${
        end ? "border-ai bg-ai-bg text-ai" : "border-purple bg-purple-lt text-purple-dk"
      }`}
    >
      <Handle type="target" position={Position.Left} />
      {data.label}
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

export type GroupFlowNode = Node<{ label: string; detail?: string; fault?: boolean }, "container">;

export const GroupNode = memo(function GroupNode({ data }: NodeProps<GroupFlowNode>) {
  return (
    <div
      className={`h-full w-full rounded-card border-2 border-dashed ${
        data.fault ? "border-error/50 bg-error-bg/40" : "border-slate/40 bg-warm/50"
      }`}
    >
      <Handle type="target" position={Position.Left} />
      <div className="flex min-w-0 items-center gap-2 px-3 pt-1.5" title={`${data.label} ${data.detail ?? ""}`}>
        <span className={`shrink-0 text-[11px] font-semibold uppercase tracking-wider ${data.fault ? "text-error" : "text-slate"}`}>
          {data.label}
        </span>
        {data.detail ? <span className="truncate font-mono text-[11px] text-slate">{data.detail}</span> : null}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
});
