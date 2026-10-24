"use client";

import { useState } from "react";
import { Check, MessageSquareReply, RotateCcw, X } from "lucide-react";
import { Pill } from "@/components/ui/Pill";
import { useAnswerDesignQuestion } from "@/lib/queries";
import type { DesignRecord } from "@/lib/types";

// The engine ends each recommendation with "A recommendation, not a decision: the architect decides at G3."
const NOT_A_DECISION = /\s*A recommendation, not a decision[^.]*\.?\s*$/i;
const NOT_A_DECISION_PREFIX = /^\s*(A )?recommendation, not a decision:\s*/i;

/** Design decisions with the alternatives that were weighed and the reason for the choice. */
export function DecisionList({ decisions }: { decisions: DesignRecord["decisions"] }) {
  if (decisions.length === 0) return <p className="text-[14px] text-slate">No design decisions recorded.</p>;
  return (
    <ul className="space-y-3">
      {decisions.map((d, i) => (
        <li key={i} className="rounded-btn border border-line p-3">
          <p className="font-semibold text-plum">{d.title}</p>
          <p className="mt-1 text-[14px]">
            <span className="label mr-1.5">Choice</span>
            {d.choice}
          </p>
          {d.alternatives.length > 0 ? (
            <p className="mt-1 text-[14px] text-slate">
              <span className="label mr-1.5">Alternatives</span>
              {d.alternatives.join("; ")}
            </p>
          ) : null}
          <p className="mt-1 text-[14px] text-ink">
            <span className="label mr-1.5">Reason</span>
            {d.reason}
          </p>
        </li>
      ))}
    </ul>
  );
}

/** One open question: the recommendation, and the architect's answer or rejection.
 *
 * Answering writes onto the design record but never moves the design hash (`open_questions` is in
 * the engine's HASH_EXCLUDED), so settling a question cannot unseat an approved G3. */
function Question({
  q,
  index,
  processId,
}: {
  q: DesignRecord["open_questions"][number];
  index: number;
  processId: string | null;
}) {
  const answer = useAnswerDesignQuestion(processId);
  const [draft, setDraft] = useState("");
  const [open, setOpen] = useState(false);
  const decided = q.decision ?? null;
  const tone = decided === "answered" ? "border-gate/40 bg-gate-bg/40" : decided === "rejected"
    ? "border-line bg-warm/40" : "border-decision/30 bg-decision-bg/40";

  return (
    <li className={`rounded-btn border p-3 ${tone}`} data-testid="open-question" data-decision={decided ?? "open"}>
      <p className="font-medium text-plum">{q.question}</p>
      <p className="mt-1 text-[14px] text-ink">
        <span className="label mr-1.5">Recommendation</span>
        {q.recommendation.replace(NOT_A_DECISION, "").replace(NOT_A_DECISION_PREFIX, "")}{" "}
        <Pill tone="decision" className="ml-1 align-middle">
          not a decision
        </Pill>
      </p>

      {decided ? (
        <div className="mt-2 border-t border-line/60 pt-2 text-[13.5px]">
          <p className="flex flex-wrap items-center gap-1.5">
            {decided === "answered" ? (
              <Check size={14} className="text-gate" aria-hidden />
            ) : (
              <X size={14} className="text-slate" aria-hidden />
            )}
            <span className="font-medium text-plum">
              {decided === "answered" ? "Answered" : "Rejected"}
            </span>
            {q.decided_by ? <span className="text-slate">by {q.decided_by}</span> : null}
          </p>
          {q.answer ? <p className="mt-1 text-ink">{q.answer}</p> : null}
          <button
            type="button"
            className="btn-ghost mt-1.5"
            disabled={answer.isPending}
            onClick={() => answer.mutate({ index, decision: null })}
          >
            <RotateCcw size={14} aria-hidden /> Reopen
          </button>
        </div>
      ) : (
        <div className="mt-2 border-t border-line/60 pt-2">
          {open ? (
            <div className="space-y-2">
              <textarea
                className="w-full rounded-btn border border-line p-2 text-[13.5px]"
                rows={2}
                autoFocus
                placeholder="What did the architect or the business decide?"
                value={draft}
                data-testid="open-question-answer"
                onChange={(e) => setDraft(e.target.value)}
              />
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn-primary"
                  disabled={!draft.trim() || answer.isPending}
                  onClick={() =>
                    answer.mutate({ index, decision: "answered", answer: draft.trim() }, {
                      onSuccess: () => { setDraft(""); setOpen(false); },
                    })
                  }
                >
                  <Check size={15} aria-hidden /> Save answer
                </button>
                <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-secondary" data-testid="answer-question" onClick={() => setOpen(true)}>
                <MessageSquareReply size={15} aria-hidden /> Answer
              </button>
              <button
                type="button"
                className="btn-secondary"
                data-testid="reject-question"
                disabled={answer.isPending}
                onClick={() => answer.mutate({ index, decision: "rejected" })}
              >
                <X size={15} aria-hidden /> Does not apply
              </button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

/** Questions the record cannot answer; each carries a recommendation that is explicitly not a decision. */
export function OpenQuestions({
  questions,
  processId,
}: {
  questions: DesignRecord["open_questions"];
  processId: string | null;
}) {
  if (questions.length === 0) return <p className="text-[14px] text-slate">No open questions.</p>;
  return (
    <ul className="space-y-3">
      {questions.map((q, i) => (
        <Question key={i} q={q} index={i} processId={processId} />
      ))}
    </ul>
  );
}
