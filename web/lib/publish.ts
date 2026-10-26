"use client";

// Publish actions in the phase tabs (push a build to GitHub, publish to Confluence, file Jira
// issues, attach Figma frames): who may run each one, and what has been published already.
//
// The roles are the API's (`require_roles` on each route, api/app/routers/connectors.py), not the
// step-based screen permissions of lib/personas.ts: an action is shown, disabled with the reason,
// to anyone the API would refuse, and the API stays the authority.
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { useActingPersona } from "./personas";
import { useAppState } from "./state";
import type { ConnectorLinkView, JiraItem } from "./types";

export type PublishAction = "github" | "confluence" | "jira" | "figma";

/** The role keys each action's route accepts (admin always counts). */
export const ACTION_ROLES: Record<PublishAction, readonly string[]> = {
  github: ["engineer", "admin"],
  confluence: ["architect", "admin"],
  jira: ["architect", "sme", "qa", "admin"],
  figma: ["architect", "admin"],
};

/** AUTH_MODE=dev: the roles the API gives each persona (api/app/auth/roles.py DEV_PERSONAS). */
const DEV_ROLES: Record<string, readonly string[]> = {
  "sme.vicky": ["sme"],
  "architect.lee": ["architect"],
  "engineer.ravi": ["engineer"],
  "qa.priya": ["qa"],
  "ops.sam": ["ops", "admin"],
  "viewer.val": ["viewer"],
};

const ROLE_NAMES: Record<string, string> = {
  engineer: "an engineer",
  architect: "an architect",
  sme: "a TIBCO SME",
  qa: "QA",
  admin: "an administrator",
};

function list(items: string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`;
}

export interface ActionAccess {
  allowed: boolean;
  /** Why the action is disabled for this user, or undefined. */
  reason: string | undefined;
}

/** Whether the current user holds a role `action`'s route accepts, with the API's reason if not. */
export function useActionAccess(action: PublishAction): ActionAccess {
  const { auth, user } = useAppState();
  const persona = useActingPersona();
  const roles = auth.mode === "oidc" ? (auth.me?.roles ?? []) : (DEV_ROLES[user] ?? []);
  const accepted = ACTION_ROLES[action];
  if (roles.some((r) => accepted.includes(r))) return { allowed: true, reason: undefined };
  const who = list(accepted.map((r) => ROLE_NAMES[r] ?? r));
  if (roles.length > 0 && roles.every((r) => r === "viewer")) {
    return { allowed: false, reason: `Viewers are read only: this needs ${who}.` };
  }
  return { allowed: false, reason: `This needs ${who}; you are acting as ${persona.role}.` };
}

/** What this process has published (GET /processes/{id}/links); refreshed when a publish job ends. */
export function useLinks(processId: string | null) {
  return useQuery({
    queryKey: ["links", processId],
    queryFn: () => api.links(processId!),
    enabled: !!processId,
  });
}

/** The link's `local_ref` for a Jira item: `<type>:<ref>`. */
export function jiraRef(item: JiraItem): string {
  return `${item.type}:${item.ref}`;
}

/** The Jira issue links of a process, by the item they were filed for. */
export function jiraLinks(links: readonly ConnectorLinkView[] | undefined): Map<string, ConnectorLinkView> {
  return new Map((links ?? []).filter((l) => l.object_type === "jira_issue").map((l) => [l.local_ref, l]));
}

/** A job's failure text, or the request's refusal (`ApiError.detail`). */
export function actionError(e: unknown): string {
  if (e && typeof e === "object" && "detail" in e && typeof (e as { detail: unknown }).detail === "string") {
    return (e as { detail: string }).detail;
  }
  return e instanceof Error ? e.message : String(e);
}
