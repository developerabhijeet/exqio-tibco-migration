"use client";

// Analysis and design jobs streamed over SSE, kept above the tabs so a run survives switching
// steps. One stream per (kind, process); a finished job refreshes that process's views.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { subscribeJob } from "./sse";
import type { JobEvent } from "./types";

export type JobKind = "analyse" | "design";

export interface JobStream {
  kind: JobKind;
  processId: string;
  jobId: string;
  status: "running" | "done" | "failed";
  stage: string | null;
  events: JobEvent[];
  error: string | null;
  result: Record<string, unknown> | null;
}

interface JobsState {
  streams: Record<string, JobStream>;
  watch: (kind: JobKind, processId: string, jobId: string) => void;
}

const Ctx = createContext<JobsState | null>(null);
const key = (kind: JobKind, processId: string) => `${kind}:${processId}`;

export function JobsProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [streams, setStreams] = useState<Record<string, JobStream>>({});
  const subs = useRef<Record<string, () => void>>({});

  const watch = useCallback(
    (kind: JobKind, processId: string, jobId: string) => {
      const k = key(kind, processId);
      subs.current[k]?.();
      setStreams((all) => ({
        ...all,
        [k]: { kind, processId, jobId, status: "running", stage: null, events: [], error: null, result: null },
      }));
      subs.current[k] = subscribeJob(jobId, (ev) => {
        setStreams((all) => {
          const s = all[k];
          if (!s || s.jobId !== jobId) return all;
          if (ev.seq > 0 && s.events.some((e) => e.seq === ev.seq)) return all;
          const next: JobStream = { ...s, events: [...s.events, ev] };
          if (ev.event === "stage") next.stage = ev.text;
          if (ev.event === "done") {
            next.status = "done";
            next.result = ev.data;
          }
          if (ev.event === "error") {
            next.status = "failed";
            next.error = ev.text;
          }
          return { ...all, [k]: next };
        });
        if (ev.event === "done" || ev.event === "error") {
          for (const q of ["analysis", "gates", "audit", "design", "canvas"]) {
            qc.invalidateQueries({ queryKey: [q, processId] });
          }
          qc.invalidateQueries({ queryKey: ["processes"] });
        }
      });
    },
    [qc],
  );

  useEffect(() => {
    const current = subs.current;
    return () => Object.values(current).forEach((u) => u());
  }, []);

  const value = useMemo(() => ({ streams, watch }), [streams, watch]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useJob(kind: JobKind, processId: string | null): {
  stream: JobStream | null;
  watch: (jobId: string) => void;
} {
  const v = useContext(Ctx);
  if (!v) throw new Error("useJob outside JobsProvider");
  const { streams, watch } = v;
  const stream = processId ? (streams[key(kind, processId)] ?? null) : null;
  const start = useCallback((jobId: string) => processId && watch(kind, processId, jobId), [watch, kind, processId]);
  return { stream, watch: start };
}
