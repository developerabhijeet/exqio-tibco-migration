import { Pill } from "@/components/ui/Pill";
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

/** Questions the record cannot answer; each carries a recommendation that is explicitly not a decision. */
export function OpenQuestions({ questions }: { questions: DesignRecord["open_questions"] }) {
  if (questions.length === 0) return <p className="text-[14px] text-slate">No open questions.</p>;
  return (
    <ul className="space-y-3">
      {questions.map((q, i) => (
        <li key={i} className="rounded-btn border border-decision/30 bg-decision-bg/40 p-3">
          <p className="font-medium text-plum">{q.question}</p>
          <p className="mt-1 text-[14px] text-ink">
            <span className="label mr-1.5">Recommendation</span>
            {q.recommendation.replace(NOT_A_DECISION, "").replace(NOT_A_DECISION_PREFIX, "")}{" "}
            <Pill tone="decision" className="ml-1 align-middle">
              not a decision
            </Pill>
          </p>
        </li>
      ))}
    </ul>
  );
}
