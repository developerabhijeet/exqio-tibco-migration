"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { relativeTime, type MemoryStatus } from "@/lib/memory";

/** "Revision 42 · discover · 3 min ago", and "3 ingests pending" while the queue has some. */
export function MemoryStatusPill({ status }: { status: MemoryStatus | undefined }) {
  // Re-render each half minute so "3 min ago" keeps up without a refetch.
  const [, tick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => tick((n) => n + 1), 30_000);
    return () => window.clearInterval(t);
  }, []);

  let text = "Checking memory";
  let tone = "bg-surface text-slate ring-line";
  let dot = "bg-line-strong";
  if (status) {
    if (!status.configured) text = "Not configured";
    else if (!status.available) {
      text = "Unavailable";
      tone = "bg-error-bg text-error ring-error/20";
      dot = "bg-error";
    } else if (status.revision === null) text = "No revisions yet";
    else {
      text = [`Revision ${status.revision}`, status.phase, relativeTime(status.at)].filter(Boolean).join(" · ");
      tone = "bg-surface text-heading ring-line";
      dot = "bg-gate";
    }
  }
  const pending = status?.pending_ingests ?? 0;
  return (
    <div className="flex items-center gap-3">
      <span
        data-testid="memory-status"
        className={`inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 text-md font-medium shadow-card ring-1 ring-inset ${tone}`}
      >
        <span className={`h-2 w-2 rounded-full ${dot}`} aria-hidden />
        {text}
      </span>
      {pending > 0 ? (
        <span className="inline-flex items-center gap-1.5 text-sm text-slate" data-testid="memory-pending">
          <Loader2 size={14} className="animate-spin" aria-hidden />
          {pending} {pending === 1 ? "ingest" : "ingests"} pending
        </span>
      ) : null}
    </div>
  );
}
