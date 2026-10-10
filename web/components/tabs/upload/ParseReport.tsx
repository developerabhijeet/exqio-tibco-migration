"use client";

import { AlertOctagon, ArrowRight } from "lucide-react";
import { BandPill, Pill } from "@/components/ui/Pill";
import { reportFile } from "@/lib/files";
import type { ProcessSummary, ReportItem, ScanReport } from "@/lib/types";

function Counter({ label, value, testId, tone }: { label: string; value: number; testId: string; tone?: "error" }) {
  return (
    <div className={`card px-4 py-3 ${tone === "error" && value > 0 ? "border-error/40 bg-error-bg" : ""}`}>
      <p className="label">{label}</p>
      <p
        data-testid={testId}
        className={`text-[28px] font-semibold leading-tight ${tone === "error" && value > 0 ? "text-error" : "text-plum"}`}
      >
        {value}
      </p>
    </div>
  );
}

export function ReportCounters({ report, processes }: { report: ScanReport; processes: ProcessSummary[] }) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <Counter label="Files found" value={report.files_found} testId="counter-files-found" />
      <Counter label="Files parsed" value={report.files_parsed} testId="counter-files-parsed" />
      <Counter label="Processes" value={processes.length} testId="counter-processes" />
      <Counter
        label="Unknown activity types"
        value={report.unknown_activity_types.length}
        testId="counter-unknown-types"
        tone="error"
      />
      <Counter label="Unknown elements" value={report.unknown_elements.length} testId="counter-unknown-elements" tone="error" />
      <Counter label="Elements explained" value={report.explained.length} testId="counter-explained" />
    </div>
  );
}

function rowStatus(p: ProcessSummary, report: ScanReport) {
  if (report.failures.some((f) => reportFile(f.path) === p.source_path)) return <Pill tone="error">failed</Pill>;
  if (report.unknown_activity_types.some((u) => u.process_id === p.ir_id))
    return <Pill tone="error">unknown type</Pill>;
  if (report.unknown_elements.some((u) => reportFile(u.path) === p.source_path))
    return <Pill tone="decision">unknown element</Pill>;
  return <Pill tone="gate">parsed</Pill>;
}

export function ParseReportTable({
  report,
  processes,
  onOpen,
}: {
  report: ScanReport;
  processes: ProcessSummary[];
  onOpen: (p: ProcessSummary) => void;
}) {
  const processFiles = new Set(processes.map((p) => p.source_path));
  const otherFailures = report.failures.filter((f) => !processFiles.has(reportFile(f.path)));
  return (
    <section className="card overflow-hidden">
      <div className="flex items-center justify-between px-5 py-4">
        <h3 className="panel-title">Parse report</h3>
        <span className="text-[14px] text-slate">Select a row to open the process on the canvas</span>
      </div>
      <div className="overflow-x-auto">
        <table data-testid="parse-report" className="w-full text-[14px]">
          <thead>
            <tr className="table-head">
              <th className="px-5 py-2.5 font-semibold">File</th>
              <th className="px-3 py-2.5 font-semibold">Dialect</th>
              <th className="px-3 py-2.5 font-semibold">Process</th>
              <th className="px-3 py-2.5 text-right font-semibold">Activities</th>
              <th className="px-3 py-2.5 font-semibold">Complexity</th>
              <th className="px-3 py-2.5 font-semibold">Status</th>
              <th className="px-5 py-2.5" aria-label="Open" />
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {processes.map((p) => (
              <tr
                key={p.id}
                className="cursor-pointer hover:bg-purple-lt/50"
                onClick={() => onOpen(p)}
                data-testid={`report-row-${p.name}`}
              >
                <td className="max-w-[420px] truncate px-5 py-2.5 font-mono text-[12.5px] text-slate" title={p.source_path}>
                  {p.source_path}
                </td>
                <td className="px-3 py-2.5">
                  <Pill tone="det">{p.dialect}</Pill>
                </td>
                <td className="px-3 py-2.5 font-medium text-plum">{p.name}</td>
                <td className="px-3 py-2.5 text-right tabular-nums">{p.activity_count}</td>
                <td className="px-3 py-2.5">
                  <BandPill band={p.complexity_band} />
                </td>
                <td className="px-3 py-2.5">{rowStatus(p, report)}</td>
                <td className="px-5 py-2.5 text-right">
                  <span className="inline-flex items-center gap-1 text-[13px] font-medium text-purple">
                    Canvas <ArrowRight size={14} aria-hidden />
                  </span>
                </td>
              </tr>
            ))}
            {otherFailures.map((f, i) => (
              <tr key={`fail-${i}`}>
                <td className="px-5 py-2.5 font-mono text-[12.5px] text-slate">{reportFile(f.path)}</td>
                <td className="px-3 py-2.5" />
                <td className="px-3 py-2.5 text-slate">{String(f.error ?? "")}</td>
                <td className="px-3 py-2.5" />
                <td className="px-3 py-2.5" />
                <td className="px-3 py-2.5">
                  <Pill tone="error">failed</Pill>
                </td>
                <td />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function itemText(item: ReportItem): { where: string; what: string } {
  const where = String(item.path ?? item.process_id ?? "");
  const what = [item.tag, item.native_type, item.reason, item.error].filter(Boolean).map(String).join(": ");
  return { where, what };
}

/** Red panel: anything the parser could not map or explain. Never hidden. */
export function UnknownsPanel({ report }: { report: ScanReport }) {
  const groups = [
    { title: "Parse failures", items: report.failures },
    { title: "Unknown activity types", items: report.unknown_activity_types },
    { title: "Unknown elements", items: report.unknown_elements },
  ].filter((g) => g.items.length > 0);
  if (groups.length === 0) {
    return (
      <div className="rounded-card border border-gate/30 bg-gate-bg px-5 py-3 text-[14px] text-gate" data-testid="unknowns-clear">
        Every activity type is known and every XML element is mapped or explained.
      </div>
    );
  }
  return (
    <section className="rounded-card border border-error/40 bg-error-bg p-5" data-testid="unknowns-panel">
      <div className="flex items-center gap-2 text-error">
        <AlertOctagon size={18} aria-hidden />
        <h3 className="text-[18px] font-semibold">Needs attention before scope is confirmed</h3>
      </div>
      {groups.map((g) => (
        <div key={g.title} className="mt-3">
          <p className="label text-error">
            {g.title} ({g.items.length})
          </p>
          <ul className="mt-1 space-y-1.5">
            {g.items.map((item, i) => {
              const { where, what } = itemText(item);
              return (
                <li key={i} className="rounded-btn bg-white/70 px-3 py-2 text-[13px]">
                  <p className="break-all font-mono text-[12px] text-slate">{where}</p>
                  <p className="text-ink">{what}</p>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </section>
  );
}
