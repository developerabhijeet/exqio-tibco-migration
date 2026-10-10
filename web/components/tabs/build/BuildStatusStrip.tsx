"use client";

import { AlertTriangle, Hammer } from "lucide-react";
import type { BuildView } from "@/lib/buildTypes";
import { useAppState } from "@/lib/state";
import { OutcomePill, shortHash } from "./Panel";

const REMEDY = "Run `make nuget-cache` on a machine with NuGet access, copy .tools/nuget-cache across, then build again.";

/**
 * Why restore failed, from the restore step's log: the API's own verdict line ("NuGet unreachable",
 * "restore failed ...", "dotnet not found ...") else the last line that is not the probe. The
 * `make nuget-cache` remedy is split out when the log names it, or added when NuGet is the cause.
 */
export function restoreFailure(build: BuildView | null | undefined): { reason: string; remedy: string | null } | null {
  const step = build?.steps.find((s) => s.name === "restore");
  if (!step || step.ok) return null;
  const lines = step.log
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  const verdict =
    lines.find((l) => /^(nuget unreachable|restore failed|dotnet not found)/i.test(l)) ??
    [...lines].reverse().find((l) => !l.startsWith("probe:")) ??
    "restore failed";
  const at = verdict.search(/\s*Run `make nuget-cache`/);
  if (at >= 0) return { reason: verdict.slice(0, at).trim(), remedy: verdict.slice(at).trim() };
  return { reason: verdict, remedy: /nuget/i.test(verdict) ? REMEDY : null };
}

/**
 * The latest build failed: its id and outcome, why restore failed, the remedy, and what this tab
 * cannot do until a build passes. Verify and Deploy show it at the top.
 */
export function FailedBuildStrip({
  build,
  processName,
  consequence,
}: {
  build: BuildView;
  processName?: string | null;
  consequence: string;
}) {
  const { setStep } = useAppState();
  const restore = restoreFailure(build);
  const failedStep = build.steps.find((s) => !s.ok && !s.skipped);
  return (
    <section
      role="status"
      data-testid="failed-build-strip"
      className="rounded-card border border-error/30 bg-error-bg px-5 py-3 text-[13.5px] text-error"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        <span className="flex items-center gap-1.5 font-semibold">
          <AlertTriangle size={16} aria-hidden /> Latest build failed
        </span>
        <span className="flex items-center gap-1.5 text-ink">
          <span className="label">Build</span>
          <span className="font-mono">{shortHash(build.id, 8)}</span>
          {processName ? <span>of {processName}</span> : null}
          <OutcomePill outcome={build.status} />
        </span>
        <button type="button" className="btn-secondary ml-auto px-3 py-1 text-[13px]" onClick={() => setStep("build")}>
          <Hammer size={14} aria-hidden /> Open Build
        </button>
      </div>
      <p className="mt-1.5 font-medium" data-testid="failed-build-consequence">
        {consequence}
      </p>
      {restore ? (
        <p className="mt-1 text-[13px]" data-testid="failed-build-reason">
          Restore failed: <Remedy text={restore.reason} />
        </p>
      ) : failedStep ? (
        <p className="mt-1 text-[13px]" data-testid="failed-build-reason">
          {failedStep.name} failed: {failedStep.log.split("\n").find((l) => l.trim()) ?? "see the Build tab"}
        </p>
      ) : build.error ? (
        <p className="mt-1 text-[13px]" data-testid="failed-build-reason">
          {build.error}
        </p>
      ) : null}
      {restore?.remedy ? (
        <p className="mt-1 text-[13px] text-ink" data-testid="failed-build-remedy">
          <span className="label mr-1.5">Remedy</span>
          <Remedy text={restore.remedy} />
        </p>
      ) : null}
    </section>
  );
}

/** Text with its `commands` set in code type (the restore reason and its remedy). */
export function Remedy({ text }: { text: string }) {
  return (
    <>
      {text.split(/(`[^`]+`)/).map((part, i) =>
        part.startsWith("`") ? (
          <code key={i} className="rounded bg-white px-1 font-mono text-[12px]">
            {part.slice(1, -1)}
          </code>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}
