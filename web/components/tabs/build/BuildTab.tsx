"use client";

import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Hammer, History, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useBuild, useBuildFiles, useBuilds } from "@/lib/buildQueries";
import { buildStepRows } from "@/lib/buildSteps";
import { useJob } from "@/lib/jobs";
import { useDesign, useGates, useProcesses } from "@/lib/queries";
import { useAppState } from "@/lib/state";
import { Empty, ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { GateButton, gateDisplayState } from "@/components/gates/GateButton";
import { AuditDrawer } from "@/components/gates/AuditDrawer";
import { ProcessPicker } from "@/components/tabs/ProcessPicker";
import { BuildJobLog, BuildSteps } from "./BuildLog";
import { InfraPanel } from "./InfraPanel";
import { MetricsRadar } from "./MetricsRadar";
import { errorText, OutcomePill, shortHash, useScrollToHash } from "./Panel";
import { ReviewPanel } from "./ReviewPanel";
import { SourceTargetExplorer } from "./SourceTargetExplorer";
import { TestResults } from "./TestResults";

/** Queries that change when a build finishes. */
export const BUILD_QUERIES = ["build", "build-files", "build-tests", "build-reviews", "infra", "run", "plan", "parity"];

export function BuildTab() {
  const qc = useQueryClient();
  const { projectId, processId } = useAppState();
  useScrollToHash();
  const processes = useProcesses(projectId);
  const gates = useGates(processId);
  const design = useDesign(processId);
  const builds = useBuilds(processId);
  const { stream, watch } = useJob("build", processId);
  const [started, setStarted] = useState<{ processId: string; buildId: string } | null>(null);
  const [runtime, setRuntime] = useState<"container_app" | "aks">("container_app");
  const [runError, setRunError] = useState<string | null>(null);
  const [auditOpen, setAuditOpen] = useState(false);

  const proc = processes.data?.find((p) => p.id === processId) ?? null;
  const buildId = (started?.processId === processId ? started.buildId : null) ?? builds.data?.[0]?.id ?? null;
  const build = useBuild(buildId);
  const files = useBuildFiles(build.data?.finished_at ? buildId : null);
  const gateList = gates.data ?? [];
  const g3 = gateList.find((g) => g.gate === "G3");
  const g4 = gateList.find((g) => g.gate === "G4");
  const running = stream?.status === "running";
  const finished = !!build.data?.finished_at;

  // Replay the log of a build that ran before this page opened (the stream returns every event).
  const jobId = build.data?.job_id ?? null;
  useEffect(() => {
    if (jobId && build.data?.id === buildId && stream?.jobId !== jobId && stream?.status !== "running") watch(jobId);
  }, [jobId, buildId, build.data?.id, stream?.jobId, stream?.status, watch]);

  // A finished job refreshes the build views, the gates and the readiness rows.
  const status = stream?.status;
  useEffect(() => {
    if (status !== "done" && status !== "failed") return;
    qc.invalidateQueries({ queryKey: ["builds", processId] });
    for (const q of BUILD_QUERIES) qc.invalidateQueries({ queryKey: [q] });
    qc.invalidateQueries({ queryKey: ["gates", processId] });
    qc.invalidateQueries({ queryKey: ["readiness"] });
  }, [status, processId, qc]);

  const rows = useMemo(
    () =>
      buildStepRows(
        stream && stream.jobId === jobId ? stream.events : stream && !jobId ? stream.events : [],
        stream && (stream.jobId === jobId || !jobId) ? stream.status : null,
        build.data ?? null,
        files.data?.counts?.infra ?? 0,
      ),
    [stream, jobId, build.data, files.data],
  );
  const restoreFailed = finished && rows.find((r) => r.name === "restore")?.status === "failed";
  const restoreReason = build.data?.steps.find((s) => s.name === "restore")?.log.split("\n").find((l) => l.trim());

  const start = async () => {
    if (!processId) return;
    setRunError(null);
    try {
      const r = await api.startBuild(processId, runtime);
      setStarted({ processId, buildId: r.build_id });
      watch(r.job_id);
      qc.invalidateQueries({ queryKey: ["builds", processId] });
    } catch (e) {
      setRunError(errorText(e));
    }
  };

  const g3State = gateDisplayState(g3, !!design.data);
  const buildStatus = running ? "running" : (build.data?.status ?? null);

  return (
    <div className="space-y-5" data-testid="build-tab">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Step 5</p>
          <h2 className="panel-title">Build</h2>
          <p className="mt-1 max-w-[760px] text-[14px] text-slate">
            Code writes the solution and its tests from the approved design record before any body, the Code Builder fills
            what is left, then dotnet restores, builds, formats and tests. Infrastructure is generated and validated in the
            same job. An engineer approves the build (G4).
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ProcessPicker />
          <label className="flex items-center gap-2 text-[13.5px] text-slate">
            Runtime
            <select
              className="rounded-btn border border-line bg-white px-2.5 py-1.5 text-[14px] font-medium text-plum"
              value={runtime}
              onChange={(e) => setRuntime(e.target.value as "container_app" | "aks")}
              disabled={running}
            >
              <option value="container_app">Container Apps</option>
              <option value="aks">AKS</option>
            </select>
          </label>
          <button type="button" className="btn-secondary" disabled={!processId} onClick={() => setAuditOpen(true)}>
            <History size={16} aria-hidden /> Audit trail
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!processId || running || g3?.state !== "approved"}
            title={g3?.state !== "approved" ? "G3 must be approved first" : undefined}
            onClick={start}
          >
            {running ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Hammer size={16} aria-hidden />}
            Generate and build
          </button>
        </div>
      </div>

      {!processId ? (
        <Empty title="No process open">Pick a process above, or open one from Discover.</Empty>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 rounded-card border border-line bg-white px-5 py-3 text-[13px]">
            <span className="font-semibold text-plum">{proc?.name}</span>
            <span data-testid="build-design-hash" title={design.data?.record.design_hash}>
              <span className="label mr-1.5">Design hash</span>
              <span className="font-mono">{design.data ? shortHash(design.data.record.design_hash, 16) : "no design record"}</span>
            </span>
            <span data-testid="build-g3-state" className="flex items-center gap-1.5">
              <span className="label">G3</span>
              <Pill tone={g3State === "approved" ? "gate" : g3State === "rejected" ? "error" : "decision"}>
                {g3State.replace("_", " ")}
              </Pill>
            </span>
            {build.data ? (
              <>
                <span className="flex items-center gap-1.5" data-testid="build-status">
                  <span className="label">Build</span>
                  <span className="font-mono">{shortHash(build.data.id, 8)}</span>
                  <OutcomePill outcome={buildStatus ?? "pending"} />
                </span>
                <span title={build.data.build_hash ?? undefined}>
                  <span className="label mr-1.5">Build hash</span>
                  <span className="font-mono">{shortHash(build.data.build_hash, 16)}</span>
                </span>
                <span>{build.data.runtime === "aks" ? "AKS" : "Container Apps"}</span>
                {build.data.finished_at ? (
                  <span className="text-slate">finished {new Date(build.data.finished_at).toLocaleString()}</span>
                ) : null}
              </>
            ) : null}
          </div>
          {g3?.state !== "approved" && !build.data ? (
            <p className="text-[13.5px] text-slate">Approve G3 in Design before the first build.</p>
          ) : null}
          {runError ? <ErrorNote error={runError} /> : null}
          {build.data?.error ? <ErrorNote error={build.data.error} /> : null}

          <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div className="space-y-3">
              <GateButton
                processId={processId}
                gate={g4}
                name="G4"
                started={finished}
                hint="The engineer approves the build; the approval binds to its build hash."
              />
              {finished && g4?.state !== "approved" && !g4?.enabled ? (
                <p
                  role="status"
                  data-testid="g4-blocked"
                  className="rounded-card border border-error/30 bg-error-bg px-4 py-3 text-[14px] text-error"
                >
                  <span className="font-semibold">
                    {restoreFailed ? "G4 blocked: restore failed" : `G4 blocked: ${g4?.reason ?? "the build did not pass"}`}
                  </span>
                  {restoreFailed && restoreReason ? <span className="mt-1 block text-[13px]">{restoreReason}</span> : null}
                </p>
              ) : null}
              {buildId ? <BuildSteps rows={rows} /> : null}
            </div>
            <BuildJobLog stream={stream && (!jobId || stream.jobId === jobId) ? stream : null} />
          </div>

          {build.data && buildId ? (
            finished ? (
              <>
                {proc ? <SourceTargetExplorer buildId={buildId} process={proc} /> : null}
                <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
                  <MetricsRadar metrics={build.data.metrics} />
                  <ReviewPanel buildId={buildId} builder={build.data.builder} />
                </div>
                <TestResults buildId={buildId} compiled={build.data.metrics?.compile_ok ?? null} />
                <InfraPanel buildId={buildId} />
              </>
            ) : (
              <Empty title="Build in progress">The files, tests, metrics and infrastructure appear when the job finishes.</Empty>
            )
          ) : builds.isLoading ? null : (
            <Empty title="No build yet">
              Generate and build once G3 is approved for {proc?.name ?? "this process"}.
            </Empty>
          )}
        </>
      )}
      {processId ? <AuditDrawer processId={processId} open={auditOpen} onClose={() => setAuditOpen(false)} /> : null}
    </div>
  );
}
