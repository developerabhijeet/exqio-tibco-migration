"use client";

import dynamic from "next/dynamic";
import { useMemo, useState } from "react";
import { useAnalysis, useXml } from "@/lib/queries";
import { elementSpan } from "@/lib/xmlspan";
import type { CanvasNode } from "@/lib/types";
import { Pill } from "@/components/ui/Pill";

const CodeView = dynamic(() => import("./CodeView"), {
  ssr: false,
  loading: () => <div className="p-4 text-[13px] text-slate">Loading editor</div>,
});

const TABS = ["Config", "SQL", "Mappings", "Rules", "Findings", "XML"] as const;
type Tab = (typeof TABS)[number];

function scalar(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  return JSON.stringify(v, null, 1);
}

function ConfigTable({ config }: { config: Record<string, unknown> }) {
  const { settings, input_xslt: _x, ...rest } = config as Record<string, unknown> & { settings?: unknown; input_xslt?: unknown };
  void _x;
  const rows = Object.entries(rest);
  const native = settings && typeof settings === "object" ? Object.entries(settings as Record<string, unknown>) : [];
  return (
    <div className="space-y-4">
      <KeyValues title="Neutral model" rows={rows} empty="No configuration on this step." />
      {native.length > 0 ? <KeyValues title="Native settings" rows={native} /> : null}
    </div>
  );
}

function KeyValues({ title, rows, empty }: { title: string; rows: [string, unknown][]; empty?: string }) {
  return (
    <div>
      <p className="label">{title}</p>
      {rows.length === 0 ? (
        <p className="mt-1 text-[14px] text-slate">{empty}</p>
      ) : (
        <table className="mt-1 w-full text-[13px]">
          <tbody className="divide-y divide-line">
            {rows.map(([k, v]) => (
              <tr key={k}>
                <td className="w-[38%] py-1.5 pr-3 align-top font-medium text-plum">{k}</td>
                <td className="whitespace-pre-wrap break-all py-1.5 font-mono text-[12px] text-ink">{scalar(v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return <p className="rounded-btn bg-warm px-4 py-6 text-center text-[14px] text-slate">{children}</p>;
}

export function Inspector({ processId, node }: { processId: string; node: CanvasNode | null }) {
  const [tab, setTab] = useState<Tab>("Config");
  const xml = useXml(processId);
  const analysis = useAnalysis(processId);

  const span = useMemo(
    () => (xml.data && node?.data.source_line ? elementSpan(xml.data, node.data.source_line) : null),
    [xml.data, node],
  );

  const sql = typeof node?.data.config.sql === "string" ? (node.data.config.sql as string) : null;
  const xslt = Array.isArray(node?.data.config.input_xslt) ? (node!.data.config.input_xslt as string[]) : [];
  const rules = (analysis.data?.rules ?? []).filter(
    (r) => node && (node.data.rule_ids.includes(r.id) || (r.rule_key && node.data.rule_ids.includes(r.rule_key))),
  );
  const findings = (analysis.data?.findings ?? []).filter(
    (f) => node && (node.data.finding_ids.includes(f.id) || (f.finding_key && node.data.finding_ids.includes(f.finding_key))),
  );

  return (
    <section className="card flex h-[620px] flex-col overflow-hidden" data-testid="inspector">
      <div className="border-b border-line px-4 pt-3">
        <p className="label">Inspector</p>
        <p className="truncate font-semibold text-plum">{node ? node.data.name : "Select a step on the canvas"}</p>
        {node ? (
          <p className="truncate font-mono text-[11.5px] text-slate">
            {node.data.native_type} · {node.data.canonical_type}
            {node.data.source_line ? ` · line ${node.data.source_line}` : ""}
          </p>
        ) : null}
        <div role="tablist" aria-label="Inspector" className="mt-2 flex gap-1 overflow-x-auto">
          {TABS.map((t) => (
            <button
              key={t}
              role="tab"
              type="button"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              className={`border-b-2 px-2.5 pb-2 pt-1 text-[14px] font-medium ${
                tab === t ? "border-purple text-purple" : "border-transparent text-slate hover:text-plum"
              }`}
            >
              {t}
              {t === "Rules" && node?.data.rule_ids.length ? ` ${node.data.rule_ids.length}` : ""}
              {t === "Findings" && node?.data.finding_ids.length ? ` ${node.data.finding_ids.length}` : ""}
            </button>
          ))}
        </div>
      </div>
      <div role="tabpanel" aria-label={tab} className="min-h-0 flex-1 overflow-y-auto p-4">
        {tab === "Config" ? (
          node ? <ConfigTable config={node.data.config} /> : <Hint>Select a step to see its configuration.</Hint>
        ) : tab === "SQL" ? (
          sql ? (
            <pre className="whitespace-pre-wrap rounded-btn bg-page p-3 font-mono text-[12.5px] text-ink">{sql}</pre>
          ) : (
            <Hint>{node ? "This step runs no SQL." : "Select a JDBC step to see its statement."}</Hint>
          )
        ) : tab === "Mappings" ? (
          xslt.length > 0 ? (
            <div className="space-y-3">
              {xslt.map((x, i) => (
                <CodeView key={i} value={x} language="xml" height={420} />
              ))}
            </div>
          ) : (
            <Hint>{node ? "No input mapping on this step." : "Select a step to see its input mapping."}</Hint>
          )
        ) : tab === "Rules" ? (
          rules.length > 0 ? (
            <ul className="space-y-2">
              {rules.map((r) => (
                <li key={r.id} className="rounded-btn border border-line p-3 text-[14px]">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[12px] font-semibold text-ai">{r.rule_key ?? r.id}</span>
                    {r.status ? <Pill tone="det">{r.status}</Pill> : null}
                  </div>
                  <p className="mt-1">{r.text}</p>
                </li>
              ))}
            </ul>
          ) : (
            <Hint>
              {node && node.data.rule_ids.length > 0
                ? `Cited by ${node.data.rule_ids.join(", ")}.`
                : "No rules cite this step yet. Rules are drafted and approved in Validate."}
            </Hint>
          )
        ) : tab === "Findings" ? (
          findings.length > 0 ? (
            <ul className="space-y-2">
              {findings.map((f) => (
                <li key={f.id} className="rounded-btn border border-line p-3 text-[14px]">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[12px] font-semibold text-decision">{f.finding_key ?? f.id}</span>
                    {f.severity ? <Pill tone="decision">{f.severity}</Pill> : null}
                    {f.decision ? <Pill tone="det">{f.decision}</Pill> : null}
                  </div>
                  <p className="mt-1">{f.text}</p>
                </li>
              ))}
            </ul>
          ) : (
            <Hint>
              {node && node.data.finding_ids.length > 0
                ? `Cited by ${node.data.finding_ids.join(", ")}.`
                : "No findings cite this step yet. Findings are decided in Validate."}
            </Hint>
          )
        ) : xml.data ? (
          <div className="space-y-2">
            <p className="text-[13px] text-slate">
              {span ? `Lines ${span.start} to ${span.end} hold ${node?.data.name}.` : "The process source file, read only."}
            </p>
            <CodeView value={xml.data} language="xml" height={470} highlight={span} testId="xml-viewer" />
          </div>
        ) : (
          <Hint>{xml.error ? (xml.error as Error).message : "Loading the source file."}</Hint>
        )}
      </div>
    </section>
  );
}
