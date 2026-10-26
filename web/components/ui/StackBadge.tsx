"use client";

import { Pill } from "@/components/ui/Pill";
import { shortLabel, useProjectStack, useStacks } from "@/lib/stacks";

/** The target stack of a project, design or build as a short label (C#, Java 21, Go, Python 3.12). */
export function StackBadge({ stack }: { stack: string | undefined }) {
  const stacks = useStacks();
  if (!stack) return null;
  const entry = stacks.data?.stacks.find((s) => s.key === stack);
  return (
    <Pill tone="purple" testId="stack-badge" title={entry?.label}>
      {entry ? shortLabel(entry.label) : stack}
    </Pill>
  );
}

/** Shown when the record on screen (a design or build) is for another stack than the project's now. */
export function StackNote({ recordStack, kind }: { recordStack: string | undefined; kind: "design" | "build" }) {
  const projectStack = useProjectStack();
  const stacks = useStacks();
  if (!recordStack || !projectStack || recordStack === projectStack) return null;
  const name = (k: string) => {
    const e = stacks.data?.stacks.find((s) => s.key === k);
    return e ? shortLabel(e.label) : k;
  };
  return (
    <p role="status" data-testid="stack-note" className="rounded-card border border-line bg-warm px-3 py-2 text-[13.5px] text-ink">
      {kind === "build" ? "Built" : "Designed"} for {name(recordStack)}; the project now targets {name(projectStack)}.
      Re-run Design for {name(projectStack)}{kind === "build" ? " and build again" : ""}.
    </p>
  );
}
