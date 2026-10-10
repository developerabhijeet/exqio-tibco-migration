"use client";

import { useEffect, useRef } from "react";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { Pill } from "@/components/ui/Pill";
import type { JobStream } from "@/lib/jobs";

/** Lets a tab state the result of the work a finished job did (a build can fail inside a job that ended `done`). */
export interface JobOutcomeOverride {
  /** Replaces the job's status pill. */
  pill?: { tone: "gate" | "error" | "decision" | "det"; text: string };
  /** The tone of one stage chip, or null to keep the job's own. */
  stage?: (stage: string) => "passed" | "failed" | "skipped" | null;
}

const STAGE_TONE: Record<"passed" | "failed" | "skipped", string> = {
  passed: "bg-gate-bg text-gate",
  failed: "bg-error-bg text-error",
  skipped: "bg-warm text-slate",
};

/** A job's streamed log: stage chips, then one line per event tagged `code` or `ai`. */
export function JobLog({
  title,
  stream,
  stages,
  testId,
  idle,
  outcome,
}: {
  title: string;
  stream: JobStream | null;
  stages: string[];
  testId: string;
  idle: React.ReactNode;
  /** Only applied once the job has finished. */
  outcome?: JobOutcomeOverride | null;
}) {
  const logRef = useRef<HTMLOListElement>(null);
  const n = stream?.events.length ?? 0;
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [n]);

  const reached = stages.indexOf(stream?.stage ?? "");
  const lines = (stream?.events ?? []).filter((e) => e.event === "log" || e.event === "error");
  const over = stream && stream.status !== "running" ? (outcome ?? null) : null;
  return (
    <section className="card p-4" data-testid={testId} aria-live="polite">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[16px] font-semibold text-plum">{title}</h3>
        {stream ? (
          over?.pill ? (
            <Pill tone={over.pill.tone} testId={`${testId}-outcome`}>
              {over.pill.tone === "error" ? <XCircle size={12} aria-hidden /> : <CheckCircle2 size={12} aria-hidden />}{" "}
              {over.pill.text}
            </Pill>
          ) : stream.status === "running" ? (
            <Pill tone="purple">
              <Loader2 size={12} className="animate-spin" aria-hidden /> running
            </Pill>
          ) : stream.status === "done" ? (
            <Pill tone="gate">
              <CheckCircle2 size={12} aria-hidden /> done
            </Pill>
          ) : (
            <Pill tone="error">
              <XCircle size={12} aria-hidden /> failed
            </Pill>
          )
        ) : null}
      </div>
      {stream ? (
        <ol className="mt-2 flex flex-wrap gap-1.5 text-[12px]">
          {stages.map((s, i) => {
            const done = stream.status === "done" || i < reached;
            const current = stream.status === "running" && i === reached;
            const forced = over?.stage?.(s) ?? null;
            return (
              <li
                key={s}
                data-stage={s}
                data-tone={forced ?? (done ? "passed" : current ? "running" : "pending")}
                className={`rounded-full px-2 py-0.5 ${
                  forced
                    ? STAGE_TONE[forced]
                    : done
                      ? "bg-gate-bg text-gate"
                      : current
                        ? "bg-purple-lt text-purple-dk"
                        : "bg-warm text-slate"
                }`}
              >
                {s}
              </li>
            );
          })}
        </ol>
      ) : null}
      <ol ref={logRef} className="mt-3 max-h-72 overflow-y-auto rounded-btn bg-page p-3 font-mono text-[12px] leading-5">
        {lines.map((e, i) => (
          <li key={`${e.seq}-${i}`} className="flex gap-2 py-0.5">
            <span
              className={`h-fit shrink-0 rounded px-1.5 text-[10.5px] font-semibold uppercase ${
                e.event === "error" ? "bg-error-bg text-error" : e.tag === "ai" ? "bg-ai-bg text-ai" : "bg-det-bg text-det"
              }`}
            >
              {e.event === "error" ? "error" : (e.tag ?? "code")}
            </span>
            <span className={`break-words ${e.event === "error" ? "text-error" : "text-ink"}`}>{e.text}</span>
          </li>
        ))}
        {stream?.status === "failed" && stream.error && !lines.some((e) => e.event === "error") ? (
          <li className="flex gap-2 py-0.5">
            <span className="h-fit shrink-0 rounded bg-error-bg px-1.5 text-[10.5px] font-semibold uppercase text-error">error</span>
            <span className="text-error">{stream.error}</span>
          </li>
        ) : null}
        {!stream ? <li className="font-sans text-[13px] text-slate">{idle}</li> : null}
        {stream && lines.length === 0 ? <li className="text-slate">Waiting for the job to start.</li> : null}
      </ol>
    </section>
  );
}
