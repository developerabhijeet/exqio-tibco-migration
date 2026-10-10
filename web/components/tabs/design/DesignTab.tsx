"use client";

import { useState } from "react";
import { FileDown, History, Loader2, Wand2 } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useJob } from "@/lib/jobs";
import { useAnalysis, useDesign, useGates, useProcesses } from "@/lib/queries";
import { useAppState } from "@/lib/state";
import { Empty, ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { JobLog } from "@/components/jobs/JobLog";
import { GateButton } from "@/components/gates/GateButton";
import { AuditDrawer } from "@/components/gates/AuditDrawer";
import { ProcessPicker } from "@/components/tabs/ProcessPicker";
import { shortKey } from "@/components/tabs/validate/FindingCard";
import { ConfigTable } from "./ConfigTable";
import { DecisionList, OpenQuestions } from "./DecisionList";
import { MappingTable } from "./MappingTable";
import { SequenceDiagram } from "./SequenceDiagram";
import { SolutionTree } from "./SolutionTree";

const STAGES = ["skeleton", "memory", "designer", "persist"];

function Panel({
  title,
  eyebrow,
  children,
  testId,
  className = "",
}: {
  title: string;
  eyebrow?: string;
  children: React.ReactNode;
  testId?: string;
  className?: string;
}) {
  return (
    <section className={`card break-inside-avoid p-5 ${className}`} data-testid={testId}>
      {eyebrow ? <p className="label">{eyebrow}</p> : null}
      <h3 className="text-[18px] font-semibold text-plum">{title}</h3>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function Json({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="text-slate">none</span>;
  if (typeof value !== "object") return <span>{String(value)}</span>;
  return (
    <span className="font-mono text-[12px]">
      {Object.entries(value as Record<string, unknown>)
        .map(([k, v]) => `${k} ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
        .join(", ")}
    </span>
  );
}

export function DesignTab() {
  const { projectId, processId } = useAppState();
  const processes = useProcesses(projectId);
  const analysis = useAnalysis(processId);
  const gates = useGates(processId);
  const design = useDesign(processId);
  const { stream, watch } = useJob("design", processId);
  const [runError, setRunError] = useState<string | null>(null);
  const [auditOpen, setAuditOpen] = useState(false);

  const proc = processes.data?.find((p) => p.id === processId) ?? null;
  const running = stream?.status === "running";
  const rec = design.data?.record ?? null;
  const g3 = (gates.data ?? []).find((g) => g.gate === "G3");

  const generate = async () => {
    if (!processId) return;
    setRunError(null);
    try {
      const { job_id } = await api.generateDesign(processId);
      watch(job_id);
    } catch (e) {
      setRunError(e instanceof ApiError ? e.detail : String(e));
    }
  };

  return (
    <div className="space-y-5" data-testid="design-tab">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Step 4</p>
          <h2 className="panel-title">Design</h2>
          <p className="mt-1 max-w-[720px] text-[14px] text-slate">
            Code maps every TIBCO step to a C# file, class and method from the pattern catalogue. The designer model writes
            the narrative. An architect approves the record (G3).
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 print:hidden">
          <ProcessPicker />
          <button type="button" className="btn-secondary" disabled={!processId} onClick={() => setAuditOpen(true)}>
            <History size={16} aria-hidden /> Audit trail
          </button>
          <button type="button" className="btn-secondary" disabled={!rec} onClick={() => window.print()}>
            <FileDown size={16} aria-hidden /> Export PDF
          </button>
          <button type="button" className="btn-primary" disabled={!processId || running} onClick={generate}>
            {running ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Wand2 size={16} aria-hidden />}
            Generate design
          </button>
        </div>
      </div>

      {!processId ? (
        <Empty title="No process open">Pick a process above, or open one from Discover.</Empty>
      ) : (
        <>
          <div className={`grid items-start gap-4 print:hidden ${stream || runError ? "lg:grid-cols-[1fr_420px]" : ""}`}>
            <GateButton
              processId={processId}
              gate={g3}
              name="G3"
              started={!!rec}
              hint="The architect approves this design record; the approval binds to its design hash."
            />
            {stream || runError ? (
              <div className="space-y-2">
                {runError ? <ErrorNote error={runError} /> : null}
                {stream ? <JobLog title="Design job" stream={stream} stages={STAGES} testId="design-log" idle={null} /> : null}
              </div>
            ) : null}
          </div>

          {design.error ? <ErrorNote error={design.error} /> : null}
          {!rec ? (
            design.isLoading ? null : (
              <Empty title="No design record yet">
                Generate design once G2 is approved for {proc?.name ?? "this process"}. Code builds the mapping; the model only
                writes the narrative.
              </Empty>
            )
          ) : (
            <div className="space-y-4" data-testid="design-record">
              {design.data?.stale ? (
                <p role="status" className="rounded-card border border-decision/30 bg-decision-bg px-4 py-3 text-[14px] text-decision">
                  This record is out of date: {design.data.stale_reason ?? "the spec hash changed"}. Generate the design again
                  before approving G3.
                </p>
              ) : null}
              <div className="flex flex-wrap items-center gap-x-5 gap-y-1 rounded-card border border-line bg-white px-5 py-3 text-[13px]">
                <span className="font-semibold text-plum">{proc?.name}</span>
                <span data-testid="design-hash" title={rec.design_hash}>
                  <span className="label mr-1.5">Design hash</span>
                  <span className="font-mono">{rec.design_hash.slice(0, 16)}</span>
                </span>
                {rec.spec_hash ? (
                  <span title={rec.spec_hash}>
                    <span className="label mr-1.5">Spec hash</span>
                    <span className="font-mono">{rec.spec_hash.slice(0, 16)}</span>
                  </span>
                ) : null}
                {design.data?.version ? <span>version {design.data.version}</span> : null}
                <span>{rec.mapping.length} mapping rows</span>
                {design.data?.created_at ? (
                  <span className="text-slate">generated {new Date(design.data.created_at).toLocaleString()}</span>
                ) : null}
              </div>

              <Panel title="Narrative" eyebrow="Designer" testId="design-narrative">
                {rec.narrative ? (
                  <div className="space-y-2 text-[15px] leading-relaxed text-ink">
                    {rec.narrative.split(/\n{2,}/).map((p, i) => (
                      <p key={i} className="whitespace-pre-wrap">
                        {p}
                      </p>
                    ))}
                  </div>
                ) : (
                  <p className="text-[14px] text-slate">
                    No narrative: the designer model did not run{design.data?.ai_reason ? ` (${design.data.ai_reason})` : ""}.
                    Everything below is generated by code.
                  </p>
                )}
              </Panel>

              <Panel title="Mapping" eyebrow="Step to code" testId="design-mapping">
                <MappingTable mapping={rec.mapping} analysis={analysis.data} />
              </Panel>

              <Panel title="Sequence" eyebrow="Calls in order">
                <SequenceDiagram sequence={rec.sequence} />
              </Panel>

              <div className="grid gap-4 xl:grid-cols-2">
                <Panel title="Solution layout" eyebrow="Projects and files">
                  <SolutionTree solution={rec.solution} />
                </Panel>
                <Panel title="Contracts" eyebrow="Request, response and fault records">
                  {rec.contracts.length === 0 ? (
                    <p className="text-[14px] text-slate">No contracts.</p>
                  ) : (
                    <ul className="space-y-2 text-[13.5px]">
                      {rec.contracts.map((c) => (
                        <li key={c.contract_id} className="rounded-btn border border-line p-2.5">
                          <p className="font-mono text-[12.5px] font-semibold text-plum">{c.contract_id}</p>
                          <p className="mt-0.5">
                            <span className="label mr-1.5">Records</span>
                            <span className="font-mono text-[12px]">{(c.record_names ?? []).join(", ") || "none"}</span>
                          </p>
                          {c.namespace ? <p className="font-mono text-[12px] text-slate">{c.namespace}</p> : null}
                          {c.file ? <p className="font-mono text-[12px] text-slate">{c.file}</p> : null}
                        </li>
                      ))}
                    </ul>
                  )}
                </Panel>
              </div>

              <Panel title="Configuration" eyebrow="Substitution variables">
                <ConfigTable config={rec.config} ignored={rec.config_ignored} />
              </Panel>

              <div className="grid gap-4 xl:grid-cols-2">
                <Panel title="Messaging" eyebrow="Azure Service Bus entities">
                  {rec.messaging.length === 0 ? (
                    <p className="text-[14px] text-slate">No queues or topics.</p>
                  ) : (
                    <table className="w-full text-[13.5px]">
                      <thead>
                        <tr className="table-head">
                          <th className="px-3 py-2">Kind</th>
                          <th className="px-3 py-2">Name</th>
                          <th className="px-3 py-2">Direction</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-line">
                        {rec.messaging.map((m) => (
                          <tr key={`${m.kind}:${m.name}:${m.direction}`}>
                            <td className="px-3 py-2">{m.kind}</td>
                            <td className="px-3 py-2 font-mono text-[12.5px]">{m.name}</td>
                            <td className="px-3 py-2">{m.direction === "in" ? "consumed" : "published"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </Panel>
                <Panel title="Resilience" eyebrow="Timeouts, retries and breakers">
                  {rec.resilience.length === 0 ? (
                    <p className="text-[14px] text-slate">No resilience settings carried from TIBCO resources.</p>
                  ) : (
                    <ul className="space-y-2 text-[13.5px]">
                      {rec.resilience.map((r) => (
                        <li key={r.resource_id} className="rounded-btn border border-line p-2.5">
                          <p className="font-mono text-[12.5px] font-semibold text-plum">{r.resource_id}</p>
                          <p>
                            <span className="label mr-1.5">Timeout</span>
                            {r.timeout_ms != null ? `${r.timeout_ms} ms` : "none"}
                          </p>
                          <p>
                            <span className="label mr-1.5">Retry</span>
                            <Json value={r.retry} />
                          </p>
                          <p>
                            <span className="label mr-1.5">Breaker</span>
                            <Json value={r.breaker} />
                          </p>
                        </li>
                      ))}
                    </ul>
                  )}
                </Panel>
              </div>

              <div className="grid gap-4 xl:grid-cols-2">
                <Panel title="Decisions" eyebrow="Choice, alternatives, reason">
                  <DecisionList decisions={rec.decisions} />
                </Panel>
                <div className="space-y-4">
                  <Panel title="Open questions" eyebrow="For the architect">
                    <OpenQuestions questions={rec.open_questions} />
                  </Panel>
                  <Panel title="Expected differences" eyebrow="Findings decided Fix at G2" testId="expected-differences">
                    {rec.expected_differences.length === 0 ? (
                      <p className="text-[14px] text-slate">None: the .NET service keeps the TIBCO behavior everywhere.</p>
                    ) : (
                      <ul className="space-y-2 text-[14px]">
                        {rec.expected_differences.map((d) => (
                          <li key={d.finding_id} className="flex gap-2">
                            <Pill tone="decision" title={d.finding_id}>
                              {shortKey(d.finding_id)}
                            </Pill>
                            <span>{d.description}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </Panel>
                </div>
              </div>
            </div>
          )}
        </>
      )}
      {processId ? <AuditDrawer processId={processId} open={auditOpen} onClose={() => setAuditOpen(false)} /> : null}
    </div>
  );
}
