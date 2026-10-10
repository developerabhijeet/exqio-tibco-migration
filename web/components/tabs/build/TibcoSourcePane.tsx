"use client";

import dynamic from "next/dynamic";
import { useMemo } from "react";
import { FileText } from "lucide-react";
import { ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { useXml } from "@/lib/queries";
import type { ProcessSummary } from "@/lib/types";
import { elementSpan, focusLine, type XmlHighlight } from "@/lib/xmlspan";

const CodeView = dynamic(() => import("@/components/canvas/CodeView"), {
  ssr: false,
  loading: () => <div className="h-[520px] rounded-btn border border-line bg-page p-4 text-[13px] text-slate">Loading editor</div>,
});

/**
 * The TIBCO process source, read only, from the same route the Discover inspector reads
 * (GET /processes/{id}/xml). With a source line, the step's element is highlighted and scrolled
 * to, with the same span rules as Discover.
 */
export function TibcoSourcePane({
  proc,
  line,
  activityName,
  sql,
  height = 520,
}: {
  proc: ProcessSummary | null;
  line: number | null;
  activityName?: string | null;
  /** The step's SQL, when known, so the reader lands on it as in Discover. */
  sql?: string | null;
  height?: number;
}) {
  const xml = useXml(proc?.id ?? null);
  const span = useMemo<XmlHighlight | null>(() => {
    if (!xml.data || !line) return null;
    const s = elementSpan(xml.data, line);
    return s ? { start: s.start, end: s.end, focus: focusLine(xml.data, s, sql) } : null;
  }, [xml.data, line, sql]);

  if (!proc) {
    return (
      <div
        data-testid="tibco-source"
        style={{ minHeight: height }}
        className="flex items-center justify-center rounded-btn border border-dashed border-line bg-warm/60 p-6 text-center text-[14px] text-slate"
      >
        Select a TIBCO process file, or a generated file that implements a step, to see the TIBCO source here.
      </div>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-2" data-testid="tibco-source">
      <div className="flex min-h-[28px] flex-wrap items-center gap-2">
        <FileText size={15} className="text-slate" aria-hidden />
        <span data-testid="tibco-source-path" className="min-w-0 truncate font-mono text-[12.5px] font-semibold text-plum">
          {proc.source_path}
        </span>
        <Pill tone="purple" title="The uploaded TIBCO source of this process">
          TIBCO {proc.dialect}
        </Pill>
        <span className="ml-auto text-[12px] text-slate" data-testid="tibco-source-span">
          {span ? `lines ${span.start} to ${span.end}${activityName ? `: ${activityName}` : ""}` : "read only"}
        </span>
      </div>
      {xml.error ? <ErrorNote error={xml.error} /> : null}
      {xml.data ? (
        <CodeView value={xml.data} language="xml" height={height} highlight={span} testId="tibco-xml" />
      ) : xml.isLoading ? (
        <div style={{ height }} className="rounded-btn border border-line bg-page p-4 text-[13px] text-slate">
          Loading the TIBCO source
        </div>
      ) : null}
    </div>
  );
}
