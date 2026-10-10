"use client";

import { ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { useBuildTests } from "@/lib/buildQueries";
import { OutcomePill, Panel } from "./Panel";

const KIND_TONE: Record<string, "gate" | "purple" | "ai" | "det"> = { rule: "gate", branch: "purple", scenario: "ai" };

/** Test inventory merged with the TRX results: every test is listed even when it has not run. */
export function TestResults({ buildId, compiled }: { buildId: string; compiled: boolean | null }) {
  const tests = useBuildTests(buildId);
  const s = tests.data?.summary;
  return (
    <Panel
      title="Tests"
      eyebrow="Rule, branch and scenario tests"
      testId="test-results"
      id="tests"
      actions={
        s ? (
          <div className="flex flex-wrap gap-1.5 text-[12.5px]" data-testid="test-totals">
            <Pill tone="det">{s.total} total</Pill>
            <Pill tone="gate">{s.passed} passed</Pill>
            <Pill tone={s.failed ? "error" : "det"}>{s.failed} failed</Pill>
            <Pill tone="det">{s.not_run} not run</Pill>
            {s.no_body ? <Pill tone="decision">{s.no_body} without a body</Pill> : null}
          </div>
        ) : null
      }
    >
      {tests.error ? <ErrorNote error={tests.error} /> : null}
      {compiled === false && s && s.not_run > 0 ? (
        <p className="mb-2 text-[13px] text-slate">
          The solution did not compile here, so the tests are listed from the generated files and none has run.
        </p>
      ) : null}
      {s && s.total === 0 ? <p className="text-[14px] text-slate">No tests were generated.</p> : null}
      {tests.data && tests.data.cases.length > 0 ? (
        <div className="max-h-[420px] overflow-auto rounded-btn border border-line">
          <table className="w-full min-w-[640px] text-[13px]">
            <thead className="sticky top-0">
              <tr className="table-head">
                <th className="px-3 py-2">Test</th>
                <th className="px-3 py-2">Kind</th>
                <th className="px-3 py-2">Rule, branch or scenario</th>
                <th className="px-3 py-2">Outcome</th>
                <th className="px-3 py-2">Message</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {tests.data.cases.map((c) => (
                <tr key={c.name} data-testid="test-row" data-outcome={c.outcome} className="align-top">
                  <td className="px-3 py-2">
                    <p className="text-ink">{c.display_name ?? c.name}</p>
                    <p className="font-mono text-[11px] text-slate" title={c.name}>
                      {c.file}
                    </p>
                  </td>
                  <td className="px-3 py-2">
                    <Pill tone={KIND_TONE[c.kind] ?? "det"}>{c.kind}</Pill>
                  </td>
                  <td className="px-3 py-2 font-mono text-[12px]">{c.rule_key ?? c.ref ?? "none"}</td>
                  <td className="px-3 py-2">
                    <OutcomePill outcome={c.outcome} />
                    {!c.has_body ? (
                      <span className="mt-1 block text-[11.5px] text-decision">no body yet</span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-[12.5px] text-slate">{c.message ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Panel>
  );
}
