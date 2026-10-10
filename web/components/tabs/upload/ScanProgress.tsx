"use client";

import { useEffect, useRef } from "react";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { Pill } from "@/components/ui/Pill";
import type { ScanState } from "@/lib/state";

const STAGES = ["scan", "discover", "persist"];

export function ScanProgress({ scan }: { scan: ScanState }) {
  const logRef = useRef<HTMLOListElement>(null);
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [scan.events.length]);

  const reached = STAGES.indexOf(scan.stage ?? "");
  return (
    <section className="card p-5" data-testid="scan-progress" aria-live="polite">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="panel-title">Scan</h3>
        {scan.status === "running" ? (
          <Pill tone="purple">
            <Loader2 size={12} className="animate-spin" aria-hidden /> running
          </Pill>
        ) : scan.status === "done" ? (
          <Pill tone="gate">
            <CheckCircle2 size={12} aria-hidden /> done
          </Pill>
        ) : (
          <Pill tone="error">
            <XCircle size={12} aria-hidden /> failed
          </Pill>
        )}
        <ol className="ml-auto flex items-center gap-2 text-[13px]">
          {STAGES.map((s, i) => {
            const done = scan.status === "done" || i < reached;
            const current = scan.status === "running" && i === reached;
            return (
              <li
                key={s}
                className={`rounded-full px-2.5 py-0.5 ${
                  done ? "bg-gate-bg text-gate" : current ? "bg-purple-lt text-purple-dk" : "bg-warm text-slate"
                }`}
              >
                {s}
              </li>
            );
          })}
        </ol>
      </div>
      <ol ref={logRef} className="mt-3 max-h-56 overflow-y-auto rounded-btn bg-page p-3 font-mono text-[12.5px]">
        {scan.events
          .filter((e) => e.event === "log" || e.event === "error")
          .map((e, i) => (
            <li key={`${e.seq}-${i}`} className="flex gap-2 py-0.5">
              <span
                className={`shrink-0 rounded px-1.5 text-[11px] font-semibold uppercase ${
                  e.event === "error" ? "bg-error-bg text-error" : e.tag === "ai" ? "bg-ai-bg text-ai" : "bg-det-bg text-det"
                }`}
              >
                {e.event === "error" ? "error" : (e.tag ?? "code")}
              </span>
              <span className={e.event === "error" ? "text-error" : "text-ink"}>{e.text}</span>
            </li>
          ))}
        {scan.events.length === 0 ? <li className="text-slate">Waiting for the job to start.</li> : null}
      </ol>
    </section>
  );
}
