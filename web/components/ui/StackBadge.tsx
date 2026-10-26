"use client";

import { Pill } from "@/components/ui/Pill";
import { shortLabel, useStacks } from "@/lib/stacks";

/** The target stack of a project or build, as a short label (C#, Java 21, Go, Python 3.12). */
export function StackBadge({ stack }: { stack: string }) {
  const stacks = useStacks();
  const entry = stacks.data?.stacks.find((s) => s.key === stack);
  return (
    <Pill tone="purple" testId="stack-badge" title={entry?.label}>
      {entry ? shortLabel(entry.label) : stack}
    </Pill>
  );
}
