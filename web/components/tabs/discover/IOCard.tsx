"use client";

import { ArrowDownToLine, ArrowUpFromLine, Database, Send, Workflow, Zap } from "lucide-react";
import { Pill } from "@/components/ui/Pill";
import { useIOCard, useScenarios } from "@/lib/queries";
import type { FieldNode, ScenarioSet } from "@/lib/types";

function Section({ title, icon: Icon, children }: { title: string; icon: typeof Zap; children: React.ReactNode }) {
  return (
    <div>
      <p className="label flex items-center gap-1.5">
        <Icon size={13} aria-hidden /> {title}
      </p>
      <div className="mt-1.5">{children}</div>
    </div>
  );
}

function Fields({ node, depth = 0 }: { node: FieldNode; depth?: number }) {
  const children = node.children ?? [];
  return (
    <ul className={depth ? "ml-4 border-l border-line pl-3" : ""}>
      {children.map((c, i) => (
        <li key={`${c.name}-${i}`} className="py-0.5">
          <span className="text-[13.5px] font-medium text-plum">{c.name}</span>
          <span className="ml-2 font-mono text-[12px] text-slate">{c.type}</span>
          {c.required ? <span className="ml-1.5 text-[11px] text-coral">required</span> : null}
          {c.repeated ? <span className="ml-1.5 text-[11px] text-slate">repeated</span> : null}
          {c.children && c.children.length > 0 ? <Fields node={c} depth={depth + 1} /> : null}
        </li>
      ))}
    </ul>
  );
}

function Contract({ node }: { node: FieldNode }) {
  if (!node || Object.keys(node).length === 0) return <p className="text-[14px] text-slate">None</p>;
  return (
    <div className="rounded-btn bg-page p-3">
      <p className="font-mono text-[12.5px] font-semibold text-plum">{node.name ?? "root"}</p>
      {node.children && node.children.length > 0 ? <Fields node={node} depth={1} /> : null}
    </div>
  );
}

function TriggerLine({ trigger }: { trigger: Record<string, unknown> }) {
  const kind = String(trigger.kind ?? "none");
  const entries = Object.entries(trigger).filter(([k, v]) => k !== "kind" && v !== null && v !== "" && typeof v !== "object");
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Pill tone="plum">{kind}</Pill>
      {entries.map(([k, v]) => (
        <span key={k} className="text-[13px]">
          <span className="text-slate">{k}</span> <span className="font-mono text-[12.5px] text-ink">{String(v)}</span>
        </span>
      ))}
    </div>
  );
}

const EFFECT_ICON: Record<string, typeof Zap> = { table_read: Database, table_write: Database, publish: Send };

export function IOCard({ processId }: { processId: string }) {
  const card = useIOCard(processId);
  const scenarios = useScenarios(processId);
  if (!card.data) {
    return <section className="card p-5 text-[14px] text-slate">{card.error ? (card.error as Error).message : "Loading I/O card"}</section>;
  }
  const c = card.data;
  return (
    <section className="card p-5" data-testid="io-card">
      <div className="flex items-baseline justify-between">
        <h3 className="panel-title">I/O card</h3>
        <span className="text-[13px] text-slate">Built from the parsed model</span>
      </div>
      <div className="mt-4 grid gap-6 lg:grid-cols-2">
        <div className="space-y-5">
          <Section title="Trigger" icon={Zap}>
            <TriggerLine trigger={c.trigger} />
          </Section>
          <Section title="Request" icon={ArrowDownToLine}>
            <Contract node={c.request_contract} />
          </Section>
          <Section title="Response" icon={ArrowUpFromLine}>
            <Contract node={c.response_contract} />
          </Section>
        </div>
        <div className="space-y-5">
          <Section title="Side effects" icon={Database}>
            {c.side_effects.length === 0 ? (
              <p className="text-[14px] text-slate">None</p>
            ) : (
              <ul className="space-y-2">
                {c.side_effects.map((s, i) => {
                  const kind = String(s.kind);
                  const Icon = EFFECT_ICON[kind] ?? Workflow;
                  const target = String(s.table ?? s.destination ?? s.service ?? s.process ?? s.url ?? "");
                  const shared = Array.isArray(s.shared_with) ? (s.shared_with as string[]) : [];
                  return (
                    <li key={i} className="rounded-btn border border-line p-2.5 text-[13px]">
                      <div className="flex flex-wrap items-center gap-2">
                        <Icon size={14} className="text-slate" aria-hidden />
                        <Pill tone={kind.endsWith("write") ? "decision" : kind === "publish" ? "coral" : "det"}>
                          {kind.replace("_", " ")}
                        </Pill>
                        <span className="font-mono font-semibold text-plum">{target}</span>
                        {s.operation ? <span className="text-slate">{String(s.operation)}</span> : null}
                        {s.messaging_style ? <span className="text-slate">{String(s.messaging_style)}</span> : null}
                        <span className="ml-auto text-[12px] text-slate">step {String(s.activity ?? "")}</span>
                      </div>
                      {typeof s.sql === "string" ? (
                        <pre className="mt-1.5 whitespace-pre-wrap rounded bg-page p-2 font-mono text-[11.5px] text-ink">{s.sql}</pre>
                      ) : null}
                      {shared.length > 0 ? (
                        <p className="mt-1 text-[12px] text-slate">Shared with {shared.join(", ")}</p>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </Section>
          <div className="grid grid-cols-2 gap-4">
            <Section title="Faults" icon={Zap}>
              {c.faults.length ? (
                <div className="flex flex-wrap gap-1">
                  {c.faults.map((f) => (
                    <Pill key={f} tone="error">
                      {f}
                    </Pill>
                  ))}
                </div>
              ) : (
                <p className="text-[14px] text-slate">None</p>
              )}
            </Section>
            <Section title="Subprocesses" icon={Workflow}>
              {c.subprocesses.length ? (
                <div className="flex flex-wrap gap-1">
                  {c.subprocesses.map((f) => (
                    <Pill key={f} tone="purple">
                      {f}
                    </Pill>
                  ))}
                </div>
              ) : (
                <p className="text-[14px] text-slate">None</p>
              )}
            </Section>
          </div>
        </div>
      </div>
      <div className="mt-6 border-t border-line pt-4" data-testid="sample-outputs">
        <p className="label">Sample outputs</p>
        <p className="mt-0.5 text-[13px] font-medium text-decision">Expected output derived from the TIBCO model</p>
        <SampleOutputs set={scenarios.data} loading={scenarios.isLoading} error={scenarios.error} />
      </div>
    </section>
  );
}

const KIND_TONE: Record<string, "gate" | "decision" | "error" | "purple" | "det"> = {
  happy: "gate",
  branch: "decision",
  fault: "error",
  external_failure: "error",
  sme: "purple",
};

function SampleOutputs({ set, loading, error }: { set: ScenarioSet | null | undefined; loading: boolean; error: unknown }) {
  if (loading) {
    return <p className="mt-2 text-[14px] text-slate">Deriving scenarios from the TIBCO model</p>;
  }
  if (error) {
    return (
      <p className="mt-2 rounded-btn bg-error-bg px-4 py-3 text-[14px] text-error">
        Scenarios are not available: {(error as Error).message}
      </p>
    );
  }
  if (!set || set.scenarios.length === 0) {
    return (
      <p className="mt-2 rounded-btn bg-warm px-4 py-4 text-[14px] text-slate">
        No scenarios yet. The parity evaluator derives one example per scenario from the parsed model.
      </p>
    );
  }
  return (
    <>
      <ul className="mt-2 grid gap-2 md:grid-cols-2">
        {set.scenarios.map((s) => {
          const exp = s.expected;
          const effects = exp?.side_effects ?? [];
          return (
            <li key={s.id} className="rounded-btn border border-line p-3" data-testid="sample-output">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-[13.5px] font-medium text-plum">{s.name}</p>
                {s.kind ? <Pill tone={KIND_TONE[s.kind] ?? "det"}>{s.kind.replace("_", " ")}</Pill> : null}
                {s.origin === "sme" ? <Pill tone="purple">SME</Pill> : null}
              </div>
              {exp?.fault ? (
                <p className="mt-1 text-[13px] text-error">
                  Fault <span className="font-mono">{exp.fault.name}</span>
                </p>
              ) : null}
              {exp?.output && Object.keys(exp.output).length > 0 ? (
                <pre className="mt-1 max-h-48 overflow-auto rounded bg-page p-2 font-mono text-[11.5px]">
                  {JSON.stringify(exp.output, null, 2)}
                </pre>
              ) : (
                <p className="mt-1 text-[12.5px] text-slate">No response body; the outcome is in the side effects.</p>
              )}
              {effects.length > 0 ? (
                <ul className="mt-1 space-y-0.5 text-[12px] text-slate">
                  {effects.map((e, i) => (
                    <li key={i}>
                      {e.kind.replace("_", " ")} <span className="font-mono text-ink">{e.target}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ul>
      {set.notes.length > 0 ? (
        <ul className="mt-2 list-disc pl-5 text-[12.5px] text-slate">
          {set.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      ) : null}
    </>
  );
}
