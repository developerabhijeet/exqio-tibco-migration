"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Rocket, ScrollText } from "lucide-react";
import { api } from "@/lib/api";
import { useInfra, usePlan } from "@/lib/buildQueries";
import type { JobStream } from "@/lib/jobs";
import { JobLog } from "@/components/jobs/JobLog";
import { ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { errorText, shortHash } from "@/components/tabs/build/Panel";
import { ValidationRows } from "@/components/tabs/build/InfraPanel";

const CodeView = dynamic(() => import("@/components/canvas/CodeView"), { ssr: false });

const STATUS: Record<string, { tone: "gate" | "error" | "decision" | "det" | "purple"; text: string }> = {
  connect_later: { tone: "decision", text: "connect later" },
  acknowledged: { tone: "gate", text: "connect later acknowledged" },
  running: { tone: "purple", text: "planning" },
  planned: { tone: "gate", text: "planned" },
  failed: { tone: "error", text: "plan failed" },
};

const sentence = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

export const APPLY_REASON = "Apply is not part of this build";

/** Terragrunt plan per unit, or the connect-later state with the last validation and the assets. */
export function PlanView({
  buildId,
  stream,
  onPlanJob,
}: {
  buildId: string;
  stream: JobStream | null;
  onPlanJob: (jobId: string) => void;
}) {
  const qc = useQueryClient();
  const plan = usePlan(buildId);
  const infra = useInfra(buildId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const p = plan.data;
  const st = p ? (STATUS[p.status] ?? { tone: "det" as const, text: p.status }) : null;

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const out = await api.startPlan(buildId);
      if (out.connected) onPlanJob(out.job_id);
      else qc.setQueryData(["plan", buildId], out.plan);
      qc.invalidateQueries({ queryKey: ["plan", buildId] });
      qc.invalidateQueries({ queryKey: ["gates"] });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const kinds = new Map<string, number>();
  for (const f of infra.data?.files ?? []) kinds.set(f.kind, (kinds.get(f.kind) ?? 0) + 1);
  const offline = !p || p.status === "connect_later" || p.status === "acknowledged";

  return (
    <section className="card p-5" data-testid="plan-view" data-status={p?.status ?? "none"}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="label">terragrunt run --all -- plan</p>
          <h3 className="text-[18px] font-semibold text-plum">Plan</h3>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {st ? <Pill tone={st.tone}>{st.text}</Pill> : null}
          <button type="button" className="btn-primary" onClick={run} disabled={busy || stream?.status === "running"}>
            {busy || stream?.status === "running" ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <ScrollText size={15} aria-hidden />}
            Run plan
          </button>
          <span title={p?.apply?.reason ?? APPLY_REASON}>
            <button type="button" className="btn-secondary" disabled>
              <Rocket size={15} aria-hidden /> Apply
            </button>
          </span>
        </div>
      </div>
      <p className="mt-2 text-[13px] text-slate">
        <span className="font-medium text-plum">{APPLY_REASON}.</span>{" "}
        {sentence(p?.apply?.reason?.replace(/^apply is not in this build:\s*/i, "") ?? "The pipelines apply after review.")}
      </p>
      {error ? (
        <div className="mt-3">
          <ErrorNote error={error} />
        </div>
      ) : null}
      {stream ? (
        <div className="mt-3">
          <JobLog title="Plan job" stream={stream} stages={["plan"]} testId="plan-log" idle={null} />
        </div>
      ) : null}

      {!p ? (
        <p className="mt-3 text-[14px] text-slate">
          {plan.isLoading ? "Loading." : "No plan yet. Run plan records the connect-later state when Azure is not connected."}
        </p>
      ) : (
        <div className="mt-4 space-y-4">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[13px]">
            <span>
              <span className="label mr-1.5">Plan hash</span>
              <span className="font-mono">{shortHash(p.plan_hash, 16)}</span>
            </span>
            {p.created_at ? <span className="text-slate">recorded {new Date(p.created_at).toLocaleString()}</span> : null}
            {p.decided_by ? (
              <span>
                <span className="label mr-1.5">Acknowledged by</span>
                <span className="font-medium text-plum">{p.decided_by}</span>
              </span>
            ) : null}
          </div>
          {offline ? (
            <div className="rounded-card border border-decision/30 bg-decision-bg px-4 py-3 text-[14px] text-decision">
              <p className="font-semibold">{p.status === "acknowledged" ? "Connect later recorded" : "Not planned"}</p>
              {p.note ? <p className="mt-0.5">{p.status === "acknowledged" ? `Note: ${p.note}` : p.note}</p> : null}
            </div>
          ) : null}

          {p.summary ? (
            <div className="overflow-x-auto rounded-btn border border-line">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="table-head">
                    <th className="px-3 py-2">Unit</th>
                    <th className="px-3 py-2 text-right">Add</th>
                    <th className="px-3 py-2 text-right">Change</th>
                    <th className="px-3 py-2 text-right">Destroy</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {p.summary.units.map((u) => (
                    <tr key={u.unit} data-testid="plan-unit">
                      <td className="px-3 py-1.5 font-mono text-[12px]">{u.unit}</td>
                      <td className="px-3 py-1.5 text-right text-gate">{u.add}</td>
                      <td className="px-3 py-1.5 text-right text-decision">{u.change}</td>
                      <td className="px-3 py-1.5 text-right text-error">{u.destroy}</td>
                    </tr>
                  ))}
                  <tr className="font-semibold">
                    <td className="px-3 py-1.5">Total</td>
                    <td className="px-3 py-1.5 text-right">{p.summary.add}</td>
                    <td className="px-3 py-1.5 text-right">{p.summary.change}</td>
                    <td className="px-3 py-1.5 text-right">{p.summary.destroy}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          ) : null}
          {p.output ? <CodeView value={p.output} language="plaintext" height={360} /> : null}

          {offline ? (
            <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
              <div data-testid="last-validation">
                <p className="mb-2 text-[14px] font-semibold text-plum">Last validation</p>
                <ValidationRows validation={p.last_validation} />
              </div>
              <div>
                <p className="mb-2 text-[14px] font-semibold text-plum">Generated assets</p>
                <ul className="divide-y divide-line rounded-btn border border-line text-[13px]">
                  {[...kinds.entries()].map(([k, n]) => (
                    <li key={k} className="flex justify-between px-3 py-1.5">
                      <span>{k}</span>
                      <span className="font-semibold text-plum">{n}</span>
                    </li>
                  ))}
                  {kinds.size === 0 ? <li className="px-3 py-1.5 text-slate">No infrastructure files.</li> : null}
                </ul>
                <p className="mt-1.5 text-[12.5px] text-slate">The files themselves are in Build, under Infrastructure.</p>
              </div>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

