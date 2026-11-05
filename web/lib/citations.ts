"use client";

// Citation navigation, plus the cited-text model re-exported (lib/citations-model.ts) so the
// components import one module.
import { useCallback } from "react";
import { usePathname, useRouter } from "next/navigation";
import type { CiteTarget } from "./citations-model";
import { STEP_KEYS, useAppState, type StepKey } from "./state";

export * from "./citations-model";

/** How long a hash target waits for its panel to render before giving up. */
const REVEAL_TIMEOUT_MS = 10_000;

/** The latest reveal: a newer one (or a navigation away) stops the older poller. */
let revealToken = 0;

/**
 * Scroll to the element `hash` names ("#tests") once it has rendered on `path`, and focus it so
 * keyboard and screen reader users land there too (a panel that takes no focus gets tabIndex -1).
 * The element may not exist yet: the step it lives on mounts after the navigation. A later reveal
 * cancels this one, and so does leaving `path` once it has been reached.
 */
export function revealHash(hash: string, path = "/"): void {
  const id = hash.replace(/^#/, "");
  if (!id || typeof window === "undefined") return;
  const token = ++revealToken;
  const started = Date.now();
  let arrived = false;
  const tick = () => {
    if (token !== revealToken) return;
    const here = window.location.pathname === path;
    if (arrived && !here) return;
    arrived ||= here;
    const el = here ? document.getElementById(id) : null;
    if (!el) {
      if (Date.now() - started < REVEAL_TIMEOUT_MS) window.setTimeout(tick, 100);
      return;
    }
    el.scrollIntoView({ block: "start" });
    if (el.tabIndex < 0 && !el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
    el.focus({ preventScroll: true });
  };
  window.setTimeout(tick, 0);
}

/**
 * Go where a citation points. activity: the step on the Discover canvas with its XML; step: that
 * phase of the process, scrolled to its `hash` panel when it names one; file: the generated file
 * in Build's source and target explorer, at `line` when it has one; memory: the node in the memory
 * explorer; page: a screen that is no phase (/settings, /history), at its `hash`, except /exiqoian, which opens the ExiQoian panel. Off the main
 * screen (on /memory, say) the main screen is opened on the step afterwards.
 */
export function useCiteNavigate(): (target: CiteTarget) => void {
  const { jumpToStep, openProcess, setStep, openBuildFile, setChatOpen } = useAppState();
  const router = useRouter();
  const pathname = usePathname();
  return useCallback(
    (target: CiteTarget) => {
      if (target.kind === "memory" || (target.kind === "step" && target.step === "memory")) {
        router.push(target.uid ? `/memory?uid=${encodeURIComponent(target.uid)}` : "/memory");
        return;
      }
      const hash = target.hash ? `#${target.hash.replace(/^#/, "")}` : "";
      if (target.kind === "page" && target.path === "/exiqoian") {
        setChatOpen(true); // the assistant is a panel, not a screen
        return;
      }
      if (target.kind === "page") {
        const path = target.path || "/";
        router.push(`${path}${hash}`);
        if (hash) revealHash(hash, path.split(/[?#]/)[0] || "/");
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
        router.push(`/?${q.toString()}${hash}`);
      }
      if (hash) revealHash(hash);
    },
    [jumpToStep, openProcess, setStep, openBuildFile, setChatOpen, router, pathname],
  );
}
