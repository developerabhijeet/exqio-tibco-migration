"use client";

import { ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { useBuildReviews } from "@/lib/buildQueries";
import type { BuilderResult } from "@/lib/buildTypes";
import { Panel } from "./Panel";

const TRIAGE: Record<string, { tone: "gate" | "decision" | "det" | "error"; text: string }> = {
  patch: { tone: "error", text: "patch" },
  defer: { tone: "det", text: "defer" },
  decision: { tone: "decision", text: "needs a decision" },
};

/** Equivalence, security and standards reviewers on agent-written code, with triage. */
export function ReviewPanel({ buildId, builder }: { buildId: string; builder: BuilderResult | null }) {
  const reviews = useBuildReviews(buildId);
  const list = reviews.data ?? [];
  return (
    <Panel title="Review panel" eyebrow="Equivalence, security, standards" testId="review-panel">
      {reviews.error ? <ErrorNote error={reviews.error} /> : null}
      {builder ? (
        <p className="mb-3 text-[13px] text-slate">
          Code Builder ({builder.provider ?? "no provider"}): {builder.filled.length} file(s) filled,{" "}
          {builder.rejections.length} reply(ies) rejected by the code checks.
          {builder.filled.length === 0 ? " The reviewers only read agent-written code, so there was nothing to review." : ""}
        </p>
      ) : null}
      {list.length === 0 ? (
        <p className="text-[14px] text-slate">No review findings.</p>
      ) : (
        <ul className="space-y-2">
          {list.map((r, i) => {
            const t = TRIAGE[r.triage] ?? { tone: "det" as const, text: r.triage };
            return (
              <li key={i} data-testid="review-finding" className="rounded-btn border border-line p-3 text-[13.5px]">
                <div className="flex flex-wrap items-center gap-2">
                  <Pill tone="ai">{r.reviewer}</Pill>
                  <Pill tone={t.tone}>{t.text}</Pill>
                  {r.file ? (
                    <span className="font-mono text-[12px] text-slate">
                      {r.file}
                      {r.line ? `:${r.line}` : ""}
                    </span>
                  ) : null}
                </div>
                <p className="mt-1.5 text-ink">{r.text}</p>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
