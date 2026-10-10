"use client";

import { useEffect, useRef } from "react";
import { Stepper } from "@/components/stepper/Stepper";
import { UploadTab } from "@/components/tabs/upload/UploadTab";
import { DiscoverTab } from "@/components/tabs/discover/DiscoverTab";
import { LaterStep } from "@/components/tabs/LaterStep";
import { Empty } from "@/components/ui/Empty";
import { useProjects, useReport } from "@/lib/queries";
import { STEP_KEYS, useAppState } from "@/lib/state";
import { useStepStates } from "@/lib/steps";

export default function Home() {
  const { projectId, step, setStep } = useAppState();
  const projects = useProjects();
  const report = useReport(projectId);
  const steps = useStepStates();
  const opened = useRef<string | null>(null);

  // A project that has been scanned opens on Discover; a new one on Upload.
  useEffect(() => {
    if (!projectId || report.isLoading || opened.current === projectId) return;
    opened.current = projectId;
    setStep(report.data ? "discover" : "upload");
  }, [projectId, report.isLoading, report.data, setStep]);

  const project = projects.data?.find((p) => p.id === projectId);
  const active = STEP_KEYS.indexOf(step);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Project</p>
          <h1 className="screen-title">{project?.name ?? "No project selected"}</h1>
        </div>
        {report.data ? (
          <p className="text-[14px] text-slate">
            Last scan {new Date(report.data.created_at).toLocaleString()} · {report.data.files_parsed} of{" "}
            {report.data.files_found} files parsed
          </p>
        ) : null}
      </div>
      <Stepper steps={steps} active={active} onSelect={(i) => setStep(STEP_KEYS[i])} />
      {!projectId ? (
        <Empty title="No project yet">Create a project with New project in the top bar, then upload an estate.</Empty>
      ) : step === "upload" ? (
        <UploadTab />
      ) : step === "discover" ? (
        <DiscoverTab />
      ) : (
        <LaterStep step={steps[active]} />
      )}
    </div>
  );
}
