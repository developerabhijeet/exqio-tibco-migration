"use client";

// Who may do what, in one place. The five users of the top bar menu are personas of a migration
// team, and each one joins the pipeline at a different step: the TIBCO SME owns the estate from the
// upload, the architect from the design, the engineer from the build, QA from the verification, and
// operations at the deployment. From that step onwards a persona may act; before it the screens are
// open to read but their controls are not, so a reviewer can always see the evidence behind a step
// without being able to re-run or re-approve someone else's work.
//
// Each gate has exactly one owner, which is the persona whose name the step's own hint already
// gives ("the engineer approves the build"). The owner is the only persona offered approve and
// reject, so a G4 approval in the audit trail is always an engineer's.
//
// Scope: this is the screen's permission model. In AUTH_MODE=dev every request carries the chosen
// user as X-User and the API records that actor on the gate without refusing the wrong persona,
// so this blocks the control, not the endpoint. In AUTH_MODE=oidc there is no switcher: the user
// signed in with Entra ID holds role keys (one per persona, `roleKey` below), may act wherever any
// of them may (the union), and the API refuses a gate decision from anyone but its owner's role.
import { useCallback } from "react";
import { STEP_KEYS, useAppState, type StepKey } from "./state";
import { STEP_LABELS } from "./steps";
import type { UserName } from "./types";

export interface Persona {
  id: UserName;
  /** What this persona is called on screen. */
  role: string;
  /** One line: what they are responsible for. */
  responsibility: string;
  /** The first step they may act in; every later step is theirs too. Null: none (read only). */
  entry: StepKey | null;
  /** The gates only this persona may approve or reject. */
  gates: string[];
  /** The API's role key for this persona (AUTH_MODE=oidc roles, api/app/auth/roles.py). */
  roleKey: string;
}

export const PERSONAS: readonly Persona[] = [
  {
    id: "sme.vicky",
    role: "TIBCO SME",
    responsibility: "Owns the estate end to end: the upload, what the processes mean, and the rules and findings behind them.",
    entry: "upload",
    gates: ["G1", "G2"],
    roleKey: "sme",
  },
  {
    id: "architect.lee",
    role: "Solution architect",
    responsibility: "Owns the target design and everything downstream of it: the record, the build, the parity run and the plan.",
    entry: "design",
    gates: ["G3"],
    roleKey: "architect",
  },
  {
    id: "engineer.ravi",
    role: "Engineer",
    responsibility: "Owns the generated solution: the build, its tests and its infrastructure.",
    entry: "build",
    gates: ["G4"],
    roleKey: "engineer",
  },
  {
    id: "qa.priya",
    role: "QA lead",
    responsibility: "Owns the verification: the scenarios, the parity report and the differences it explains.",
    entry: "verify",
    gates: ["G5"],
    roleKey: "qa",
  },
  {
    id: "ops.sam",
    role: "Operations",
    responsibility: "Owns the deployment: the Azure connection, the plan and the release.",
    entry: "deploy",
    gates: ["G6"],
    roleKey: "ops",
  },
  {
    id: "viewer.val",
    role: "Viewer",
    responsibility: "Reads every step and the migration memory; acts in none and curates nothing.",
    entry: null,
    gates: [],
    roleKey: "viewer",
  },
] as const;

const BY_ID = new Map(PERSONAS.map((p) => [p.id as string, p]));

/** The persona of a user name, or the most restricted one for a name the screen does not know. */
export function personaOf(user: string): Persona {
  return BY_ID.get(user) ?? PERSONAS[PERSONAS.length - 1];
}

/**
 * AUTH_MODE=oidc: the signed-in user as one persona, the union of the personas their role keys
 * name: acting from the earliest entry step, owning every one of their gates. A user with no
 * persona role (viewer, admin only) reads everything and acts nowhere.
 */
export function personaForRoles(roles: readonly string[]): Persona {
  const held = PERSONAS.filter((p) => p.entry !== null && roles.includes(p.roleKey));
  if (held.length === 1) return held[0];
  if (held.length === 0) {
    return {
      id: PERSONAS[PERSONAS.length - 1].id,
      role: roles.includes("viewer") ? "Viewer" : "No migration role",
      responsibility: "Reads every step; acts in none.",
      entry: null,
      gates: [],
      roleKey: "",
    };
  }
  const first = held.reduce((a, b) => (stepIndex(b.entry) < stepIndex(a.entry) ? b : a));
  return {
    id: first.id,
    role: held.map((p) => p.role).join(" + "),
    responsibility: held.map((p) => p.responsibility).join(" "),
    entry: first.entry,
    gates: held.flatMap((p) => p.gates),
    roleKey: held.map((p) => p.roleKey).join(","),
  };
}

function stepIndex(step: StepKey | null): number {
  return step === null ? STEP_KEYS.length : STEP_KEYS.indexOf(step);
}

/** Who the screens act as: the chosen persona in dev, the signed-in user's roles in oidc. */
export function useActingPersona(): Persona {
  const { user, auth } = useAppState();
  return auth.mode === "oidc" ? personaForRoles(auth.me?.roles ?? []) : personaOf(user);
}

/** The API's WRITE_ROLES (api/app/deps.py): what `require_curator` lets through. */
const WRITE_ROLE_KEYS = ["admin", "sme", "architect", "engineer", "qa", "ops"] as const;

/**
 * Whether this user may curate the migration memory (pin a note, correct or reject a mapping, mark
 * memory obsolete): any write role; a viewer never. The API refuses the rest (403 read-only).
 */
/** AUTH_MODE=dev: the personas that also hold `admin` (api/app/auth/roles.py: ops.sam). */
const DEV_ADMINS: readonly string[] = ["ops.sam"];

export function useCan(): { curate: boolean; admin: boolean } {
  const { auth, user } = useAppState();
  const persona = useActingPersona();
  const roles = auth.mode === "oidc" ? (auth.me?.roles ?? []) : null;
  const curate = roles ? roles.some((r) => (WRITE_ROLE_KEYS as readonly string[]).includes(r)) : persona.entry !== null;
  // Admin-only controls (re-ingest) show from the role, without probing an admin route that
  // answers 403 to everyone else. The API still decides.
  const admin = roles ? roles.includes("admin") : DEV_ADMINS.includes(user);
  return { curate, admin };
}

/** The steps this persona may act in: the entry step and everything after it. */
export function stepsOf(persona: Persona): StepKey[] {
  return STEP_KEYS.slice(stepIndex(persona.entry)) as StepKey[];
}

export function canActOn(persona: Persona, step: StepKey): boolean {
  return STEP_KEYS.indexOf(step) >= stepIndex(persona.entry);
}

/** The persona that owns a gate, or null for a gate name no persona claims. */
export function ownerOfGate(gate: string): Persona | null {
  return PERSONAS.find((p) => p.gates.includes(gate)) ?? null;
}

/**
 * Why this persona cannot act on this step, phrased for a tooltip; null when they can. `signedIn`
 * (AUTH_MODE=oidc) has no switcher to point at: the role has to be granted.
 */
export function blockedReason(persona: Persona, step: StepKey, signedIn = false): string | null {
  if (canActOn(persona, step)) return null;
  const owners = PERSONAS.filter((p) => canActOn(p, step)).map((p) => p.role);
  const label = STEP_LABELS[step] ?? step;
  const next = signedIn ? "Ask for one of those roles to act here." : "Switch user in the top bar to act here.";
  return `${label} is open to you to read, not to change: you are acting as ${persona.role}, and this step belongs to ${list(owners)}. ${next}`;
}

function list(items: string[]): string {
  if (items.length <= 1) return items[0] ?? "nobody";
  return `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`;
}

export interface Permission {
  persona: Persona;
  /** False when the step is read-only for this persona. */
  canAct: boolean;
  /** The tooltip for a disabled control, or undefined when nothing is blocked. */
  reason: string | undefined;
  /** True when this persona owns the gate and may approve or reject it. */
  ownsGate: (gate: string) => boolean;
  /** Why a gate's buttons are disabled for this persona, or null when they are not. */
  gateReason: (gate: string) => string | null;
}

/**
 * What the current user may do on one step. `canAct` guards a control that changes something (run a
 * job, upload, approve); reading is never blocked.
 */
export function usePermission(step: StepKey): Permission {
  const { auth } = useAppState();
  const persona = useActingPersona();
  const canAct = canActOn(persona, step);
  const reason = blockedReason(persona, step, auth.mode === "oidc") ?? undefined;
  const gates = persona.gates.join(",");
  const role = persona.role;
  const ownsGate = useCallback((gate: string) => gates.split(",").includes(gate), [gates]);
  const gateReason = useCallback(
    (gate: string) => {
      if (gates.split(",").includes(gate)) return null;
      const owner = ownerOfGate(gate);
      return owner
        ? `${gate} is the ${owner.role}'s gate. You are acting as ${role}, so you can read the evidence here but not decide it.`
        : `${gate} has no owner in this screen's role model, so no persona may decide it.`;
    },
    [gates, role],
  );
  return { persona, canAct, reason, ownsGate, gateReason };
}
