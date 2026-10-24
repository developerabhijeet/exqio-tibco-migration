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
// Scope: this is the screen's permission model. Every request still carries the chosen user as
// X-User and the API records that actor on the gate, but the API does not yet refuse a decision
// from the wrong persona, so this blocks the control, not the endpoint. Enforcing it server side
// belongs with the gate service, where the actor is already written.
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
  /** The first step they may act in; every later step is theirs too. */
  entry: StepKey;
  /** The gates only this persona may approve or reject. */
  gates: string[];
}

export const PERSONAS: readonly Persona[] = [
  {
    id: "sme.vicky",
    role: "TIBCO SME",
    responsibility: "Owns the estate end to end: the upload, what the processes mean, and the rules and findings behind them.",
    entry: "upload",
    gates: ["G1", "G2"],
  },
  {
    id: "architect.lee",
    role: "Solution architect",
    responsibility: "Owns the target design and everything downstream of it: the record, the build, the parity run and the plan.",
    entry: "design",
    gates: ["G3"],
  },
  {
    id: "engineer.ravi",
    role: "Engineer",
    responsibility: "Owns the generated solution: the build, its tests and its infrastructure.",
    entry: "build",
    gates: ["G4"],
  },
  {
    id: "qa.priya",
    role: "QA lead",
    responsibility: "Owns the verification: the scenarios, the parity report and the differences it explains.",
    entry: "verify",
    gates: ["G5"],
  },
  {
    id: "ops.sam",
    role: "Operations",
    responsibility: "Owns the deployment: the Azure connection, the plan and the release.",
    entry: "deploy",
    gates: ["G6"],
  },
] as const;

const BY_ID = new Map(PERSONAS.map((p) => [p.id as string, p]));

/** The persona of a user name, or the most restricted one for a name the screen does not know. */
export function personaOf(user: string): Persona {
  return BY_ID.get(user) ?? PERSONAS[PERSONAS.length - 1];
}

/** The steps this persona may act in: the entry step and everything after it. */
export function stepsOf(persona: Persona): StepKey[] {
  return STEP_KEYS.slice(STEP_KEYS.indexOf(persona.entry)) as StepKey[];
}

export function canActOn(persona: Persona, step: StepKey): boolean {
  return STEP_KEYS.indexOf(step) >= STEP_KEYS.indexOf(persona.entry);
}

/** The persona that owns a gate, or null for a gate name no persona claims. */
export function ownerOfGate(gate: string): Persona | null {
  return PERSONAS.find((p) => p.gates.includes(gate)) ?? null;
}

/** Why this persona cannot act on this step, phrased for a tooltip; null when they can. */
export function blockedReason(persona: Persona, step: StepKey): string | null {
  if (canActOn(persona, step)) return null;
  const owners = PERSONAS.filter((p) => canActOn(p, step)).map((p) => p.role);
  const label = STEP_LABELS[step] ?? step;
  return `${label} is open to you to read, not to change: you are acting as ${persona.role}, and this step belongs to ${list(owners)}. Switch user in the top bar to act here.`;
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
  const { user } = useAppState();
  const persona = personaOf(user);
  const canAct = canActOn(persona, step);
  const reason = blockedReason(persona, step) ?? undefined;
  const ownsGate = useCallback((gate: string) => personaOf(user).gates.includes(gate), [user]);
  const gateReason = useCallback(
    (gate: string) => {
      const me = personaOf(user);
      if (me.gates.includes(gate)) return null;
      const owner = ownerOfGate(gate);
      return owner
        ? `${gate} is the ${owner.role}'s gate. You are acting as ${me.role}, so you can read the evidence here but not decide it.`
        : `${gate} has no owner in this screen's role model, so no persona may decide it.`;
    },
    [user],
  );
  return { persona, canAct, reason, ownsGate, gateReason };
}
