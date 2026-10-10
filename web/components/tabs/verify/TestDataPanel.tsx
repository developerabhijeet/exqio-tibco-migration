"use client";

import { Lock } from "lucide-react";
import { useTestData } from "@/lib/buildQueries";
import { ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { Panel } from "@/components/tabs/build/Panel";
import { KindPill } from "./ScenarioRunner";

function cell(v: unknown): string {
  if (v === null || v === undefined) return "null";
  return typeof v === "string" ? v : JSON.stringify(v);
}

/** Generated test rows with masking applied; production test data is a separate workstream. */
export function TestDataPanel({ processId }: { processId: string }) {
  const data = useTestData(processId);
  const d = data.data;
  const masked = new Set((d?.masked_fields ?? []).map((m) => m.toLowerCase()));
  return (
    <Panel title="Test data" eyebrow="Generated, masked" testId="test-data-panel" id="test-data">
      {data.error ? <ErrorNote error={data.error} /> : null}
      {!d ? (
        <p className="text-[14px] text-slate">{data.isLoading ? "Loading." : "No test data yet: generate the scenarios first."}</p>
      ) : (
        <div className="space-y-3">
          <p className="text-[14px] text-ink">{d.note}</p>
          <div className="rounded-btn border border-line bg-page px-3 py-2 text-[13px]">
            <p className="flex items-center gap-1.5 font-semibold text-plum">
              <Lock size={13} aria-hidden /> Masking rules
            </p>
            <p className="mt-0.5 text-ink">{d.masking}</p>
            <p className="mt-1 text-slate">
              Masked fields in this process:{" "}
              {d.masked_fields.length ? (
                d.masked_fields.map((f) => (
                  <Pill key={f} tone="decision" className="mr-1">
                    {f}
                  </Pill>
                ))
              ) : (
                <span>none (no field of this process is tagged sensitive)</span>
              )}
            </p>
          </div>
          {d.tables.map((t) => {
            const cols = [...new Set(t.rows.flatMap((r) => Object.keys(r)))];
            return (
              <div key={t.table} data-testid="test-data-table" className="overflow-x-auto rounded-btn border border-line">
                <p className="flex items-center gap-2 border-b border-line bg-page px-3 py-1.5 text-[13px]">
                  <span className="font-mono font-semibold text-plum">{t.table}</span>
                  <span className="text-slate">{t.operations.join(", ")}</span>
                  <span className="ml-auto text-slate">{t.rows.length} rows</span>
                </p>
                <table className="w-full text-[12.5px]">
                  <thead>
                    <tr className="text-left text-[11px] uppercase tracking-wide text-slate">
                      {cols.map((c) => (
                        <th key={c} className="px-3 py-1.5">
                          {c}
                          {masked.has(c.toLowerCase()) ? <Lock size={10} className="ml-1 inline text-decision" aria-label="masked" /> : null}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {t.rows.map((r, i) => (
                      <tr key={i}>
                        {cols.map((c) => (
                          <td key={c} className={`px-3 py-1.5 font-mono ${masked.has(c.toLowerCase()) ? "text-decision" : "text-ink"}`}>
                            {cell(r[c])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })}
          {d.inputs.length > 0 ? (
            <div className="overflow-x-auto rounded-btn border border-line">
              <p className="border-b border-line bg-page px-3 py-1.5 text-[13px] font-semibold text-plum">Scenario inputs</p>
              <table className="w-full text-[12.5px]">
                <tbody className="divide-y divide-line">
                  {d.inputs.map((x) => (
                    <tr key={x.scenario}>
                      <td className="px-3 py-1.5">
                        <KindPill kind={x.kind} />
                      </td>
                      <td className="px-3 py-1.5 text-ink">{x.name}</td>
                      <td className="px-3 py-1.5 font-mono">{JSON.stringify(x.input)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {d.notes.map((n) => (
            <p key={n} className="text-[12.5px] text-decision">
              {n}
            </p>
          ))}
        </div>
      )}
    </Panel>
  );
}
