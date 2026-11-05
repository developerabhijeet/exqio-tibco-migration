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
  Layers,
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

/** The family's colour token, for the tinted icon tile and the accent bar. */
const TILE: Record<string, string> = {
  http: "accent",
  jdbc: "heading",
  jms: "coral-dk",
  mapper: "slate",
  end: "ai",
  throw: "error",
  other: "slate",
};

export const ActivityNode = memo(function ActivityNode({ data }: NodeProps<ActivityFlowNode>) {
  const fam = family(data.canonical_type);
  const Icon = iconFor(data.canonical_type);
  const token = TILE[fam] ?? "slate";
  return (
    <div
      className="activity-card group relative flex h-[84px] w-[230px] overflow-hidden rounded-node border border-line bg-surface shadow-e1 transition-[box-shadow,transform] duration-fast hover:-translate-y-px hover:shadow-e2"
      data-canonical={data.canonical_type}
    >
      <Handle type="target" position={Position.Left} />
      <span aria-hidden className="w-[3px] shrink-0" style={{ background: STRIP[fam] }} />
      <div className="flex min-w-0 flex-1 gap-2.5 py-2 pl-2.5 pr-3">
        <span
          aria-hidden
          className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-btn"
          style={{ background: `rgb(var(--c-${token})/0.12)`, color: `rgb(var(--c-${token}))` }}
        >
          <Icon size={16} strokeWidth={2} />
        </span>
        <div className="min-w-0 flex-1">
          <span className="block truncate text-md font-semibold leading-5 text-heading" title={data.name}>
            {data.name}
          </span>
          <p className="truncate font-mono text-2xs text-slate" title={data.native_type}>
            {data.native_type}
          </p>
          <div className="mt-1 flex flex-wrap gap-1">
            {data.isStarter ? (
              <span className="inline-flex items-center gap-0.5 rounded-full bg-accent-soft px-1.5 text-2xs font-medium text-accent-ink">
                <Play size={9} aria-hidden /> starter
              </span>
            ) : null}
            {data.rule_ids.length > 0 ? (
              <span className="rounded-full bg-ai-bg px-1.5 text-2xs font-medium text-ai" title={data.rule_ids.join(", ")}>
                {data.rule_ids.length} {data.rule_ids.length === 1 ? "rule" : "rules"}
              </span>
            ) : null}
            {data.finding_ids.length > 0 ? (
              <span
                className="rounded-full bg-decision-bg px-1.5 text-2xs font-medium text-decision"
                title={data.finding_ids.join(", ")}
              >
                {data.finding_ids.length} {data.finding_ids.length === 1 ? "finding" : "findings"}
              </span>
            ) : null}
            {data.unknown ? (
              <span className="rounded-full bg-error-bg px-1.5 text-2xs font-medium text-error">unknown type</span>
            ) : null}
          </div>
        </div>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

export type TerminalFlowNode = Node<{ label: string; kind: "start" | "end"; native_type: string }, "terminal">;

export const TerminalNode = memo(function TerminalNode({ data }: NodeProps<TerminalFlowNode>) {
  const end = data.kind === "end";
  const Icon = end ? CircleStop : Play;
  return (
    <div
      title={data.native_type}
      className={`terminal-pill flex h-[40px] w-[120px] items-center justify-center gap-1.5 rounded-full border text-sm font-semibold shadow-e1 ${
        end ? "border-ai/50 bg-ai-bg text-ai" : "border-accent/50 bg-accent-soft text-accent-ink"
      }`}
    >
      <Handle type="target" position={Position.Left} />
      <Icon size={14} aria-hidden />
      {data.label}
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

export type GroupFlowNode = Node<{ label: string; detail?: string; fault?: boolean }, "container">;

export const GroupNode = memo(function GroupNode({ data }: NodeProps<GroupFlowNode>) {
  return (
    <div
      className={`h-full w-full rounded-card border border-dashed ${
        data.fault ? "border-error/35 bg-error-bg/25" : "border-line-strong bg-sunken/40"
      }`}
    >
      <Handle type="target" position={Position.Left} />
      <div className="flex min-w-0 items-center gap-2 px-3 pt-2" title={`${data.label} ${data.detail ?? ""}`}>
        <span
          className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 text-2xs font-semibold uppercase leading-5 tracking-wider ${
            data.fault ? "bg-error-bg text-error" : "bg-surface text-slate ring-1 ring-inset ring-line"
          }`}
        >
          {data.fault ? <AlertOctagon size={10} aria-hidden /> : <Layers size={10} aria-hidden />}
          {data.label}
        </span>
        {data.detail ? <span className="truncate font-mono text-2xs text-slate">{data.detail}</span> : null}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
});
