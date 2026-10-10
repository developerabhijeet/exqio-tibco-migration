"use client";

// Analysis and design jobs streamed over SSE, kept above the tabs so a run survives switching
// steps. One stream per (kind, process); a finished job refreshes that process's views. A running
// job's id is kept in localStorage so the stream re-attaches after a page reload (as the scan does).
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { subscribeJob } from "./sse";
import type { JobEvent } from "./types";

export type JobKind = "analyse" | "design" | "build" | "verify" | "plan" | "eval";

export interface JobStream {
  kind: JobKind;
  processId: string;
  jobId: string;
  status: "running" | "done" | "failed"; // a lost connection reads "failed" with that error
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
const storeKey = (kind: JobKind, processId: string) => `exiqo.job.${kind}.${processId}`;

function stored(k: string): string | null {
  try {
    return window.localStorage.getItem(k);
  } catch {
    return null;
  }
}

function store(k: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(k);
    else window.localStorage.setItem(k, value);
  } catch {
    /* storage unavailable: no re-attach after a reload */
  }
}

export function JobsProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [streams, setStreams] = useState<Record<string, JobStream>>({});
  const subs = useRef<Record<string, () => void>>({});

  const watch = useCallback(
    (kind: JobKind, processId: string, jobId: string) => {
      const k = key(kind, processId);
      subs.current[k]?.();
      store(storeKey(kind, processId), jobId);
      setStreams((all) => ({
        ...all,
        [k]: { kind, processId, jobId, status: "running", stage: null, events: [], error: null, result: null },
      }));
      subs.current[k] = subscribeJob(
        jobId,
        (ev) => {
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
          store(storeKey(kind, processId), null);
          for (const q of ["analysis", "gates", "audit", "design", "canvas"]) {
            qc.invalidateQueries({ queryKey: [q, processId] });
          }
          qc.invalidateQueries({ queryKey: ["processes"] });
          // Build, verify and plan runs feed the stepper even when their tab is not open.
          qc.invalidateQueries({ queryKey: ["builds", processId] });
          for (const q of ["build", "parity", "plan"]) qc.invalidateQueries({ queryKey: [q] });
        }
        },
        {
          onLost: () =>
            setStreams((all) => {
              const s = all[k];
              if (!s || s.jobId !== jobId) return all;
              return { ...all, [k]: { ...s, status: "failed", error: "connection to the API lost; reload to re-attach" } };
            }),
        },
      );
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
  const attached = !!stream;

  // After a reload, re-attach to this kind of job for this process if it is still queued or running.
  useEffect(() => {
    if (!processId || attached) return;
    const jobId = stored(storeKey(kind, processId));
    if (!jobId) return;
    let live = true;
    api
      .job(jobId)
      .then((job) => {
        if (!live) return;
        if (job.status === "queued" || job.status === "running") watch(kind, processId, jobId);
        else store(storeKey(kind, processId), null);
      })
      .catch(() => store(storeKey(kind, processId), null));
    return () => {
      live = false;
    };
  }, [kind, processId, attached, watch]);
  return { stream, watch: start };
}
