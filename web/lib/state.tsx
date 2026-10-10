"use client";

// Screen state shared by the top bar, the stepper and the tabs: the user (sent as X-User), the
// project, the active step, the open process and domain, and the scan job's streamed log.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { setApiUser } from "./api";
import { subscribeJob } from "./sse";
import type { InspectorTab, JobEvent, UserName } from "./types";
import { USERS } from "./types";

export const STEP_KEYS = ["upload", "discover", "validate", "design", "build", "verify", "deploy"] as const;
export type StepKey = (typeof STEP_KEYS)[number];

export interface ScanState {
  jobId: string;
  projectId: string;
  status: "running" | "done" | "failed";
  stage: string | null;
  events: JobEvent[];
  error: string | null;
}

interface AppState {
  user: UserName;
  setUser: (u: UserName) => void;
  projectId: string | null;
  setProjectId: (id: string | null) => void;
  step: StepKey;
  setStep: (s: StepKey) => void;
  processId: string | null;
  domainId: string | null;
  openProcess: (processId: string | null, domainId?: string | null) => void;
  setDomainId: (id: string | null) => void;
  scan: ScanState | null;
  watchScan: (projectId: string, jobId: string) => void;
  /** The canvas step selected in Discover, shared so Validate and Ask can jump to it. */
  nodeId: string | null;
  selectNode: (id: string | null) => void;
  inspectorTab: InspectorTab;
  setInspectorTab: (t: InspectorTab) => void;
  /** Open a step on the canvas with its XML lines, remembering the step to come back to. */
  jumpToStep: (activityId: string, processId?: string | null) => void;
  returnTo: StepKey | null;
  askOpen: boolean;
  setAskOpen: (open: boolean) => void;
}

const Ctx = createContext<AppState | null>(null);

function stored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
}

export function AppStateProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [user, setUserState] = useState<UserName>("sme.vicky");
  const [projectId, setProjectIdState] = useState<string | null>(null);
  const [step, setStepState] = useState<StepKey>("upload");
  const [processId, setProcessId] = useState<string | null>(null);
  const [nodeId, selectNode] = useState<string | null>(null);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("Config");
  const [returnTo, setReturnTo] = useState<StepKey | null>(null);
  const [askOpen, setAskOpen] = useState(false);
  const processRef = useRef<string | null>(null);
  useEffect(() => {
    processRef.current = processId;
  }, [processId]);
  const [domainId, setDomainId] = useState<string | null>(null);
  const [scan, setScan] = useState<ScanState | null>(null);
  const unsubscribe = useRef<(() => void) | null>(null);

  useEffect(() => {
    const u = stored("exiqo.user");
    if (u && (USERS as readonly string[]).includes(u)) {
      setUserState(u as UserName);
      setApiUser(u);
    }
    setProjectIdState(stored("exiqo.project"));
  }, []);

  const setUser = useCallback(
    (u: UserName) => {
      setApiUser(u);
      setUserState(u);
      store("exiqo.user", u);
      qc.invalidateQueries();
    },
    [qc],
  );

  const setStep = useCallback((s: StepKey) => {
    setReturnTo(null);
    setStepState(s);
  }, []);

  const setProjectId = useCallback((id: string | null) => {
    setProjectIdState(id);
    store("exiqo.project", id);
    setProcessId(null);
    setDomainId(null);
    selectNode(null);
  }, []);

  const openProcess = useCallback((pid: string | null, did?: string | null) => {
    if (pid !== processRef.current) selectNode(null);
    setProcessId(pid);
    if (did !== undefined) setDomainId(did);
  }, []);

  const jumpToStep = useCallback(
    (activityId: string, pid?: string | null) => {
      if (pid) setProcessId(pid);
      selectNode(activityId);
      setInspectorTab("XML");
      setReturnTo(step === "discover" ? null : step);
      setStepState("discover");
    },
    [step],
  );

  const watchScan = useCallback(
    (pid: string, jobId: string) => {
      unsubscribe.current?.();
      setScan({ jobId, projectId: pid, status: "running", stage: null, events: [], error: null });
      unsubscribe.current = subscribeJob(jobId, (ev) => {
        setScan((s) => {
          if (!s || s.jobId !== jobId) return s;
          if (ev.seq > 0 && s.events.some((e) => e.seq === ev.seq)) return s;
          const next: ScanState = { ...s, events: [...s.events, ev] };
          if (ev.event === "stage") next.stage = ev.text;
          if (ev.event === "done") next.status = "done";
          if (ev.event === "error") {
            next.status = "failed";
            next.error = ev.text;
          }
          return next;
        });
        if (ev.event === "done" || ev.event === "error") {
          for (const key of ["report", "processes", "files", "domains", "graph"]) {
            qc.invalidateQueries({ queryKey: [key, pid] });
          }
        }
      });
    },
    [qc],
  );

  useEffect(() => () => unsubscribe.current?.(), []);

  const value = useMemo<AppState>(
    () => ({
      user,
      setUser,
      projectId,
      setProjectId,
      step,
      setStep,
      processId,
      domainId,
      openProcess,
      setDomainId,
      scan,
      watchScan,
      nodeId,
      selectNode,
      inspectorTab,
      setInspectorTab,
      jumpToStep,
      returnTo,
      askOpen,
      setAskOpen,
    }),
    [
      user,
      setUser,
      projectId,
      setProjectId,
      step,
      setStep,
      processId,
      domainId,
      openProcess,
      scan,
      watchScan,
      nodeId,
      inspectorTab,
      jumpToStep,
      returnTo,
      askOpen,
    ],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppState(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAppState outside AppStateProvider");
  return v;
}
