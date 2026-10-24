"use client";

import { useEffect, useRef } from "react";
import { Stepper } from "@/components/stepper/Stepper";
import { NextStep } from "@/components/stepper/NextStep";
import { UploadTab } from "@/components/tabs/upload/UploadTab";
import { DiscoverTab } from "@/components/tabs/discover/DiscoverTab";
import { LaterStep } from "@/components/tabs/LaterStep";
import { ValidateTab } from "@/components/tabs/validate/ValidateTab";
import { DesignTab } from "@/components/tabs/design/DesignTab";
import { BuildTab } from "@/components/tabs/build/BuildTab";
import { VerifyTab } from "@/components/tabs/verify/VerifyTab";
import { DeployTab } from "@/components/tabs/deploy/DeployTab";
import { AskPanel } from "@/components/tabs/validate/AskPanel";
import { Empty } from "@/components/ui/Empty";
import { RoleNotice } from "@/components/shell/RoleNotice";
import { useProjects, useReport } from "@/lib/queries";
import { STEP_KEYS, useAppState, type StepKey } from "@/lib/state";
import { useStepStates } from "@/lib/steps";

export default function Home() {
  const { projectId, step, setStep, openProcess } = useAppState();
  const projects = useProjects();
  const report = useReport(projectId);
  const steps = useStepStates();
  const opened = useRef<string | null>(null);

  // A link from Readiness (/?step=build&process=<id>#metrics) opens that step and process.
  useEffect(() => {
    if (!projectId) return;
    const q = new URLSearchParams(window.location.search);
    const s = q.get("step");
    if (!s || !(STEP_KEYS as readonly string[]).includes(s)) return;
    opened.current = projectId;
    const pid = q.get("process");
    if (pid) openProcess(pid);
    setStep(s as StepKey);
    window.history.replaceState(null, "", `/${window.location.hash}`);
  }, [projectId, openProcess, setStep]);

  // A project that has been scanned opens on Discover; a new one on Upload.
  useEffect(() => {
    if (!projectId || report.isLoading || opened.current === projectId) return;
    opened.current = projectId;
    setStep(report.data ? "discover" : "upload");
  }, [projectId, report.isLoading, report.data, setStep]);

  const project = projects.data?.find((p) => p.id === projectId);
  const active = STEP_KEYS.indexOf(step);

  // Each step's own heading pins itself directly under this block, so it needs the block's height.
  // It is measured rather than assumed because the stepper wraps on a narrow window and the title
  // can run to two lines, and a wrong number would leave a gap or hide the heading behind it.
  const pinned = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const node = pinned.current;
    if (!node) return;
    const apply = () => document.documentElement.style.setProperty("--pinned-h", `${node.offsetHeight}px`);
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="space-y-6">
      {/* Pinned to the top of the scrolling content area: which project this is and where the
          migration stands. The negative margins let the background cover the page gutter, so the
          cards passing underneath are not visible beside it. */}
      <div
        ref={pinned}
        className="sticky top-0 z-20 -mx-6 -mt-6 space-y-4 border-b border-line/60 bg-page px-6 pb-4 pt-6 print:static print:m-0 print:border-0 print:p-0"
      >
        <div className="flex items-end justify-between gap-4 print:hidden">
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
        <div className="print:hidden">
          <Stepper steps={steps} active={active} onSelect={(i) => setStep(STEP_KEYS[i])} />
        </div>
      </div>
      {/* A step that is not this persona's is open to read; the banner says so, above the controls
          the persona model disables. It renders nothing on a step they own. */}
      {projectId ? (
        <div className="print:hidden">
          <RoleNotice step={step} />
        </div>
      ) : null}
      {!projectId ? (
        <Empty title="No project yet">Create a project with New project in the top bar, then upload an estate.</Empty>
      ) : step === "upload" ? (
        <UploadTab />
      ) : step === "discover" ? (
        <DiscoverTab />
      ) : step === "validate" ? (
        <ValidateTab />
      ) : step === "design" ? (
        <DesignTab />
      ) : step === "build" ? (
        <BuildTab />
      ) : step === "verify" ? (
        <VerifyTab />
      ) : step === "deploy" ? (
        <DeployTab />
      ) : (
        <LaterStep step={steps[active]} />
      )}
      {projectId ? (
        <div className="print:hidden">
          <NextStep steps={steps} active={active} onGo={(i) => setStep(STEP_KEYS[i])} />
        </div>
      ) : null}
      <AskPanel key={projectId ?? "none"} />
    </div>
  );
}
