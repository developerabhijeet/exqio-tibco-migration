"use client";

import { Fragment, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Loader2, ScrollText, Upload } from "lucide-react";
import { SecondaryScreen } from "@/components/shell/SecondaryScreen";
import { RoleLock } from "@/components/shell/RoleNotice";
import { Empty, ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import {
  PHASE_LABEL,
  SEVERITIES,
  expertRulesApi,
  useExpertRules,
  type ExpertRule,
  type ExpertRuleList,
  type Severity,
} from "@/lib/expertRules";
import { useCan } from "@/lib/personas";
import { useAppState } from "@/lib/state";

const SEVERITY_TONE: Record<Severity, "error" | "decision" | "purple" | "det"> = {
  Critical: "error",
  High: "decision",
  Medium: "purple",
  Low: "det",
};

const READ_ONLY = "You are acting as a viewer: expert rules are read only.";

function SeverityPill({ severity }: { severity: Severity }) {
  return <Pill tone={SEVERITY_TONE[severity] ?? "det"}>{severity}</Pill>;
}

function StatChip({ label, value, tone }: { label: string; value: number; tone?: Severity }) {
  return (
    <div className="card-compact flex min-w-[120px] flex-col">
      <p className="label">{label}</p>
      <p className="mt-1 flex items-center gap-2 text-2xl font-semibold text-heading tnum">
        {value}
        {tone ? <SeverityPill severity={tone} /> : null}
      </p>
    </div>
  );
}

function Detail({ rule }: { rule: ExpertRule }) {
  const rows: [string, string][] = [
    ["Guidance", rule.guidance],
    ["Evidence required", rule.evidence],
    ["Pass criteria", rule.pass_criteria],
    ["Enforcement", rule.enforcement],
    ["Applicability", rule.applicability],
    ["Exceptions / notes", rule.notes],
  ];
  const meta = [
    rule.version && `version ${rule.version}`,
    rule.rule_status && `status ${rule.rule_status}`,
    rule.owner && `owner ${rule.owner}`,
    rule.last_reviewed && `reviewed ${rule.last_reviewed}`,
    rule.source === "default" ? "bundled GMF set" : rule.uploaded_by ? `uploaded by ${rule.uploaded_by}` : "uploaded",
  ].filter(Boolean);
  return (
    <div className="grid gap-3 px-4 pb-4 pt-1 md:grid-cols-2" data-testid={`rule-detail-${rule.rule_id}`}>
      {rows
        .filter(([, v]) => v)
        .map(([k, v]) => (
          <div key={k} className={k === "Guidance" ? "md:col-span-2" : undefined}>
            <p className="label">{k}</p>
            <p className="mt-0.5 whitespace-pre-wrap text-md text-ink">{v}</p>
          </div>
        ))}
      <div className="md:col-span-2">
        <p className="label">Prompts</p>
        <p className="mt-0.5 text-md text-slate">
          Added to {rule.phases.map((p) => PHASE_LABEL[p] ?? p).join(", ")}.{" "}
          {rule.last_applied
            ? `Last applied in ${PHASE_LABEL[rule.last_applied.phase] ?? rule.last_applied.phase} on ${new Date(rule.last_applied.at).toLocaleString()}.`
            : "Not applied in a run yet."}
        </p>
        <p className="mt-1 text-sm text-slate">{meta.join(" · ")}</p>
      </div>
    </div>
  );
}

function RulesTable({ data, canWrite, projectId }: { data: ExpertRuleList; canWrite: boolean; projectId: string }) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [domain, setDomain] = useState("all");
  const [severity, setSeverity] = useState("all");
  const [agent, setAgent] = useState("all");
  const [enabled, setEnabled] = useState("all");
  const [open, setOpen] = useState<string | null>(null);
  const domains = useMemo(() => [...new Set(data.rules.map((r) => r.domain).filter(Boolean))].sort(), [data]);
  const agents = useMemo(() => [...new Set(data.rules.map((r) => r.primary_agent).filter(Boolean))].sort(), [data]);
  const toggle = useMutation({
    mutationFn: (r: ExpertRule) => expertRulesApi.setEnabled(projectId, r.rule_id, !r.enabled),
    onSuccess: (row) =>
      qc.setQueryData<ExpertRuleList>(["expert-rules", projectId], (old) =>
        old
          ? {
              ...old,
              rules: old.rules.map((r) => (r.rule_id === row.rule_id ? row : r)),
              enabled: old.enabled + (row.enabled ? 1 : -1),
            }
          : old,
      ),
  });
  const needle = q.trim().toLowerCase();
  const shown = data.rules.filter(
    (r) =>
      (domain === "all" || r.domain === domain) &&
      (severity === "all" || r.severity === severity) &&
      (agent === "all" || r.primary_agent === agent) &&
      (enabled === "all" || (enabled === "on") === r.enabled) &&
      (!needle || `${r.rule_id} ${r.name} ${r.guidance} ${r.domain}`.toLowerCase().includes(needle)),
  );
  const blocked = canWrite ? undefined : READ_ONLY;
  return (
    <section className="card space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="min-w-[200px] flex-1">
          <span className="label">Search</span>
          <input className="input mt-1" value={q} onChange={(e) => setQ(e.target.value)} placeholder="ID, name or guidance" data-testid="rules-search" />
        </label>
        {[
          { label: "Domain", value: domain, set: setDomain, options: domains, testId: "rules-domain" },
          { label: "Severity", value: severity, set: setSeverity, options: [...SEVERITIES], testId: "rules-severity" },
          { label: "Primary agent", value: agent, set: setAgent, options: agents, testId: "rules-agent" },
        ].map((f) => (
          <label key={f.label} className="min-w-0">
            <span className="label">{f.label}</span>
            <select className="select mt-1 block max-w-full" value={f.value} onChange={(e) => f.set(e.target.value)} data-testid={f.testId}>
              <option value="all">All</option>
              {f.options.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          </label>
        ))}
        <label className="min-w-0">
          <span className="label">Enabled</span>
          <select className="select mt-1 block" value={enabled} onChange={(e) => setEnabled(e.target.value)}>
            <option value="all">All</option>
            <option value="on">On</option>
            <option value="off">Off</option>
          </select>
        </label>
      </div>
      <p className="text-sm text-slate" data-testid="rules-shown">
        {shown.length} of {data.total} rules
      </p>
      {toggle.error ? <ErrorNote error={toggle.error} /> : null}
      <div className="table-scroll overflow-x-auto rounded-card border border-line">
        <table className="table" data-testid="rules-table">
          <thead>
            <tr>
              <th className="w-8" aria-label="Expand" />
              <th>Rule</th>
              <th>Severity</th>
              <th className="hidden md:table-cell">Domain</th>
              <th className="hidden lg:table-cell">Agent</th>
              <th>Applied in</th>
              <th>Enabled</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const isOpen = open === r.rule_id;
              return (
                <Fragment key={r.rule_id}>
                  <tr className="row-hover cursor-pointer align-top" data-testid={`rule-row-${r.rule_id}`} onClick={() => setOpen(isOpen ? null : r.rule_id)}>
                    <td>
                      <button type="button" aria-expanded={isOpen} aria-label={`${isOpen ? "Hide" : "Show"} ${r.rule_id}`} className="text-slate">
                        {isOpen ? <ChevronDown size={16} aria-hidden /> : <ChevronRight size={16} aria-hidden />}
                      </button>
                    </td>
                    <td className="min-w-[220px]">
                      <span className="font-mono text-sm text-slate">{r.rule_id}</span>
                      <p className={r.enabled ? "font-medium text-ink" : "font-medium text-slate line-through"}>{r.name}</p>
                    </td>
                    <td>
                      <SeverityPill severity={r.severity} />
                    </td>
                    <td className="hidden text-slate md:table-cell">{r.domain}</td>
                    <td className="hidden text-slate lg:table-cell">{r.primary_agent}</td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {r.applied_in.length ? (
                          r.applied_in.map((p) => (
                            <Pill key={p} tone="ai">
                              {PHASE_LABEL[p] ?? p}
                            </Pill>
                          ))
                        ) : (
                          <span className="text-sm text-slate">not yet</span>
                        )}
                      </div>
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <label className="inline-flex items-center gap-2" title={blocked}>
                        <input
                          type="checkbox"
                          role="switch"
                          aria-label={`Enable ${r.rule_id}`}
                          checked={r.enabled}
                          disabled={!canWrite || toggle.isPending}
                          data-testid={`rule-toggle-${r.rule_id}`}
                          onChange={() => toggle.mutate(r)}
                        />
                        <span className="text-sm text-slate">{r.enabled ? "on" : "off"}</span>
                      </label>
                    </td>
                  </tr>
                  {isOpen ? (
                    <tr>
                      <td colSpan={7} className="bg-sunken/40 p-0">
                        <Detail rule={r} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {shown.length === 0 ? <p className="text-md text-slate">No rule matches these filters.</p> : null}
    </section>
  );
}

export default function RulesPage() {
  const { projectId, setStep } = useAppState();
  const { curate } = useCan();
  const router = useRouter();
  const qc = useQueryClient();
  const rules = useExpertRules(projectId);
  const defaults = useMutation({
    mutationFn: () => expertRulesApi.loadDefaults(projectId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["expert-rules", projectId] }),
  });
  const d = rules.data;
  const blocked = curate ? undefined : READ_ONLY;
  const toUpload = () => {
    setStep("upload");
    router.push("/");
  };

  return (
    <SecondaryScreen
      title="Rules"
      description="The project's TIBCO expert rules: each one is added to the prompts of the phases its agent covers and overrides the default guidance there."
      actions={
        projectId ? (
          <button type="button" className="btn-secondary" onClick={toUpload}>
            <Upload size={16} aria-hidden /> Import rules
          </button>
        ) : null
      }
    >
      {!projectId ? (
        <Empty title="No project selected">Pick a project in the top bar.</Empty>
      ) : rules.error ? (
        <ErrorNote error={rules.error} />
      ) : !d ? null : d.total === 0 ? (
        <Empty
          title="No expert rules yet"
          icon={ScrollText}
          action={
            <>
              <button type="button" className="btn-primary" disabled={!curate || defaults.isPending} title={blocked} onClick={() => defaults.mutate()} data-testid="rules-load-defaults">
                {defaults.isPending ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <RoleLock reason={blocked} />}
                Load GMF expert rules (125)
              </button>
              <button type="button" className="btn-secondary" onClick={toUpload}>
                Go to Upload
              </button>
            </>
          }
        >
          Import a CSV of custom TIBCO rules at Upload, or load the bundled GMF set.
          {defaults.error ? <ErrorNote error={defaults.error} /> : null}
        </Empty>
      ) : (
        <div className="space-y-6">
          <div className="flex flex-wrap gap-3" data-testid="rules-stats">
            <StatChip label="Rules" value={d.total} />
            <StatChip label="Enabled" value={d.enabled} />
            {SEVERITIES.filter((s) => d.by_severity[s]).map((s) => (
              <StatChip key={s} label={s} value={d.by_severity[s]} tone={s} />
            ))}
          </div>
          <RulesTable data={d} canWrite={curate} projectId={projectId} />
        </div>
      )}
    </SecondaryScreen>
  );
}
