"use client";

import { Check, RotateCcw, X } from "lucide-react";
import { ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { useBuildReviews, useDecideAllReviews, useDecideReview } from "@/lib/buildQueries";
import type { BuildRejection, BuilderResult } from "@/lib/buildTypes";
import { Panel } from "./Panel";


/**
 * What the code checks refused, and the body they refused it for.
 *
 * A count tells a reader that something was rejected; it does not let them judge whether the check
 * was right. When a rule ends the build with no test body, this is where the body that was offered
 * for it can be read, which is the difference between a verdict and evidence.
 */
function Rejections({ rejections }: { rejections: BuildRejection[] }) {
  if (rejections.length === 0) return null;
  return (
    <details className="mb-3" data-testid="builder-rejections">
      <summary className="cursor-pointer text-[13px] font-medium text-plum">
        What the checks refused ({rejections.length})
      </summary>
      <ul className="mt-2 space-y-2">
        {rejections.map((r, i) => (
          <li key={`${r.path}-${r.attempt ?? 0}-${i}`} className="rounded-btn border border-line p-2.5 text-[12.5px]">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="break-all font-mono text-[11.5px] text-slate">{r.path}</span>
              {r.attempt ? <Pill tone="det">attempt {r.attempt}</Pill> : null}
            </div>
            <p className="mt-1 text-ink">{r.reason}</p>
            {r.body ? (
              <pre className="mt-1.5 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded bg-page p-2 font-mono text-[11px] leading-5 text-slate">
                {r.body}
              </pre>
            ) : null}
          </li>
        ))}
      </ul>
    </details>
  );
}
const TRIAGE: Record<string, { tone: "gate" | "decision" | "det" | "error"; text: string }> = {
  patch: { tone: "error", text: "patch" },
  defer: { tone: "det", text: "defer" },
  decision: { tone: "decision", text: "needs a decision" },
};

/** Equivalence, security and standards reviewers on agent-written code, with triage.
 *
 * Each finding takes a verdict. Keeping one hands it to the next build, which asks the model for a
 * diff that applies it and rolls that diff back unless the solution still compiles and every test
 * that passed still passes; rejecting one records that it was read and dismissed. Nothing here
 * edits code directly: the build is the only thing that writes. */
export function ReviewPanel({ buildId, builder }: { buildId: string; builder: BuilderResult | null }) {
  const reviews = useBuildReviews(buildId);
  const decide = useDecideReview(buildId);
  const decideAll = useDecideAllReviews(buildId);
  const list = reviews.data ?? [];
  const kept = list.filter((r) => r.decision === "keep").length;
  const undecided = list.filter((r) => !r.decision).length;
  return (
    <Panel
      title="Review panel"
      eyebrow="Equivalence, security, standards"
      testId="review-panel"
      actions={
        list.length > 0 ? (
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-[13px] text-slate" data-testid="review-decision-summary">
              {kept} kept · {list.length - kept - undecided} rejected · {undecided} undecided
            </span>
            {undecided > 0 ? (
              <>
                <button
                  type="button"
                  className="btn-secondary"
                  data-testid="review-keep-all"
                  disabled={decideAll.isPending}
                  title={`Keep the ${undecided} finding(s) nobody has decided yet`}
                  onClick={() => decideAll.mutate({ decision: "keep", only_undecided: true })}
                >
                  <Check size={15} aria-hidden /> Keep all {undecided}
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  data-testid="review-reject-all"
                  disabled={decideAll.isPending}
                  title={`Reject the ${undecided} finding(s) nobody has decided yet`}
                  onClick={() => decideAll.mutate({ decision: "reject", only_undecided: true })}
                >
                  <X size={15} aria-hidden /> Reject all {undecided}
                </button>
              </>
            ) : null}
          </span>
        ) : undefined
      }
    >
      {reviews.error ? <ErrorNote error={reviews.error} /> : null}
      {builder ? (
        <>
          <p className="mb-3 text-[13px] text-slate">
            Code Builder ({builder.provider ?? "no provider"}): {builder.filled.length} file(s) filled,{" "}
            {builder.rejections.length} reply(ies) rejected by the code checks.
            {builder.filled.length === 0 ? " The reviewers only read agent-written code, so there was nothing to review." : ""}
          </p>
          <Rejections
            rejections={[...builder.rejections, ...(builder.test_engineer?.rejections ?? [])]}
          />
        </>
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
                <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-line/60 pt-2">
                  <button
                    type="button"
                    data-testid="review-keep"
                    aria-pressed={r.decision === "keep"}
                    disabled={decide.isPending || !!r.applied_in}
                    className={r.decision === "keep" ? "btn-primary" : "btn-secondary"}
                    onClick={() => decide.mutate({ index: i, decision: r.decision === "keep" ? null : "keep" })}
                  >
                    <Check size={15} aria-hidden /> Keep
                  </button>
                  <button
                    type="button"
                    data-testid="review-reject"
                    aria-pressed={r.decision === "reject"}
                    disabled={decide.isPending || !!r.applied_in}
                    className="btn-secondary"
                    onClick={() => decide.mutate({ index: i, decision: r.decision === "reject" ? null : "reject" })}
                  >
                    <X size={15} aria-hidden /> Reject
                  </button>
                  {r.decision ? (
                    <span className="flex items-center gap-1.5 text-[12.5px] text-slate">
                      {r.applied_in ? (
                        <>
                          <Check size={13} className="text-gate" aria-hidden /> applied in a later build
                        </>
                      ) : (
                        <>
                          <RotateCcw size={13} aria-hidden />
                          {r.decision === "keep"
                            ? "the next build applies this"
                            : "dismissed"}
                          {r.decided_by ? ` by ${r.decided_by}` : ""}
                        </>
                      )}
                    </span>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
