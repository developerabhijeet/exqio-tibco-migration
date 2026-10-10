"use client";

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useLatestBuildId } from "@/lib/buildQueries";
import { useJob } from "@/lib/jobs";
import { useGates, useProcesses } from "@/lib/queries";
import { useAppState } from "@/lib/state";
import { Empty } from "@/components/ui/Empty";
import { GateButton } from "@/components/gates/GateButton";
import { ProcessPicker } from "@/components/tabs/ProcessPicker";
import { shortHash } from "@/components/tabs/build/Panel";
import { AzureConnectForm } from "./AzureConnectForm";
import { PlanView } from "./PlanView";

export function DeployTab() {
  const qc = useQueryClient();
  const { projectId, processId, user } = useAppState();
  const processes = useProcesses(projectId);
  const gates = useGates(processId);
  const { buildId, loading } = useLatestBuildId(processId);
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
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Step 7</p>
          <h2 className="panel-title">Deploy</h2>
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
          {buildId ? (
            <p className="text-[13px] text-slate">
              Latest build <span className="font-mono">{shortHash(buildId, 8)}</span> of {proc?.name}.
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
