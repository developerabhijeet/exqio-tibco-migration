"use client";

import { FlaskConical } from "lucide-react";
import { useReport } from "@/lib/queries";
import { useAppState } from "@/lib/state";

/** Shown on every screen while the open project's scan report says the estate is synthetic. */
export function SyntheticBanner() {
  const { projectId } = useAppState();
  const report = useReport(projectId);
  if (!report.data?.synthetic) return null;
  return (
    <div data-testid="synthetic-banner" className="border-b border-decision/20 bg-decision-bg">
      <div className="mx-auto flex max-w-[1440px] items-center gap-2 px-6 py-2 text-[14px] text-decision">
        <FlaskConical size={16} aria-hidden />
        <span className="font-semibold">Synthetic estate.</span>
        <span>
          This project was scanned from generated TIBCO sources (SYNTHETIC.txt found). No client code or data.
        </span>
      </div>
    </div>
  );
}
