"use client";

// Screen state shared by the top bar, the stepper and the tabs: the sign-in mode and user (the
// persona sent as X-User in dev, the Entra user in oidc), the project, the active step, the open
// process and domain, and the scan job's streamed log.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, setApiUser } from "./api";
import { probeAuth, type AuthState } from "./auth";
import { subscribeJob } from "./sse";
import type { InspectorTab, JobEvent, UserName } from "./types";
import { USERS } from "./types";

export const STEP_KEYS = ["upload", "discover", "validate", "design", "build", "verify", "deploy"] as const;
export type StepKey = (typeof STEP_KEYS)[number];

export interface ScanState {
  jobId: string;
  projectId: string;
  status: "running" | "done" | "failed" | "lost";
  stage: string | null;
  events: JobEvent[];
  error: string | null;
}

export interface BuildFileRequest {
  path: string;
  line: number | null;
  /** The API process the file belongs to; null for whichever process Build shows. */
  processId: string | null;
  /** When it was asked for (Date.now()), so a late or repeated request is told apart. */
  at: number;
}

/** How long a file request waits for Build to open it before it is dropped. */
export const BUILD_FILE_TTL_MS = 10_000;

interface AppState {
  /** dev or oidc, and in oidc the signed-in user; "unknown" until /auth/me has answered. */
  auth: AuthState;
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
  /**
   * A generated file asked for (by a citation, say), opened by Build's source and target explorer
   * of that process. Dropped when the step leaves Build or after BUILD_FILE_TTL_MS unanswered.
   */
  buildFile: BuildFileRequest | null;
  openBuildFile: (path: string | null, line?: number | null, processId?: string | null) => void;
  /** The ExiQoian slide-over (components/exiqoian), open across steps and screens. */
  chatOpen: boolean;
  setChatOpen: (open: boolean) => void;
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
  const [auth, setAuth] = useState<AuthState>({ mode: "unknown", me: null });
  const [user, setUserState] = useState<UserName>("sme.vicky");
  const [projectId, setProjectIdState] = useState<string | null>(null);
  const [step, setStepState] = useState<StepKey>("upload");
  const [processId, setProcessId] = useState<string | null>(null);
  const [nodeId, selectNode] = useState<string | null>(null);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("Config");
  const [returnTo, setReturnTo] = useState<StepKey | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [buildFile, setBuildFile] = useState<BuildFileRequest | null>(null);
  const openBuildFile = useCallback((path: string | null, line?: number | null, pid?: string | null) => {
    setBuildFile(path ? { path, line: line ?? null, processId: pid ?? null, at: Date.now() } : null);
  }, []);
  // A file request only stands while Build is open, and not forever even then.
  useEffect(() => {
    if (!buildFile) return;
    if (step !== "build") {
      setBuildFile(null);
      return;
    }
    const t = setTimeout(() => setBuildFile((b) => (b === buildFile ? null : b)), BUILD_FILE_TTL_MS);
    return () => clearTimeout(t);
  }, [buildFile, step]);
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
    let live = true;
    probeAuth().then((a) => {
      if (!live) return;
      setAuth(a);
      // oidc: what was fetched before the mode was known went out with X-User; ask again
      if (a.mode === "oidc") qc.invalidateQueries();
    });
    return () => {
      live = false;
    };
  }, [qc]);

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
      store(`exiqo.scan.${pid}`, jobId); // re-attached after a reload while it runs
      unsubscribe.current = subscribeJob(
        jobId,
        (ev) => {
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
            store(`exiqo.scan.${pid}`, null);
            for (const key of ["report", "processes", "files", "domains", "graph"]) {
              qc.invalidateQueries({ queryKey: [key, pid] });
            }
            // A rescan can change every process view.
            for (const key of ["canvas", "io-card", "analysis", "xml", "gates", "scenarios"]) {
              qc.invalidateQueries({ queryKey: [key] });
            }
          }
        },
        {
          onLost: () =>
            setScan((s) =>
              s && s.jobId === jobId ? { ...s, status: "lost", error: "connection to the API lost" } : s,
            ),
        },
      );
    },
    [qc],
  );

  useEffect(() => () => unsubscribe.current?.(), []);

  // After a reload, re-attach to a scan of this project that is still queued or running.
  useEffect(() => {
    if (!projectId) return;
    const jobId = stored(`exiqo.scan.${projectId}`);
    if (!jobId) return;
    let live = true;
    api
      .job(jobId)
      .then((job) => {
        if (!live) return;
        if (job.status === "queued" || job.status === "running") watchScan(projectId, jobId);
        else store(`exiqo.scan.${projectId}`, null);
      })
      .catch(() => store(`exiqo.scan.${projectId}`, null));
    return () => {
      live = false;
    };
  }, [projectId, watchScan]);

  const value = useMemo<AppState>(
    () => ({
      auth,
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
      buildFile,
      openBuildFile,
      chatOpen,
      setChatOpen,
    }),
    [
      auth,
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
      buildFile,
      openBuildFile,
      chatOpen,
    ],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppState(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAppState outside AppStateProvider");
  return v;
}
