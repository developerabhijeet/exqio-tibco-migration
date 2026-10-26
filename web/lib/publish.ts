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
import type { ScenarioReport } from "./buildTypes";
import type { ConnectorLinkView, DesignRecord, FindingView, JiraItem } from "./types";

export type PublishAction = "github" | "confluence" | "jira" | "figma";

/** The role keys each action's route accepts (admin always counts). */
export const ACTION_ROLES: Record<PublishAction, readonly string[]> = {
  github: ["engineer", "admin"],
  confluence: ["architect", "admin"],
  jira: ["architect", "sme", "qa", "admin"],
  figma: ["architect", "admin"],
};

/**
 * AUTH_MODE=dev: the roles the API gives each persona. A copy of DEV_PERSONAS in
 * api/app/auth/roles.py (the dev API reads X-User through it); e2e/publish.unit.ts parses that
 * file and fails when the two drift apart.
 */
export const DEV_ROLES: Record<string, readonly string[]> = {
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

/** A job's failure text, or the request's refusal as one readable line. `ApiError.detail` holds a
 * non-string detail as JSON (lib/api.ts), so a validation list is parsed back and `detailText`ed. */
export function actionError(e: unknown): string {
  if (e && typeof e === "object" && "detail" in e) {
    const detail = (e as { detail: unknown }).detail;
    if (typeof detail === "string") return detailText(tryJson(detail));
    return detailText(detail);
  }
  return e instanceof Error ? e.message : String(e);
}

function tryJson(text: string): unknown {
  if (!/^[[{]/.test(text.trim())) return text;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/**
 * An API error's `detail` as one readable line: a string as it is; FastAPI's validation list
 * (`[{loc, msg}]`) as `<field>: <msg>` joined by "; " (the leading "body" of `loc` dropped).
 */
export function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((d) => {
      if (!d || typeof d !== "object") return String(d);
      const { loc, msg } = d as { loc?: unknown[]; msg?: unknown };
      const where = (Array.isArray(loc) ? loc : []).filter((x, i) => !(i === 0 && x === "body")).join(".");
      const text = typeof msg === "string" ? msg : JSON.stringify(d);
      return where ? `${where}: ${text}` : text;
    });
    return parts.join("; ") || "the request was refused";
  }
  if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  return detail === undefined || detail === null ? "the request was refused" : JSON.stringify(detail);
}

// ---- What each tab offers to file in Jira, by stable identity (ruling X17).

export interface JiraCandidate {
  item: JiraItem;
  /** One line naming the item (its key and text). */
  label: string;
  /** False when the item has nothing to track any more (decided keep or ask, answered, explained). */
  trackable: boolean;
  /** Why it is not trackable, for the disabled box. */
  note?: string;
}

const clip = (t: string, n = 140) => (t.length > n ? `${t.slice(0, n - 1)}…` : t);

/** Findings: one to track is undecided or decided Fix (keep and ask have nothing to track). */
export function findingCandidates(findings: readonly FindingView[]): JiraCandidate[] {
  return findings
    .filter((f) => f.finding_key)
    .map((f) => {
      const trackable = !f.decision || f.decision === "fix";
      return {
        item: { type: "finding" as const, ref: f.finding_key! },
        label: `${f.finding_key!.split("-")[0]}${f.severity ? ` (${f.severity})` : ""}: ${clip(f.text)}`,
        trackable,
        note: trackable ? undefined : `decided ${f.decision}`,
      };
    });
}

/** Open questions, by their stable id (never their position); answered ones have nothing to track. */
export function questionCandidates(questions: DesignRecord["open_questions"]): JiraCandidate[] {
  return questions
    .filter((q) => q.question_id)
    .map((q) => ({
      item: { type: "question" as const, ref: q.question_id! },
      label: clip(q.question),
      trackable: !q.decision,
      note: q.decision ? `${q.decision}` : undefined,
    }));
}

/** The server's `parity_key(scenario.key, field)` (api/app/connectors/jira.py): `<scenario key>:<field path>`. */
export function parityRef(scenarioKey: string, field: string): string {
  return `${scenarioKey}:${field}`;
}

/** Unexplained differences (defects) of the parity run, as `<scenario key>:<field path>`. */
export function parityCandidates(reports: readonly ScenarioReport[]): JiraCandidate[] {
  const out = new Map<string, JiraCandidate>();
  for (const r of reports) {
    if (!r.scenario_key) continue;
    for (const f of [...(r.fields ?? []), ...(r.side_effects ?? [])]) {
      if (f.status !== "defect") continue;
      const ref = parityRef(r.scenario_key, f.path);
      out.set(ref, {
        item: { type: "parity", ref },
        label: `${r.scenario_name ?? r.scenario_key}: ${f.path}`,
        trackable: true,
      });
    }
  }
  return [...out.values()];
}

