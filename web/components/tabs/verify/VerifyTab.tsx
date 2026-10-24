"use client";

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useBuild, useLatestBuildId } from "@/lib/buildQueries";
import { useJob } from "@/lib/jobs";
import { useGates, useProcesses } from "@/lib/queries";
import { useAppState } from "@/lib/state";
import { Empty } from "@/components/ui/Empty";
import { GateButton } from "@/components/gates/GateButton";
import { ProcessPicker } from "@/components/tabs/ProcessPicker";
import { FailedBuildStrip } from "@/components/tabs/build/BuildStatusStrip";
import { OutcomePill, shortHash, useScrollToHash } from "@/components/tabs/build/Panel";
import { ParityTable } from "./ParityTable";
import { ScenarioRunner } from "./ScenarioRunner";
import { DevTokenHelper, ServiceControl, SwaggerFrame, useService } from "./SwaggerFrame";
import { TestDataPanel } from "./TestDataPanel";

export function VerifyTab() {
  const qc = useQueryClient();
  const { projectId, processId } = useAppState();
  useScrollToHash();
  const processes = useProcesses(projectId);
  const gates = useGates(processId);
  const { buildId, loading } = useLatestBuildId(processId);
  const build = useBuild(buildId);
  const svc = useService(buildId);
  const { stream, watch } = useJob("verify", processId);
  const proc = processes.data?.find((p) => p.id === processId) ?? null;
  const g5 = (gates.data ?? []).find((g) => g.gate === "G5");

  const status = stream?.status;
  useEffect(() => {
    if (status !== "done" && status !== "failed") return;
    for (const q of ["parity", "gates", "readiness", "scenario-list"]) qc.invalidateQueries({ queryKey: [q] });
  }, [status, qc]);

  const runVerify = async () => {
    if (!buildId) return;
    const { job_id } = await api.verify(buildId);
    watch(job_id);
  };

  return (
    <div className="space-y-5" data-testid="verify-tab">
      <div className="sticky top-[var(--pinned-h,0px)] z-10 -mx-6 bg-page px-6 py-3 print:static print:m-0 print:p-0 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Step 6</p>
          <h2 className="panel-title">Verify</h2>
          <p className="mt-1 max-w-[760px] text-[14px] text-slate">
            The scenarios run through the platform&apos;s model of the TIBCO process; expected values are derived from the TIBCO
            model, not from a TIBCO runtime. The same inputs then go to the running .NET service. Every field is compared; a
            difference is a defect unless a finding decided Fix explains it. QA approves parity (G5).
          </p>
        </div>
        <ProcessPicker />
      </div>
      {!processId ? (
        <Empty title="No process open">Pick a process above, or open one from Discover.</Empty>
      ) : (
        <>
          {!buildId && !loading ? (
            <Empty title="No build yet">Build {proc?.name ?? "this process"} first; Verify runs the service of the latest build.</Empty>
          ) : null}
          {build.data?.status === "failed" ? (
            <FailedBuildStrip
              build={build.data}
              processName={proc?.name}
              consequence="Verify cannot start the generated service until a build of this process passes."
            />
          ) : buildId ? (
            <p className="flex items-center gap-1.5 text-[13px] text-slate">
              Latest build <span className="font-mono">{shortHash(buildId, 8)}</span> of {proc?.name}
              {build.data ? <OutcomePill outcome={build.data.status} /> : null}
            </p>
          ) : null}
          <div>
            <GateButton
              processId={processId}
              gate={g5}
              name="G5"
              started={!!buildId}
              hint="QA approves parity when no difference is unexplained and no scenario errored."
            />
            {g5?.notes?.length ? (
              <ul data-testid="g5-notes" className="mt-1.5 space-y-0.5 text-[13px] text-decision">
                {g5.notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            ) : null}
          </div>
          {buildId ? (
            <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
              <ServiceControl svc={svc} blocked={build.data?.status === "failed" ? "build failed; restore the build first" : null} />
              <DevTokenHelper run={svc.run} running={svc.state === "running"} />
            </div>
          ) : null}
          {buildId ? <SwaggerFrame run={svc.run} state={svc.state} reason={svc.reason} /> : null}
          <ScenarioRunner processId={processId} buildId={buildId} running={svc.state === "running"} stream={stream} onRun={runVerify} />
          <ParityTable buildId={buildId} />
          <TestDataPanel processId={processId} />
        </>
      )}
    </div>
  );
}
