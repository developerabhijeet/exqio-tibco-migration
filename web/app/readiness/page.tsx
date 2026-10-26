"use client";

import { ScrollRegion } from "@/components/ui/ScrollRegion";
import { CitedText } from "@/components/citations/CitedText";
import { SecondaryScreen } from "@/components/shell/SecondaryScreen";
import { Empty, ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { useReadiness } from "@/lib/buildQueries";
import { useAppState } from "@/lib/state";
import type { ReadinessRow } from "@/lib/buildTypes";

const STATUS: Record<string, { tone: "gate" | "decision" | "error"; text: string }> = {
  met: { tone: "gate", text: "met" },
  partial: { tone: "decision", text: "partial" },
  missing: { tone: "error", text: "missing" },
};

/** The row's status sentence with a chip per evidence item: each opens that evidence (a step and
 * its panel, or a page such as Settings). Plain when the payload has no `cited`. */
function Row({ row }: { row: ReadinessRow }) {
  const s = STATUS[row.status] ?? { tone: "decision" as const, text: row.status };
  return (
    <tr data-testid="readiness-row" data-status={row.status} data-key={row.key} className="align-top">
      <td className="px-4 py-3 text-md text-ink" data-testid="readiness-claim">
        {row.cited ? (
          <CitedText value={row.cited} />
        ) : (
          <>
            <span className="font-medium text-heading">{row.expectation}</span>
            {row.gap ? <span className="text-slate">: {row.gap}</span> : null}
          </>
        )}
      </td>
      <td className="px-4 py-3">
        <Pill tone={s.tone}>{s.text}</Pill>
      </td>
    </tr>
  );
}

export default function Page() {
  const { projectId } = useAppState();
  const readiness = useReadiness(projectId);
  const r = readiness.data;
  return (
    <SecondaryScreen title="Readiness" description="Each expectation from the SME review with its status, the evidence on screen and the gap.">
      {!projectId ? (
        <Empty title="No project selected">Pick a project in the top bar.</Empty>
      ) : readiness.error ? (
        <ErrorNote error={readiness.error} />
      ) : !r ? null : (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2" data-testid="readiness-summary">
            <Pill tone="gate">{r.summary.met} met</Pill>
            <Pill tone="decision">{r.summary.partial} partial</Pill>
            <Pill tone="error">{r.summary.missing} missing</Pill>
            <span className="text-sm text-slate">Status is computed from stored evidence; select a citation to open it.</span>
          </div>
          <ScrollRegion label="Readiness expectations" as="section" className="card  p-0">
            <table className="table min-w-[640px]">
              <thead>
                <tr className="table-head">
                  <th className="px-4 py-2.5">Expectation, status and evidence</th>
                  <th className="w-[110px] px-4 py-2.5">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {r.rows.map((row) => (
                  <Row key={row.key} row={row} />
                ))}
              </tbody>
            </table>
          </ScrollRegion>
        </div>
      )}
    </SecondaryScreen>
  );
}
