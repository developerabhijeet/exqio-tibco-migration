"use client";

import { MapPin } from "lucide-react";
import { Pill } from "@/components/ui/Pill";
import { useAppState } from "@/lib/state";
import type { AnalysisView, DesignRecord } from "@/lib/types";
import { shortKey } from "@/components/tabs/validate/FindingCard";

/** Each TIBCO step and the C# project, file, class and method code maps it to. */
export function MappingTable({ mapping, analysis }: { mapping: DesignRecord["mapping"]; analysis: AnalysisView | null | undefined }) {
  const { jumpToStep } = useAppState();
  const ruleKey = new Map<string, string>();
  for (const r of analysis?.rules ?? []) {
    ruleKey.set(r.id, r.rule_key ?? r.id);
    if (r.rule_key) ruleKey.set(r.rule_key, r.rule_key);
  }
  const finding = new Map<string, { key: string; decision: string | null; severity?: string }>();
  for (const f of analysis?.findings ?? []) {
    const v = { key: f.finding_key ?? f.id, decision: (f.decision as string) ?? null, severity: f.severity };
    finding.set(f.id, v);
    if (f.finding_key) finding.set(f.finding_key, v);
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[960px] text-[13.5px]" data-testid="mapping-table">
        <thead>
          <tr className="table-head">
            <th className="px-3 py-2">TIBCO step</th>
            <th className="px-3 py-2">Type and pattern</th>
            <th className="px-3 py-2">Project and file</th>
            <th className="px-3 py-2">Class and method</th>
            <th className="px-3 py-2">Rules</th>
            <th className="px-3 py-2">Findings</th>
            <th className="px-3 py-2">Origin</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {mapping.map((m, i) => (
            <tr key={`${m.activity_id}-${m.pattern}-${i}`} data-testid="mapping-row" data-activity={m.activity_id} className="align-top hover:bg-page">
              <td className="px-3 py-2">
                <button
                  type="button"
                  className="inline-flex items-center gap-1 text-left font-semibold text-plum hover:text-purple"
                  title="Open on the canvas"
                  onClick={() => jumpToStep(m.activity_id)}
                >
                  <MapPin size={13} className="shrink-0 text-ai" aria-hidden />
                  {m.activity_name}
                </button>
                {m.activity_name !== m.activity_id ? (
                  <span className="block font-mono text-[11.5px] text-slate">{m.activity_id}</span>
                ) : null}
              </td>
              <td className="px-3 py-2">
                <span className="block font-mono text-[12px] text-slate">{m.canonical_type}</span>
                <span className="block font-mono text-[12px] text-ink">{m.pattern}</span>
              </td>
              <td className="px-3 py-2">
                <span className="block text-[12.5px] text-slate">{m.project}</span>
                <span className="block break-all font-mono text-[12px]">{m.file}</span>
              </td>
              <td className="px-3 py-2">
                <span className="block font-mono text-[12.5px] font-semibold text-plum">{m.class_name}</span>
                <span className="block break-all font-mono text-[12px] text-ink">{m.method}</span>
              </td>
              <td className="px-3 py-2">
                <div className="flex flex-wrap gap-1">
                  {m.rule_ids.map((r) => (
                    <span key={r} data-testid="rule-badge" className="rounded bg-plum px-1.5 font-mono text-[11.5px] font-semibold text-white">
                      {ruleKey.get(r) ?? r}
                    </span>
                  ))}
                </div>
              </td>
              <td className="px-3 py-2">
                <div className="flex flex-wrap gap-1">
                  {m.finding_ids.map((fid) => {
                    const f = finding.get(fid);
                    const key = f?.key ?? fid;
                    return (
                      <span
                        key={fid}
                        data-testid="finding-badge"
                        title={`${key}${f?.decision ? `: ${f.decision}` : ""}`}
                        className={`rounded px-1.5 font-mono text-[11.5px] font-semibold ${
                          f?.severity === "high" ? "bg-error-bg text-error" : "bg-decision-bg text-decision"
                        }`}
                      >
                        {shortKey(key)}
                        {f?.decision ? ` ${f.decision}` : ""}
                      </span>
                    );
                  })}
                </div>
              </td>
              <td className="px-3 py-2">
                {m.origin === "code" ? (
                  <Pill tone="det" title="Code-generated mapping from the pattern catalogue; no model involved">
                    code
                  </Pill>
                ) : (
                  <Pill tone="ai" title="Agent-assisted mapping: the builder model drafts this method body and a reviewer checks it">
                    AI
                  </Pill>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
