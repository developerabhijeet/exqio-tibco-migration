"use client";

// The wizard's forward move. The stepper above stays freely clickable (an SME often looks back at
// Discover while deciding a rule); this is the one control that says whether the step you are on is
// actually finished, and what is left when it is not. "Finished" is the step's own status, the same
// one the stepper dot shows: approved (its gates are signed off) or complete (Upload and Discover,
// which have no gate). Anything else keeps the button disabled and shows the reason the gate itself
// gives, so the answer to "why can I not go on?" is here rather than three panels away.
import { ArrowRight, CheckCircle2 } from "lucide-react";
import type { StepState } from "@/lib/types";

const DONE: StepState["status"][] = ["approved", "complete"];

export function isStepDone(step: StepState | undefined): boolean {
  return !!step && DONE.includes(step.status);
}

/** Why the step is not done yet, in the gate's own words where it has them. */
function blockedReason(step: StepState): string {
  if (step.status === "running") return "This step is still running.";
  if (step.status === "failed") return "This step failed; fix the failure before moving on.";
  if (step.status === "not_started") return "Nothing has run for this step yet.";
  // needs_review: the gate rows carry "pending: <reason>", which names the real blocker
  const pending = step.evidence
    .map((e) => e.detail ?? "")
    .filter((d) => d.startsWith("pending: "))
    .map((d) => d.slice("pending: ".length));
  if (pending.length > 0) return pending.join(" · ");
  return "Decide this step's gate before moving on.";
}

export function NextStep({
  steps,
  active,
  onGo,
}: {
  steps: StepState[];
  active: number;
  onGo: (index: number) => void;
}) {
  const step = steps[active];
  const next = steps[active + 1];
  if (!step) return null;

  const done = isStepDone(step);

  if (!next) {
    return (
      <div className="card flex items-center gap-2 px-4 py-3 text-[14px]">
        {done ? (
          <>
            <CheckCircle2 size={18} className="text-gate" aria-hidden />
            <span className="font-semibold text-plum">{step.label} is the last step, and it is done.</span>
          </>
        ) : (
          <span className="text-slate">
            <span className="font-semibold text-plum">{step.label}</span> is the last step. {blockedReason(step)}
          </span>
        )}
      </div>
    );
  }

  return (
    <div
      data-testid="next-step"
      data-ready={done ? "yes" : "no"}
      className="card flex flex-wrap items-center justify-between gap-3 px-4 py-3"
    >
      <p className="text-[14px] text-slate">
        {done ? (
          <>
            <CheckCircle2 size={16} className="mr-1.5 inline align-text-bottom text-gate" aria-hidden />
            <span className="font-semibold text-plum">{step.label}</span> is done. Next is {next.label}.
          </>
        ) : (
          <>
            <span className="font-semibold text-plum">{step.label}</span> is not finished yet.{" "}
            <span data-testid="next-step-reason">{blockedReason(step)}</span>
          </>
        )}
      </p>
      <button
        type="button"
        className="btn-primary"
        data-testid="next-step-button"
        disabled={!done}
        title={done ? `Go to ${next.label}` : blockedReason(step)}
        onClick={() => onGo(active + 1)}
      >
        Next: {next.label} <ArrowRight size={16} aria-hidden />
      </button>
    </div>
  );
}
