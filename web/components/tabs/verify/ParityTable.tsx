"use client";

import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useParity } from "@/lib/buildQueries";
import { useAppState } from "@/lib/state";
import type { FieldDiff, ScenarioReport } from "@/lib/buildTypes";
import { ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { shortKey } from "@/components/tabs/validate/FindingCard";
import { Panel } from "@/components/tabs/build/Panel";
import { CitedText } from "@/components/citations/CitedText";
import { JiraIssues, parityCandidates } from "@/components/connectors/JiraIssues";
import { maskSensitive, sensitiveField } from "@/lib/citations";

const STATUS: Record<string, { tone: "gate" | "error" | "decision" | "det"; text: string }> = {
  match: { tone: "gate", text: "match" },
  defect: { tone: "error", text: "defect" },
  intended: { tone: "decision", text: "intended" },
  noise: { tone: "det", text: "noise" },
};

const CONTROL_ONLY_TITLE =
  "touched only through a branch decision by this finding; not accepted as intended without a TIBCO sandbox run";

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "n/a" : `${Math.round(v * 1000) / 10}%`);

/** A compared value as shown, masked like the cited note beside it: a sensitive field's value is
 * the mask whole; any other value has its SSNs and sensitive pairs masked. */
function show(path: string, v: unknown): string {
  if (v === undefined) return "absent";
  if (v === null) return "null";
  if (sensitiveField(path)) return "***";
  return maskSensitive(typeof v === "string" ? v : JSON.stringify(v));
}

/** One row per compared field. A difference's note is its cited sentence (masked), with chips for
 * the scenario run and the finding that explains it; a payload without `cited` keeps the finding
 * link in the status cell instead. A match no finding explains has nothing to cite. */
function DiffRows({ rows }: { rows: FieldDiff[] }) {
  const { setStep } = useAppState();
  return (
    <>
      {rows.map((f, i) => {
        const s = STATUS[f.status] ?? { tone: "det" as const, text: f.status };
        return (
          <tr key={`${f.path}:${i}`} data-testid="parity-row" data-status={f.status} className={f.status === "defect" ? "bg-error-bg/40" : ""}>
            <td className="break-words px-3 py-1.5 font-mono text-xs">{f.path}</td>
            <td className="break-words px-3 py-1.5 font-mono text-xs">{show(f.path, f.expected)}</td>
            <td className="break-words px-3 py-1.5 font-mono text-xs">{show(f.path, f.actual)}</td>
            <td className="px-3 py-1.5">
              <span className="flex flex-wrap items-center gap-1.5">
                <Pill tone={s.tone}>{s.text}</Pill>
                {f.status === "intended" && f.confirmed === false ? (
                  <Pill tone="det" testId="unconfirmed-pill" title="No TIBCO sandbox run confirmed this difference; it is derived from the TIBCO model">
                    not confirmed
                  </Pill>
                ) : null}
                {!f.cited && (f.status === "intended" || f.status === "defect") && f.finding_id ? (
                  <button
                    type="button"
                    data-testid="finding-pill"
                    className="text-xs font-medium text-decision underline-offset-2 hover:underline"
                    title={
                      f.via === "control"
                        ? CONTROL_ONLY_TITLE
                        : `Finding ${f.finding_id}, decided Fix at G2`
                    }
                    onClick={() => setStep("validate")}
                  >
                    {shortKey(f.finding_id)}
                  </button>
                ) : null}
              </span>
            </td>
            <td className="break-words px-3 py-1.5 text-xs text-ink" data-testid="parity-note">
              {f.cited ? <CitedText value={f.cited} /> : null}
            </td>
          </tr>
        );
      })}
    </>
  );
}

function ScenarioCard({ r }: { r: ScenarioReport }) {
  const fields = r.fields ?? [];
  const effects = r.side_effects ?? [];
  return (
    <div className="rounded-btn border border-line" data-testid="parity-scenario" data-status={r.status}>
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-page px-3 py-2">
        <span className="font-medium text-heading">{r.scenario_name ?? r.scenario_id}</span>
        <span className="font-mono text-2xs text-slate">{r.scenario_key ?? r.scenario_id}</span>
        <span className="ml-auto flex items-center gap-2 text-sm">
          {r.unexplained ? <Pill tone="error">{r.unexplained} unexplained</Pill> : null}
          <Pill tone={r.status === "match" ? "gate" : r.status === "error" ? "error" : "decision"}>{r.status}</Pill>
          <span className="font-semibold text-heading">{pct(r.parity)}</span>
        </span>
      </div>
      {r.status === "error" ? (
        <p className="px-3 py-2 text-sm text-error">{r.error ?? "the scenario did not run"}</p>
      ) : (
        <div className="table-scroll">
          <table className="table min-w-[900px] table-fixed text-sm">
            <thead>
              <tr className="text-left text-2xs uppercase tracking-wide text-slate">
                <th className="w-[24%] px-3 py-1.5">Field</th>
                <th className="w-[16%] px-3 py-1.5">Expected (TIBCO model)</th>
                <th className="w-[16%] px-3 py-1.5">Actual (.NET service)</th>
                <th className="w-[13%] px-3 py-1.5">Status</th>
                <th className="px-3 py-1.5">Note</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              <DiffRows rows={fields} />
              {effects.length > 0 ? (
                <tr>
                  <td colSpan={5} className="bg-sunken px-3 py-1 text-2xs font-semibold uppercase tracking-wide text-slate">
                    Side effects: rows written and messages published
                  </td>
                </tr>
              ) : null}
              <DiffRows rows={effects} />
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const STATIC_LABEL = "Expected output derived from the TIBCO model";

/** The API's label with a capital first letter, or the static text. */
function sourceLabel(label: string | null | undefined): string {
  const t = (label ?? "").trim();
  return t ? t[0].toUpperCase() + t.slice(1) : STATIC_LABEL;
}

/** Field-by-field parity of the latest verify run, against the expected output of the TIBCO model. */
export function ParityTable({ buildId }: { buildId: string | null }) {
  const parity = useParity(buildId);
  const { processId } = useAppState();
  const p = parity.data;
  return (
    <Panel
      title="Parity"
      eyebrow="Field by field"
      testId="parity-table"
      id="parity"
      actions={
        p ? (
          <span className="flex items-center gap-2 text-md">
            <span className="label">Overall</span>
            <span className="text-2xl font-semibold text-heading" data-testid="parity-overall">
              {pct(p.parity)}
            </span>
            <span className="text-slate">
              {p.matched} of {p.compared} fields
            </span>
          </span>
        ) : null
      }
    >
      <p data-testid="parity-label" className="mb-3 inline-flex items-center gap-1.5 rounded-full bg-ai-bg px-3 py-1 text-sm font-medium text-ai">
        {sourceLabel(p?.label)}
      </p>
      {parity.error ? <ErrorNote error={parity.error} /> : null}
      {!p ? (
        <p className="text-md text-slate">{parity.isLoading ? "Loading." : "No verify run for this build yet."}</p>
      ) : (
        <div className="space-y-3">
          {p.unexplained > 0 ? (
            <p role="status" data-testid="unexplained-banner" className="flex items-center gap-2 rounded-card border border-error/30 bg-error-bg px-4 py-2.5 text-md text-error">
              <AlertTriangle size={16} aria-hidden /> Unexplained differences: {p.unexplained}
            </p>
          ) : (
            <p role="status" data-testid="unexplained-banner" className="flex items-center gap-2 rounded-card border border-gate/30 bg-gate-bg px-4 py-2.5 text-md text-gate">
              <CheckCircle2 size={16} aria-hidden /> Unexplained differences: 0
            </p>
          )}
          {p.unconfirmed_intended ? (
            <p data-testid="unconfirmed-intended" className="rounded-card border border-decision/30 bg-decision-bg px-4 py-2 text-md text-decision">
              <span className="font-semibold">{p.unconfirmed_intended} intended difference(s)</span>{" "}
              {p.unconfirmed_note ?? "not confirmed: no TIBCO sandbox run; derived from the TIBCO model"}
            </p>
          ) : null}
          {p.control_only_defects ? (
            <p data-testid="control-only-defects" className="rounded-card border border-error/30 bg-error-bg px-4 py-2 text-md text-error">
              <span className="font-semibold">{p.control_only_defects}</span> of the unexplained differences are touched only
              through a branch decision by a Fix finding
            </p>
          ) : null}
          <p className="text-sm text-slate">
            {p.intended} intended (findings decided Fix), {p.errors} scenario(s) errored. Run{" "}
            {p.created_at ? new Date(p.created_at).toLocaleString() : ""}.
          </p>
          {p.reports.map((r) => (
            <ScenarioCard key={r.run_id ?? r.scenario_id} r={r} />
          ))}
          {processId ? (
            <JiraIssues
              processId={processId}
              scope="verify"
              title="Jira issues for unexplained differences"
              candidates={parityCandidates(p.reports)}
              empty="No unexplained difference to track."
            />
          ) : null}
          {p.noise_rules.length > 0 ? (
            <details className="text-sm">
              <summary className="cursor-pointer font-medium text-heading">Noise rules ({p.noise_rules.length})</summary>
              <ul className="mt-1 space-y-0.5 text-slate">
                {p.noise_rules.map((n) => (
                  <li key={n.path_glob}>
                    <span className="font-mono">{n.path_glob}</span>: {n.reason}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      )}
    </Panel>
  );
}
