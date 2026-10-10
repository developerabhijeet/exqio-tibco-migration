"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { JobLog, type JobOutcomeOverride } from "@/components/jobs/JobLog";
import type { JobStream } from "@/lib/jobs";
import { JOB_STAGES, stepOfStage, type BuildStepRow } from "@/lib/buildSteps";
import { OutcomePill, TagPill } from "./Panel";

/**
 * When the job ended but the build failed, the card says so and the stage chips take the color
 * of their step rows (restore failed red, build and test skipped gray); otherwise null.
 */
export function failedBuildOutcome(buildStatus: string | null | undefined, rows: BuildStepRow[]): JobOutcomeOverride | null {
  if (buildStatus !== "failed") return null;
  return {
    pill: { tone: "error", text: "job finished, build failed" },
    stage: (stage) => {
      const name = stepOfStage(stage);
      const row = name ? rows.find((r) => r.name === name) : undefined;
      return row?.status === "failed" ? "failed" : row?.status === "skipped" ? "skipped" : null;
    },
  };
}

/** The build job's live log with stage markers. */
export function BuildJobLog({ stream, outcome }: { stream: JobStream | null; outcome?: JobOutcomeOverride | null }) {
  return (
    <JobLog
      title="Build job"
      stream={stream}
      stages={JOB_STAGES}
      outcome={outcome}
      testId="build-log"
      idle="No build yet. Generate and build writes the solution, the tests and the infrastructure, then runs dotnet."
    />
  );
}

function StepRow({ row }: { row: BuildStepRow }) {
  const [open, setOpen] = useState(false);
  const failed = row.status === "failed";
  return (
    <li data-step={row.name} data-status={row.status} className={failed ? "bg-error-bg/40" : ""}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2.5 px-3 py-2 text-left hover:bg-warm"
      >
        <ChevronRight size={14} className={`shrink-0 text-slate transition-transform ${open ? "rotate-90" : ""}`} aria-hidden />
        <TagPill tag={row.tag} />
        <span className="w-[170px] shrink-0 text-[14px] font-medium text-plum">{row.label}</span>
        <OutcomePill outcome={row.status} text={row.statusText} />
        <span className={`min-w-0 flex-1 truncate text-[13px] ${failed ? "text-error" : "text-slate"}`} title={row.summary}>
          {row.summary}
        </span>
      </button>
      {open ? (
        <pre
          data-testid="step-log"
          className="mx-3 mb-3 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-btn bg-page p-3 font-mono text-[12px] leading-5 text-ink"
        >
          {row.log || "No log for this step."}
        </pre>
      ) : null}
    </li>
  );
}

/** One row per build step with its pass or fail pill; each row opens its log. */
export function BuildSteps({ rows }: { rows: BuildStepRow[] }) {
  const passed = rows.filter((r) => r.status === "passed").length;
  const failed = rows.filter((r) => r.status === "failed").length;
  return (
    <section className="card p-4" data-testid="build-steps">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[16px] font-semibold text-plum">Steps</h3>
        <span className="text-[13px] text-slate">
          {passed} passed, {failed} failed, {rows.length - passed - failed} other
        </span>
      </div>
      <ol className="mt-2 divide-y divide-line overflow-hidden rounded-btn border border-line">
        {rows.map((r) => (
          <StepRow key={r.name} row={r} />
        ))}
      </ol>
    </section>
  );
}
