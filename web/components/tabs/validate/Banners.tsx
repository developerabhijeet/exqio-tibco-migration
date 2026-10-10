import { AlertTriangle, CheckCircle2, CircleSlash, XCircle } from "lucide-react";
import type { Coverage, Verification } from "@/lib/types";

function Banner({
  ok,
  testId,
  title,
  children,
}: {
  ok: boolean;
  testId: string;
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      data-testid={testId}
      data-state={ok ? "ok" : "fail"}
      role="status"
      className={`rounded-card border px-4 py-3 ${ok ? "border-gate/30 bg-gate-bg text-gate" : "border-error/30 bg-error-bg text-error"}`}
    >
      <p className="flex items-center gap-2 font-semibold">
        {ok ? <CheckCircle2 size={18} aria-hidden /> : <XCircle size={18} aria-hidden />}
        {title}
      </p>
      {children ? <div className="mt-1.5 pl-[26px] text-[13.5px]">{children}</div> : null}
    </div>
  );
}

/** Every rule citation re-checked by code against the parsed model. */
export function CitationsBanner({ verification }: { verification: Verification }) {
  const ok = verification.verified === verification.total && verification.failures.length === 0;
  return (
    <Banner ok={ok} testId="banner-citations" title={`Citations verified ${verification.verified} of ${verification.total}`}>
      {ok ? (
        "Each cited step, condition and statement exists in the parsed TIBCO model."
      ) : (
        <ul className="space-y-0.5">
          {verification.failures.map((f, i) => (
            <li key={i}>
              <span className="font-mono font-semibold">{f.rule_id}</span>: {f.citation.activity_id} {f.citation.kind}{" "}
              <span className="font-mono text-[12px]">{f.citation.text}</span>. {f.reason}
            </li>
          ))}
        </ul>
      )}
    </Banner>
  );
}

/** Every step, branch and mapping is either covered by a rule or marked technical. */
export function CoverageBanner({ coverage }: { coverage: Coverage }) {
  const gaps = coverage.items.filter((i) => i.covered_by.length === 0 && !i.technical_reason);
  const ok = coverage.covered === coverage.total && gaps.length === 0;
  return (
    <Banner ok={ok} testId="banner-coverage" title={`Coverage ${coverage.covered} of ${coverage.total} steps, branches and mappings`}>
      {ok ? (
        "Each item is cited by a rule or marked technical with a reason."
      ) : (
        <ul className="space-y-0.5">
          {gaps.slice(0, 12).map((g) => (
            <li key={`${g.kind}:${g.ref}`}>
              Not covered: {g.kind} <span className="font-mono text-[12px]">{g.ref}</span>
            </li>
          ))}
          {gaps.length > 12 ? <li>and {gaps.length - 12} more</li> : null}
        </ul>
      )}
    </Banner>
  );
}

export function AiBanner({ reason }: { reason: string | null | undefined }) {
  return (
    <div data-testid="banner-code-only" className="rounded-card border border-decision/30 bg-decision-bg px-4 py-3 text-decision">
      <p className="flex items-center gap-2 font-semibold">
        <CircleSlash size={18} aria-hidden /> Code-only analysis: no model configured
      </p>
      {reason ? <p className="mt-1 pl-[26px] text-[13.5px]">{reason}</p> : null}
    </div>
  );
}

export function NoticeBanner({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 rounded-card border border-decision/30 bg-decision-bg px-4 py-3 text-[14px] text-decision">
      <AlertTriangle size={18} aria-hidden /> {text}
    </div>
  );
}
