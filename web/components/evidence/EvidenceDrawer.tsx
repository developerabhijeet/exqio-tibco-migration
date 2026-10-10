"use client";

import { useEffect } from "react";
import { FileCheck2, X } from "lucide-react";
import { StatusPill } from "@/components/ui/Pill";
import type { StepState } from "@/lib/types";

/** Side drawer naming the artifacts that prove a step's status. */
export function EvidenceDrawer({ step, onClose }: { step: StepState | null; onClose: () => void }) {
  useEffect(() => {
    if (!step) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step, onClose]);

  if (!step) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="presentation">
      <button type="button" aria-label="Close evidence" className="absolute inset-0 bg-plum/20" onClick={onClose} />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`Evidence for ${step.label}`}
        data-testid="evidence-drawer"
        className="relative flex h-full w-[440px] max-w-full flex-col border-l border-line bg-white shadow-2xl"
      >
        <div className="flex items-start justify-between border-b border-line px-6 py-5">
          <div>
            <p className="eyebrow">Evidence</p>
            <h2 className="panel-title mt-1">{step.label}</h2>
            <div className="mt-2">
              <StatusPill status={step.status} />
            </div>
          </div>
          <button type="button" className="btn-ghost" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {step.evidence.length === 0 ? (
            <p className="text-[14px] text-slate">No evidence recorded for this step yet.</p>
          ) : (
            <ul className="space-y-3">
              {step.evidence.map((e, i) => (
                <li key={`${e.label}-${i}`} className="rounded-card border border-line bg-page p-4">
                  <div className="flex items-center gap-2 font-medium text-plum">
                    <FileCheck2 size={16} className="text-gate" aria-hidden />
                    {e.href ? (
                      <a href={e.href} className="text-purple underline-offset-2 hover:underline">
                        {e.label}
                      </a>
                    ) : (
                      e.label
                    )}
                  </div>
                  {e.detail ? <p className="mt-1 break-words text-[14px] text-slate">{e.detail}</p> : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    </div>
  );
}
