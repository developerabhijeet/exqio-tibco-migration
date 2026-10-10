"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { History, Loader2, MessageSquare, Play } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useJob } from "@/lib/jobs";
import { useAnalysis, useGates, useProcesses } from "@/lib/queries";
import { useAppState } from "@/lib/state";
import type { AnalysisView } from "@/lib/types";
import { Empty, ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { JobLog } from "@/components/jobs/JobLog";
import { GateButton } from "@/components/gates/GateButton";
import { AuditDrawer } from "@/components/gates/AuditDrawer";
import { ProcessPicker } from "@/components/tabs/ProcessPicker";
import { AiBanner, CitationsBanner, CoverageBanner, NoticeBanner } from "./Banners";
import { RuleCard } from "./RuleCard";
import { FindingCard } from "./FindingCard";

const STAGES = ["static rules", "analyst", "verifier", "coverage", "risk reviewer", "challenger", "persist"];

function Count({ n, label, tone }: { n: number; label: string; tone: "gate" | "decision" | "error" | "det" }) {
  return n > 0 ? <Pill tone={tone}>{`${n} ${label}`}</Pill> : null;
}

export function ValidateTab() {
  const qc = useQueryClient();
  const { processId, setAskOpen } = useAppState();
  const { projectId } = useAppState();
  const processes = useProcesses(projectId);
  const analysis = useAnalysis(processId);
  const gates = useGates(processId);
  const { stream, watch } = useJob("analyse", processId);
  const [runError, setRunError] = useState<string | null>(null);
  const [auditOpen, setAuditOpen] = useState(false);

  const proc = processes.data?.find((p) => p.id === processId) ?? null;
  const running = stream?.status === "running";
  const a = analysis.data;

  const refresh = (view: AnalysisView) => {
    if (!processId) return;
    qc.setQueryData(["analysis", processId], view);
    for (const q of ["gates", "audit", "design"]) qc.invalidateQueries({ queryKey: [q, processId] });
    qc.invalidateQueries({ queryKey: ["processes"] });
  };

  const run = async () => {
    if (!processId) return;
    setRunError(null);
    try {
      const { job_id } = await api.analyse(processId);
      watch(job_id);
    } catch (e) {
      setRunError(e instanceof ApiError ? e.detail : String(e));
    }
  };

  const gateList = gates.data ?? a?.gates ?? [];
  const g = (name: string) => gateList.find((x) => x.gate === name);
  const rules = a?.rules ?? [];
  const findings = a?.findings ?? [];
  const undecidedRules = rules.filter((r) => r.status === "draft" || r.status === "unverified").length;
  const undecidedFindings = findings.filter((f) => !f.decision).length;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Step 3</p>
          <h2 className="panel-title">Validate</h2>
          <p className="mt-1 max-w-[720px] text-[14px] text-slate">
            The model drafts rules and findings from the parsed process. Code verifies every citation and checks coverage. A
            business SME approves the rules (G1) and decides each finding (G2).
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ProcessPicker />
          <button type="button" className="btn-secondary" onClick={() => setAskOpen(true)}>
            <MessageSquare size={16} aria-hidden /> Ask AI
          </button>
          <button type="button" className="btn-secondary" disabled={!processId} onClick={() => setAuditOpen(true)}>
            <History size={16} aria-hidden /> Audit trail
          </button>
          <button type="button" className="btn-primary" disabled={!processId || running} onClick={run}>
            {running ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Play size={16} aria-hidden />}
            Run analysis
          </button>
        </div>
      </div>

      {!processId ? (
        <Empty title="No process open">Pick a process above, or open one from Discover.</Empty>
      ) : (
        <div className="grid items-start gap-5 lg:grid-cols-[400px_1fr]">
          <div className="space-y-4">
            {runError ? <ErrorNote error={runError} /> : null}
            <JobLog
              title="Analysis job"
              stream={stream}
              stages={STAGES}
              testId="analysis-log"
              idle={
                a?.analysed
                  ? `Last analysis ${a.created_at ? new Date(a.created_at).toLocaleString() : ""} with provider ${a.provider ?? "none"}. Run it again to see the log.`
                  : "No analysis yet for this process."
              }
            />
            {a?.analysed ? (
              <section className="card p-4" data-testid="analysis-summary">
                <p className="label">Summary</p>
                <h3 className="mt-0.5 text-[16px] font-semibold text-plum">{proc?.name}</h3>
                <p className="mt-2 text-[14.5px] leading-relaxed text-ink">{a.summary}</p>
                <dl className="mt-3 grid grid-cols-[110px_1fr] gap-y-1 text-[13px]">
                  <dt className="text-slate">Rules</dt>
                  <dd>{rules.length}</dd>
                  <dt className="text-slate">Findings</dt>
                  <dd>{findings.length}</dd>
                  <dt className="text-slate">Provider</dt>
                  <dd>{a.provider ?? "none"}</dd>
                  <dt className="text-slate">Spec hash</dt>
                  <dd className="truncate font-mono text-[12px]" title={a.spec_hash}>
                    {a.spec_hash?.slice(0, 16)}
                  </dd>
                </dl>
              </section>
            ) : null}
          </div>

          <div className="min-w-0 space-y-4">
            {analysis.error ? <ErrorNote error={analysis.error} /> : null}
            <div className="grid gap-3 xl:grid-cols-2">
              <GateButton
                processId={processId}
                gate={g("G1")}
                name="G1"
                started={!!a?.analysed}
                hint="Every rule approved, edited or rejected; citations verified; coverage complete."
              />
              <GateButton
                processId={processId}
                gate={g("G2")}
                name="G2"
                started={!!a?.analysed}
                hint="Every finding decided: keep behavior, fix, or ask the business."
              />
            </div>

            {!a?.analysed ? (
              <Empty title="Not analysed yet">
                Run analysis to draft rules and findings for {proc?.name ?? "this process"}.
              </Empty>
            ) : (
              <>
                {a.ai_available === false ? <AiBanner reason={a.ai_reason} /> : null}
                {a.notice && a.ai_available !== false ? <NoticeBanner text={a.notice} /> : null}
                {a.blockers && a.blockers.length > 0 ? <NoticeBanner text={a.blockers.join("; ")} /> : null}
                <div className="grid gap-3 xl:grid-cols-2">
                  {a.verification ? <CitationsBanner verification={a.verification} /> : null}
                  {a.coverage ? <CoverageBanner coverage={a.coverage} /> : null}
                </div>

                <section aria-labelledby="rules-title" className="space-y-3">
                  <div className="flex flex-wrap items-center gap-2 pt-2">
                    <h3 id="rules-title" className="text-[18px] font-semibold text-plum">
                      Rules
                    </h3>
                    <Count n={rules.filter((r) => r.status === "approved" || r.status === "edited").length} label="approved" tone="gate" />
                    <Count n={undecidedRules} label="to decide" tone="decision" />
                    <Count n={rules.filter((r) => r.status === "rejected").length} label="rejected" tone="error" />
                  </div>
                  {rules.map((r) => (
                    <RuleCard
                      key={`${r.id}:${r.text}`}
                      rule={r}
                      onDecide={async (id, body) => refresh(await api.patchRule(id, body))}
                    />
                  ))}
                </section>

                <section aria-labelledby="findings-title" className="space-y-3">
                  <div className="flex flex-wrap items-center gap-2 pt-2">
                    <h3 id="findings-title" className="text-[18px] font-semibold text-plum">
                      Findings
                    </h3>
                    <Count n={findings.length - undecidedFindings} label="decided" tone="gate" />
                    <Count n={undecidedFindings} label="to decide" tone="decision" />
                  </div>
                  {findings.length === 0 ? <Empty title="No findings">Code and the risk reviewer raised nothing.</Empty> : null}
                  {findings.map((f) => (
                    <FindingCard
                      key={`${f.id}:${f.decision ?? ""}:${f.note ?? ""}`}
                      finding={f}
                      onDecide={async (id, body) => refresh(await api.patchFinding(id, body))}
                    />
                  ))}
                </section>
              </>
            )}
          </div>
        </div>
      )}
      {processId ? <AuditDrawer processId={processId} open={auditOpen} onClose={() => setAuditOpen(false)} /> : null}
    </div>
  );
}
