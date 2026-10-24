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
import { OutcomePill, shortHash } from "@/components/tabs/build/Panel";
import { AzureIcon } from "@/components/ui/BrandIcons";
import { AzureConnectForm } from "./AzureConnectForm";
import { SecurityPanel } from "./SecurityPanel";
import { PlanView } from "./PlanView";

export function DeployTab() {
  const qc = useQueryClient();
  const { projectId, processId, user } = useAppState();
  const processes = useProcesses(projectId);
  const gates = useGates(processId);
  const { buildId, loading } = useLatestBuildId(processId);
  const build = useBuild(buildId);
  const { stream, watch } = useJob("plan", processId);
  const proc = processes.data?.find((p) => p.id === processId) ?? null;
  const g6 = (gates.data ?? []).find((g) => g.gate === "G6");

  const status = stream?.status;
  useEffect(() => {
    if (status !== "done" && status !== "failed") return;
    for (const q of ["plan", "gates", "readiness"]) qc.invalidateQueries({ queryKey: [q] });
  }, [status, qc]);

  const connectLater = async (note: string) => {
    if (!buildId) return;
    const plan = await api.acknowledgePlan(buildId, note);
    qc.setQueryData(["plan", buildId], plan);
    for (const q of ["gates", "audit", "readiness"]) qc.invalidateQueries({ queryKey: [q] });
  };

  return (
    <div className="space-y-5" data-testid="deploy-tab">
      <div className="sticky top-[var(--pinned-h,0px)] z-10 -mx-6 bg-page px-6 py-3 print:static print:m-0 print:p-0 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Step 7</p>
          <h2 className="panel-title flex items-center gap-2">
            <AzureIcon size={19} />
            Deploy
          </h2>
          <p className="mt-1 max-w-[760px] text-[14px] text-slate">
            The generated Terraform modules and Terragrunt live configuration are planned against the connected subscription.
            Without a connection, the connect-later state is recorded with the last validation. Operations approves (G6). Apply
            runs from the pipelines, not from here.
          </p>
        </div>
        <ProcessPicker />
      </div>
      {!processId || !projectId ? (
        <Empty title="No process open">Pick a process above, or open one from Discover.</Empty>
      ) : (
        <>
          {build.data?.status === "failed" ? (
            <FailedBuildStrip
              build={build.data}
              processName={proc?.name}
              consequence="Deploy cannot plan an image of the service until a build of this process passes."
            />
          ) : buildId ? (
            <p className="flex items-center gap-1.5 text-[13px] text-slate">
              Latest build <span className="font-mono">{shortHash(buildId, 8)}</span> of {proc?.name}
              {build.data ? <OutcomePill outcome={build.data.status} /> : null}
            </p>
          ) : !loading ? (
            <Empty title="No build yet">Build {proc?.name ?? "this process"} first; the plan uses the infrastructure of the latest build.</Empty>
          ) : null}
          <GateButton
            processId={processId}
            gate={g6}
            name="G6"
            started={!!buildId}
            hint="Operations approves the plan, or the acknowledged connect-later state."
          />
          {buildId ? <PlanView buildId={buildId} stream={stream} onPlanJob={watch} /> : null}
          <SecurityPanel buildId={buildId} />
          <AzureConnectForm
            projectId={projectId}
            onConnectLater={connectLater}
            canConnectLater={buildId ? null : "Connect later is recorded against a build; build first."}
            user={user}
          />
        </>
      )}
    </div>
  );
}
