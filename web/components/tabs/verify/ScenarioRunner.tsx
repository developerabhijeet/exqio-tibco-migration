"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Loader2, Plus, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { useScenarioList } from "@/lib/buildQueries";
import type { JobStream } from "@/lib/jobs";
import { JobLog } from "@/components/jobs/JobLog";
import { ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { errorText, Panel } from "@/components/tabs/build/Panel";

const KIND: Record<string, "gate" | "purple" | "error" | "decision" | "ai"> = {
  happy: "gate",
  branch: "purple",
  fault: "error",
  external_failure: "decision",
  sme: "ai",
};

export function KindPill({ kind }: { kind: string }) {
  return (
    <Pill tone={KIND[kind] ?? "ai"} testId="kind-pill">
      {kind}
    </Pill>
  );
}

function preview(input: unknown): string {
  const text = JSON.stringify(input);
  return text.length > 140 ? `${text.slice(0, 137)}...` : text;
}

/** Scenarios from the TIBCO model (and SME additions), and the verify run against the service. */
export function ScenarioRunner({
  processId,
  buildId,
  running,
  stream,
  onRun,
}: {
  processId: string;
  buildId: string | null;
  running: boolean;
  stream: JobStream | null;
  onRun: () => Promise<void>;
}) {
  const qc = useQueryClient();
  const list = useScenarioList(processId);
  const [busy, setBusy] = useState<"generate" | "add" | "run" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const scenarios = list.data?.scenarios ?? [];
  const [input, setInput] = useState<string>("");

  const generate = async () => {
    setBusy("generate");
    setError(null);
    try {
      const out = await api.generateScenarios(processId);
      if (out) qc.setQueryData(["scenario-list", processId], out);
      qc.invalidateQueries({ queryKey: ["scenario-list", processId] });
      qc.invalidateQueries({ queryKey: ["test-data", processId] });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const add = async () => {
    setError(null);
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(input || "{}");
    } catch {
      setError("The input is not valid JSON.");
      return;
    }
    setBusy("add");
    try {
      await api.addScenario(processId, name.trim(), parsed);
      setAdding(false);
      setName("");
      qc.invalidateQueries({ queryKey: ["scenario-list", processId] });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const run = async () => {
    setBusy("run");
    setError(null);
    try {
      await onRun();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const verifying = stream?.status === "running";
  const runBlocked = !buildId ? "No build yet." : !running ? "Start the service first: verify calls the running service." : null;

  return (
    <Panel
      title="Scenarios"
      eyebrow="Same inputs, same outputs"
      testId="scenario-runner"
      id="scenarios"
      actions={
        <>
          <button type="button" className="btn-secondary" onClick={generate} disabled={!!busy}>
            {busy === "generate" ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <RefreshCw size={15} aria-hidden />}
            Generate scenarios
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              setAdding(!adding);
              if (!input) setInput(JSON.stringify(scenarios[0]?.input ?? { parameters: {} }, null, 2));
            }}
            disabled={!!busy}
          >
            <Plus size={15} aria-hidden /> Add scenario
          </button>
          <span title={runBlocked ?? undefined}>
            <button type="button" className="btn-primary" onClick={run} disabled={!!runBlocked || verifying || !!busy || scenarios.length === 0}>
              {verifying || busy === "run" ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <FlaskConical size={15} aria-hidden />}
              Run verify
            </button>
          </span>
        </>
      }
    >
      {runBlocked ? <p className="mb-2 text-[13px] text-slate">Run verify: {runBlocked}</p> : null}
      {error ? <ErrorNote error={error} /> : null}
      {list.error ? <ErrorNote error={list.error} /> : null}
      {adding ? (
        <form
          className="mb-3 grid gap-2 rounded-btn border border-line bg-page p-3 md:grid-cols-[260px_minmax(0,1fr)_auto]"
          onSubmit={(e) => {
            e.preventDefault();
            add();
          }}
        >
          <label className="text-[13px] text-slate">
            Name
            <input className="input mt-1" value={name} onChange={(e) => setName(e.target.value)} placeholder="dealer with a cancelled deal" required />
          </label>
          <label className="text-[13px] text-slate">
            Input (JSON, the same shape as the generated scenarios)
            <textarea className="input mt-1 h-28 font-mono text-[12.5px]" value={input} onChange={(e) => setInput(e.target.value)} />
          </label>
          <div className="flex items-end gap-2">
            <button type="submit" className="btn-primary" disabled={busy === "add" || !name.trim()}>
              {busy === "add" ? <Loader2 size={15} className="animate-spin" aria-hidden /> : null}
              Add
            </button>
            <button type="button" className="btn-ghost" onClick={() => setAdding(false)}>
              Cancel
            </button>
          </div>
          <p className="text-[12px] text-slate md:col-span-3">
            The expected result is computed by the evaluator from the TIBCO model, never typed in.
          </p>
        </form>
      ) : null}
      {scenarios.length === 0 ? (
        <p className="text-[14px] text-slate">
          {list.isLoading ? "Loading scenarios." : "No scenarios yet. Generate scenarios builds one per branch, the happy path and the faults from the model."}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-btn border border-line">
          <table className="w-full min-w-[640px] text-[13px]">
            <thead>
              <tr className="table-head">
                <th className="px-3 py-2">Kind</th>
                <th className="px-3 py-2">Name</th>
                <th className="px-3 py-2">Inputs</th>
                <th className="px-3 py-2">Origin</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {scenarios.map((s) => (
                <tr key={s.id} data-testid="scenario-row" className="align-top">
                  <td className="px-3 py-2">
                    <KindPill kind={s.kind} />
                  </td>
                  <td className="px-3 py-2">
                    <p className="font-medium text-ink">{s.name}</p>
                    <p className="font-mono text-[11px] text-slate">{s.key}</p>
                  </td>
                  <td className="px-3 py-2 font-mono text-[12px]">{preview(s.input)}</td>
                  <td className="px-3 py-2 text-slate">{s.origin === "sme" ? `SME ${s.created_by ?? ""}` : "model"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(list.data?.notes ?? []).length > 0 ? (
        <ul className="mt-2 space-y-0.5 text-[12.5px] text-decision">
          {list.data!.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
      {stream ? (
        <div className="mt-4" data-testid="verify-log" data-status={stream.status}>
          <JobLog title="Verify run" stream={stream} stages={["scenarios", "run", "persist"]} testId="verify-job-log" idle={null} />
        </div>
      ) : null}
    </Panel>
  );
}
