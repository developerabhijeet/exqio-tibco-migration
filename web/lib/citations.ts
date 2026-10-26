"use client";

// Citation navigation, plus the cited-text model re-exported (lib/citations-model.ts) so the
// components import one module.
import { useCallback } from "react";
import { usePathname, useRouter } from "next/navigation";
import type { CiteTarget } from "./citations-model";
import { STEP_KEYS, useAppState, type StepKey } from "./state";

export * from "./citations-model";

/**
 * Go where a citation points. activity: the step on the Discover canvas with its XML; step: that
 * phase of the process; file: the generated file in Build's source and target explorer; memory:
 * the node in the memory explorer. Off the main screen (on /memory, say) the main screen is opened
 * on the step afterwards.
 */
export function useCiteNavigate(): (target: CiteTarget) => void {
  const { jumpToStep, openProcess, setStep, openBuildFile } = useAppState();
  const router = useRouter();
  const pathname = usePathname();
  return useCallback(
    (target: CiteTarget) => {
      if (target.kind === "memory" || (target.kind === "step" && target.step === "memory")) {
        router.push(target.uid ? `/memory?uid=${encodeURIComponent(target.uid)}` : "/memory");
        return;
      }
      let step: StepKey;
      if (target.kind === "activity" && target.activity_id) {
        jumpToStep(target.activity_id, target.process_id);
        step = "discover";
      } else if (target.kind === "file" && target.path) {
        if (target.process_id) openProcess(target.process_id);
        openBuildFile(target.path, target.line ?? null, target.process_id ?? null);
        step = "build";
        setStep(step);
      } else {
        step = target.step && (STEP_KEYS as readonly string[]).includes(target.step) ? (target.step as StepKey) : "discover";
        if (target.process_id) openProcess(target.process_id);
        setStep(step);
      }
      if (pathname !== "/") {
        const q = new URLSearchParams({ step });
        if (target.process_id) q.set("process", target.process_id);
        router.push(`/?${q.toString()}`);
      }
    },
    [jumpToStep, openProcess, setStep, openBuildFile, router, pathname],
  );
}
