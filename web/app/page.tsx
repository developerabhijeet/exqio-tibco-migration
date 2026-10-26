"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
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
  const sentinel = useRef<HTMLDivElement | null>(null);
  const [compact, setCompact] = useState(false);
  const [reserve, setReserve] = useState(0);
  const expanded = useRef(0);
  useLayoutEffect(() => {
    const node = pinned.current;
    if (!node) return;
    const apply = () => {
      const h = node.offsetHeight;
      document.documentElement.style.setProperty("--pinned-h", `${h}px`);
      // The header is taller expanded than compact. The difference is kept as bottom margin while
      // compact, so the content below never moves when the header shrinks (no layout shift, no
      // scroll feedback loop).
      if (node.dataset.compact !== "true") {
        expanded.current = h;
        setReserve(0);
      } else {
        setReserve(Math.max(0, expanded.current - h));
      }
    };
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(node);
    return () => observer.disconnect();
  }, [compact]);

  // Compact once the top 48 px of the page have scrolled away. An IntersectionObserver on a
  // sentinel avoids a scroll listener.
  useEffect(() => {
    const node = sentinel.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => setCompact(!entry.isIntersecting), { threshold: 0 });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="relative space-y-8">
      <div ref={sentinel} aria-hidden className="pointer-events-none absolute left-0 top-0 h-12 w-px" />
      {/* Pinned to the top of the scrolling content area: which project this is and where the
          migration stands. The negative margins let the background cover the page gutter, so the
          cards passing underneath are not visible beside it. */}
      <div
        ref={pinned}
        data-testid="pinned-header"
        data-compact={compact ? "true" : undefined}
        style={compact && reserve ? { marginBottom: reserve } : undefined}
        className={`sticky top-0 z-20 -mx-[var(--gutter)] -mt-8 border-b border-line/60 bg-page px-[var(--gutter)] print:static print:m-0 print:border-0 print:p-0 ${
          compact ? "space-y-2 py-2" : "space-y-4 py-5"
        }`}
      >
        <div className="flex items-end justify-between gap-4 print:hidden">
          <div>
            {compact ? null : <p className="eyebrow">Project</p>}
            <h1 className={compact ? "truncate text-lg font-semibold text-heading" : "screen-title"}>
              {project?.name ?? "No project selected"}
            </h1>
          </div>
          {report.data && !compact ? (
            <p className="text-md text-slate">
              Last scan {new Date(report.data.created_at).toLocaleString()} · {report.data.files_parsed} of{" "}
              {report.data.files_found} files parsed
            </p>
          ) : null}
        </div>
        <div className="print:hidden">
          <Stepper steps={steps} active={active} onSelect={(i) => setStep(STEP_KEYS[i])} compact={compact} />
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
