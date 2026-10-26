// Activity presentation by canonical type: color strip and icon name.
import type { CanvasNodeData } from "./types";

// CSS colours over the theme tokens (lib/theme.css), so the canvas follows the theme on its own.
// No spaces: React Flow builds marker ids from colour strings.
export const STRIP = {
  http: "rgb(var(--c-accent))",
  jdbc: "rgb(var(--c-heading))",
  jms: "rgb(var(--c-coral))",
  mapper: "rgb(var(--c-slate))",
  end: "rgb(var(--c-ai))",
  throw: "rgb(var(--c-error))",
  other: "rgb(var(--c-line-strong))",
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
