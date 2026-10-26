"use client";

import { DatabaseZap, RotateCw, Sprout } from "lucide-react";
import type { MemoryStatus } from "@/lib/memory";

export type MemoryState = "not_configured" | "unavailable" | "empty";

/** Which single card the explorer shows instead of the canvas, or null when memory is readable. */
export function memoryState(status: MemoryStatus | undefined): MemoryState | null {
  if (!status) return null;
  if (!status.configured) return "not_configured";
  if (!status.available) return "unavailable";
  if (status.revision === null) return "empty";
  return null;
}

const COPY: Record<MemoryState, { title: string; body?: string }> = {
  not_configured: { title: "Memory is not configured for this install." },
  unavailable: {
    title: "Memory is unavailable.",
    body: "Neo4j is not reachable from the platform. Phases keep running and memory catches up when it returns.",
  },
  empty: { title: "No memory yet for this project.", body: "It fills in as soon as the estate is scanned." },
};

/** The one card in place of the explorer: not configured, unreachable (with Retry), or empty. */
export function MemoryUnavailable({ state, onRetry, retrying }: { state: MemoryState; onRetry?: () => void; retrying?: boolean }) {
  const { title, body } = COPY[state];
  const Icon = state === "empty" ? Sprout : DatabaseZap;
  return (
    <section data-testid="memory-unavailable" data-state={state} className="card mx-auto mt-10 max-w-xl px-10 py-12 text-center">
      <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-purple-lt text-purple-dk">
        <Icon size={22} aria-hidden />
      </span>
      <p className="mt-5 text-[17px] font-semibold text-plum">
        {title}
        {body ? " " : null}
        {body ? <span className="mt-2 block text-[15px] font-normal leading-relaxed text-slate">{body}</span> : null}
      </p>
      {state === "unavailable" && onRetry ? (
        <button type="button" className="btn-secondary mt-6" onClick={onRetry} disabled={retrying}>
          <RotateCw size={16} className={retrying ? "animate-spin" : ""} aria-hidden />
          Retry
        </button>
      ) : null}
    </section>
  );
}
