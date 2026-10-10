// Activity presentation by canonical type: colour strip and icon name.
import type { CanvasNodeData } from "./types";

export const STRIP = {
  http: "#9B5DE5",
  jdbc: "#20063B",
  jms: "#FF495C",
  mapper: "#425466",
  end: "#0E7A6E",
  throw: "#A33A32",
  other: "#C9C4B8",
} as const;

export type ActivityFamily = keyof typeof STRIP;

export function family(canonical: string): ActivityFamily {
  if (canonical.startsWith("http") || canonical.startsWith("rest") || canonical.startsWith("soap") || canonical === "reply")
    return "http";
  if (canonical.startsWith("jdbc")) return "jdbc";
  if (canonical.startsWith("jms")) return "jms";
  if (canonical === "mapper") return "mapper";
  if (canonical === "end") return "end";
  if (canonical === "throw") return "throw";
  return "other";
}

/** Start and end markers draw as pills, not cards. */
export function terminalKind(id: string, data: CanvasNodeData, starterId: string | null): "start" | "end" | null {
  if (data.canonical_type === "end") return "end";
  if (data.native_type === "tibex:StartEvent" || (id === starterId && data.canonical_type === "noop")) return "start";
  return null;
}
