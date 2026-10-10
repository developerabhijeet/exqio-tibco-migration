"use client";

import { useState } from "react";
import { Check, FileSearch, Loader2, X } from "lucide-react";
import { EvidenceDrawer } from "@/components/evidence/EvidenceDrawer";
import { StatusPill } from "@/components/ui/Pill";
import type { StepState } from "@/lib/types";

const DOT: Record<StepState["status"], string> = {
  not_started: "border-line bg-white text-slate",
  running: "border-purple bg-purple-lt text-purple",
  needs_review: "border-decision bg-decision-bg text-decision",
  approved: "border-gate bg-gate text-white",
  failed: "border-error bg-error text-white",
};

function Dot({ index, status }: { index: number; status: StepState["status"] }) {
  return (
    <span
      aria-hidden
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 text-[13px] font-semibold ${DOT[status]}`}
    >
      {status === "approved" ? (
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
  const [drawer, setDrawer] = useState<number | null>(null);
  return (
    <>
      <nav aria-label="Migration steps" className="card overflow-x-auto p-2">
        <ol className="flex min-w-[1100px] items-stretch">
          {steps.map((s, i) => {
            const isActive = i === active;
            return (
              <li key={s.key} className="flex flex-1 items-center">
                <div
                  className={`group flex flex-1 items-center gap-1 rounded-[10px] px-2 py-2 ${
                    isActive ? "bg-purple-lt" : "hover:bg-warm"
                  }`}
                >
                  <button
                    type="button"
                    aria-current={isActive ? "step" : undefined}
                    data-testid={`step-${s.key}`}
                    onClick={() => onSelect(i)}
                    className="flex flex-1 items-center gap-2.5 text-left"
                  >
                    <Dot index={i} status={s.status} />
                    <span className="min-w-0">
                      <span className={`block text-[15px] font-semibold ${isActive ? "text-purple" : "text-plum"}`}>
                        {s.label}
                      </span>
                      <StatusPill status={s.status} />
                    </span>
                  </button>
                  <button
                    type="button"
                    aria-label={`Evidence for ${s.label}`}
                    title="Evidence"
                    onClick={() => setDrawer(i)}
                    className="rounded-btn p-1.5 text-slate hover:bg-white hover:text-purple"
                  >
                    <FileSearch size={16} />
                  </button>
                </div>
                {i < steps.length - 1 ? <span aria-hidden className="mx-1 h-px w-4 shrink-0 bg-line" /> : null}
              </li>
            );
          })}
        </ol>
      </nav>
      <EvidenceDrawer step={drawer === null ? null : steps[drawer]} onClose={() => setDrawer(null)} />
    </>
  );
}
