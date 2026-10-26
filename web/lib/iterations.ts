"use client";

// The iteration loop (sub-project 6) on the screen: the project's iterations, re-entering Design
// with what failed, an edited copy of a build uploaded back, and GitHub repositories as a source.
// Every read is a GET; the writes are thin calls the components make and then refresh from.
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { canActOn, ITERATION_ACTION_ROLES, useActingPersona, type IterationAction } from "./personas";
import { DEV_ROLES } from "./publish";
import { useAppState } from "./state";
import type { IterationOpenBody, IterationView } from "./types";

export const MAX_REQUIREMENTS = 20_000;
export const MAX_LOGS = 10;
export const MAX_LOG_BYTES = 5 * 1024 * 1024;

/** The iterations still under way (the API's ACTIVE_STATUSES). */
const ACTIVE = new Set(["open", "designing", "building", "verifying", "deploying"]);

export const isActive = (it: IterationView) => ACTIVE.has(it.status);

export const useIterations = (projectId: string | null) =>
  useQuery({
    queryKey: ["iterations", projectId],
    queryFn: () => api.iterations(projectId!),
    enabled: !!projectId,
    // An iteration's status follows its jobs (designing, building, ...): re-read while one is under way.
    refetchInterval: (q) => (q.state.data?.some((i) => i.n > 1 && isActive(i)) ? 15_000 : false),
  });

export const useIteration = (id: string | null) =>
  useQuery({ queryKey: ["iteration", id], queryFn: () => api.iteration(id!), enabled: !!id });

/** What would go into a re-entry now: the failures of the process's latest build, verify and plan. */
export const usePreview = (processId: string | null) =>
  useQuery({
    queryKey: ["iteration-preview", processId],
    queryFn: () => api.iterationPreview(processId!),
    enabled: !!processId,
    retry: false,
  });

export const useEditSet = (id: string | null) =>
  useQuery({ queryKey: ["edit-set", id], queryFn: () => api.editSet(id!), enabled: !!id });

export const useEditDiff = (id: string | null, path: string | null) =>
  useQuery({
    queryKey: ["edit-diff", id, path],
    queryFn: () => api.editDiff(id!, path!),
    enabled: !!id && !!path,
    staleTime: Infinity,
    retry: false,
  });

/** The project's connected repositories; null when this API has no repository routes. */
export const useRepos = (projectId: string | null) =>
  useQuery({ queryKey: ["repos", projectId], queryFn: () => api.repos(projectId!), enabled: !!projectId, retry: false });

/** The newest iteration (n > 1) that names this process, if any. */
export function iterationOf(list: IterationView[] | undefined, processId: string | null): IterationView | null {
  if (!list || !processId) return null;
  return [...list].filter((i) => i.n > 1 && i.process_ids.includes(processId)).sort((a, b) => b.n - a.n)[0] ?? null;
}

export const openIteration = (processId: string, body: IterationOpenBody, logs: File[] = []) =>
  api.openIteration(processId, body, logs);
export const uploadEdits = (buildId: string, file: File) => api.uploadEdits(buildId, file, file.name);
export const resumeEditSet = (id: string) => api.resumeEditSet(id);
export const connectRepo = (
  projectId: string,
  body: { repo: string; branch: string; purpose: "estate" | "build"; process_id?: string },
) => api.connectRepo(projectId, body);
export const pullRepo = (id: string, branch?: string) => api.pullRepo(id, branch);
export const pushRepo = (id: string, buildId: string, openPr = true) => api.pushRepo(id, buildId, openPr);

const ROLE_NAMES: Record<string, string> = {
  architect: "an architect",
  engineer: "an engineer",
  sme: "a TIBCO SME",
  admin: "an administrator",
};

function list(items: string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`;
}

export interface IterationAccess {
  allowed: boolean;
  /** The role keys the current user holds (dev: the persona's, as the API reads X-User). */
  roles: readonly string[];
  /** Why the action is disabled, or undefined when it is not. */
  reason: string | undefined;
}

/** Whether the current user may take an iteration action, with the reason when not. */
export function useIterationAccess(action: IterationAction, accepted?: readonly string[]): IterationAccess {
  const { auth, user } = useAppState();
  const persona = useActingPersona();
  const roles = auth.mode === "oidc" ? (auth.me?.roles ?? []) : (DEV_ROLES[user] ?? []);
  const ok = accepted ? roles.some((r) => accepted.includes(r)) : canActOn(persona, action, roles);
  if (ok) return { allowed: true, roles, reason: undefined };
  const needed: readonly string[] = accepted ?? ITERATION_ACTION_ROLES[action];
  const who = list(needed.map((r) => ROLE_NAMES[r] ?? r));
  if (roles.length === 0 || roles.every((r) => r === "viewer")) {
    return { allowed: false, roles, reason: `Viewers are read only: this needs ${who}.` };
  }
  return { allowed: false, roles, reason: `This needs ${who}; you are acting as ${persona.role}.` };
}

/** The roles that may open an iteration with failures and logs, not requirements only. */
export const FULL_OPENERS = ["architect", "admin"] as const;
