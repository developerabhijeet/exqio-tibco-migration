"use client";

import { AlertTriangle, CheckCircle2, ChevronRight, CircleDashed } from "lucide-react";
import { duplicates } from "@/lib/files";
import type { FileEntry, ScanReport } from "@/lib/types";

function Row({
  state,
  title,
  detail,
  items,
}: {
  state: "ok" | "warn" | "pending";
  title: string;
  detail: string;
  items?: string[];
}) {
  const Icon = state === "ok" ? CheckCircle2 : state === "warn" ? AlertTriangle : CircleDashed;
  const color = state === "ok" ? "text-gate" : state === "warn" ? "text-decision" : "text-slate";
  return (
    <li className="py-3">
      <div className="flex items-start gap-2.5">
        <Icon size={18} className={`mt-0.5 shrink-0 ${color}`} aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-plum">{title}</p>
          <p className="text-[14px] text-slate">{detail}</p>
          {items && items.length > 0 ? (
            <details className="mt-1">
              <summary className="cursor-pointer text-[13px] text-purple">Show {items.length}</summary>
              <ul className="mt-1 max-h-40 overflow-y-auto font-mono text-[12px] text-slate">
                {items.map((i) => (
                  <li key={i} className="truncate">
                    {i}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      </div>
    </li>
  );
}

export function ValidationPanel({ files, report }: { files: FileEntry[]; report: ScanReport | null }) {
  const dups = duplicates(files);
  const unsupported = files.filter((f) => f.kind === "other");
  const unreadable = report?.failures ?? [];
  return (
    <section className="card p-5" data-testid="validation-panel">
      {/* Collapsed: the checks are opened when they are discussed, not read off a full panel. */}
      <details className="group/validation">
        <summary className="cursor-pointer list-none">
          <h3 className="panel-title flex items-center gap-1.5">
            <ChevronRight
              size={16}
              className="text-slate transition-transform group-open/validation:rotate-90"
              aria-hidden
            />
            Validation
            <span className="ml-auto text-[14px] font-normal text-slate">3 checks</span>
          </h3>
        </summary>
        <ul className="mt-1 divide-y divide-line">
          <Row
            state={dups.length ? "warn" : "ok"}
            title="Duplicates"
            detail={
              dups.length
                ? `${dups.length} files are copies of another file (packaged copies are read once).`
                : "No duplicate sources."
            }
            items={dups.map((d) => `${d.path} = ${d.of}`)}
          />
          <Row
            state={report ? (unreadable.length ? "warn" : "ok") : "pending"}
            title="Unreadable files"
            detail={
              report
                ? unreadable.length
                  ? `${unreadable.length} files could not be parsed.`
                  : "Every source file was read."
                : "Checked during the scan."
            }
            items={unreadable.map((f) => `${String(f.path)}: ${String(f.error ?? "")}`)}
          />
          <Row
            state={unsupported.length ? "warn" : "ok"}
            title="Not parsed"
            detail={
              unsupported.length
                ? `${unsupported.length} files have extensions the scanner does not read (Studio metadata, notes, stubs).`
                : "Every file has a supported extension."
            }
            items={unsupported.map((f) => f.path)}
          />
        </ul>
      </details>
    </section>
  );
}
