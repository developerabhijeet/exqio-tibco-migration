"use client";

import {
  Check,
  CheckCircle2,
  Eye,
  Hammer,
  Loader2,
  PencilRuler,
  Rocket,
  Search,
  ShieldCheck,
  Upload,
  X,
  type LucideIcon,
} from "lucide-react";
import { StatusPill } from "@/components/ui/Pill";
import { blockedReason, canActOn, personaOf } from "@/lib/personas";
import { useAppState, type StepKey } from "@/lib/state";
import type { StepState } from "@/lib/types";

const DOT: Record<StepState["status"], string> = {
  not_started: "border-line bg-white text-slate",
  running: "border-purple bg-purple-lt text-purple",
  needs_review: "border-decision bg-decision-bg text-decision",
  complete: "border-det bg-det-bg text-det",
  approved: "border-gate bg-gate text-white",
  failed: "border-error bg-error text-white",
};

/** One icon per step, so a glance at the bar says what the step is, not only where it sits. */
const STEP_ICON: Record<string, LucideIcon> = {
  upload: Upload,
  discover: Search,
  validate: CheckCircle2,
  design: PencilRuler,
  build: Hammer,
  verify: ShieldCheck,
  deploy: Rocket,
};

function Dot({ index, status }: { index: number; status: StepState["status"] }) {
  return (
    <span
      aria-hidden
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 text-[13px] font-semibold ${DOT[status]}`}
    >
      {status === "approved" || status === "complete" ? (
        <Check size={16} strokeWidth={3} />
      ) : status === "failed" ? (
        <X size={16} strokeWidth={3} />
      ) : status === "running" ? (
        <Loader2 size={16} className="animate-spin" />
      ) : (
        index + 1
      )}
    </span>
  );
}

export function Stepper({
  steps,
  active,
  onSelect,
}: {
  steps: StepState[];
  active: number;
  onSelect: (index: number) => void;
}) {
  // Which steps the current persona may act in. A step that is not theirs stays open to read, so it
  // is never disabled; it carries an eye and a "view only" marker instead, and the reason is the
  // tooltip. That way the bar says what this user can do before they select anything.
  const { user } = useAppState();
  const persona = personaOf(user);

  return (
    <nav aria-label="Migration steps" className="card overflow-x-auto p-2 shadow-card">
      <ol className="flex min-w-[1180px] items-stretch">
        {steps.map((s, i) => {
          const isActive = i === active;
          const mine = canActOn(persona, s.key as StepKey);
          const reason = blockedReason(persona, s.key as StepKey) ?? undefined;
          return (
            <li key={s.key} className="flex flex-1 items-center">
              <div
                className={`group flex flex-1 items-center gap-1 rounded-[10px] px-2 py-2 transition-colors ${
                  isActive ? "bg-purple-lt" : "hover:bg-warm"
                }`}
              >
                <button
                  type="button"
                  aria-current={isActive ? "step" : undefined}
                  data-testid={`step-${s.key}`}
                  data-view-only={mine ? undefined : "true"}
                  title={reason}
                  onClick={() => onSelect(i)}
                  className="flex flex-1 items-center gap-2.5 rounded-[8px] text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple/60"
                >
                  <Dot index={i} status={s.status} />
                  <span className={`min-w-0 ${mine ? "" : "opacity-70"}`}>
                    <span
                      className={`flex items-center gap-1.5 text-step font-semibold ${
                        isActive ? "text-purple" : "text-plum"
                      }`}
                    >
                      {(() => {
                        const Icon = STEP_ICON[s.key];
                        return Icon ? <Icon size={15} aria-hidden className="shrink-0" /> : null;
                      })()}
                      <span className="truncate">{s.label}</span>
                      {mine ? null : <Eye size={13} aria-hidden className="shrink-0 text-slate" />}
                    </span>
                    <span className="flex flex-col items-start gap-0.5">
                      <StatusPill status={s.status} />
                      {mine ? null : (
                        <span className="whitespace-nowrap text-[10.5px] font-semibold uppercase tracking-[0.06em] text-slate">
                          view only
                        </span>
                      )}
                    </span>
                  </span>
                </button>
              </div>
              {i < steps.length - 1 ? <span aria-hidden className="mx-1 h-px w-4 shrink-0 bg-line" /> : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
