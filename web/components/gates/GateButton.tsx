"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, ShieldCheck } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { Pill } from "@/components/ui/Pill";
import type { Gate } from "@/lib/types";

export const GATE_TITLES: Record<string, string> = {
  G1: "G1 rules",
  G2: "G2 findings",
  G3: "G3 design",
  G4: "G4 build",
  G5: "G5 parity",
  G6: "G6 deploy",
};

/** pending reads "needs review" once there is something to review, or after a reset. */
export function gateDisplayState(gate: Gate | undefined, started: boolean): string {
  if (!gate) return "pending";
  if (gate.state === "pending" && (gate.reset_reason || started)) return "needs_review";
  return gate.state;
}

const LABEL: Record<string, { text: string; tone: "gate" | "decision" | "error" | "det" }> = {
  approved: { text: "approved", tone: "gate" },
  rejected: { text: "rejected", tone: "error" },
  needs_review: { text: "needs review", tone: "decision" },
  pending: { text: "not started", tone: "det" },
};

function when(ts: string | null | undefined): string {
  return ts ? new Date(ts).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
}

/**
 * A gate and its approve button. The API decides enablement: a closed gate shows the server's
 * reason as the tooltip and inline, and a 409 on approve is shown the same way.
 */
export function GateButton({
  processId,
  gate,
  name,
  started,
  hint,
}: {
  processId: string;
  gate: Gate | undefined;
  name: string;
  started: boolean;
  hint?: string;
}) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const state = gateDisplayState(gate, started);
  const label = LABEL[state] ?? LABEL.pending;
  const enabled = !!gate?.enabled && state !== "approved";
  const blocked = !gate?.enabled && state !== "approved" ? (gate?.reason ?? "The API has not reported this gate.") : null;

  const approve = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.decideGate(processId, name, "approved");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
      for (const q of ["gates", "analysis", "audit", "design"]) qc.invalidateQueries({ queryKey: [q, processId] });
      qc.invalidateQueries({ queryKey: ["processes"] });
    }
  };

  return (
    <div
      data-testid={`gate-${name}`}
      data-state={state}
      className={`rounded-card border p-4 ${state === "approved" ? "border-gate/40 bg-gate-bg/50" : "border-line bg-white"}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <ShieldCheck size={18} className={state === "approved" ? "text-gate" : "text-slate"} aria-hidden />
        <span className="font-semibold text-plum">{GATE_TITLES[name] ?? name}</span>
        <Pill tone={label.tone} testId={`gate-${name}-state`}>
          {label.text}
        </Pill>
        <span className="ml-auto" title={blocked ?? undefined}>
          {state === "approved" ? null : (
            <button
              type="button"
              disabled={!enabled || busy}
              onClick={approve}
              className="btn bg-gate text-white hover:bg-gate/90"
            >
              {busy ? <Loader2 size={15} className="animate-spin" aria-hidden /> : null}
              Approve {name}
            </button>
          )}
        </span>
      </div>
      {hint ? <p className="mt-1 text-[13px] text-slate">{hint}</p> : null}
      {state === "approved" && gate ? (
        <p className="mt-2 text-[13px] text-gate">
          Approved by <span className="font-semibold">{gate.decided_by}</span> on {when(gate.decided_at)}
          {gate.spec_hash ? (
            <span className="ml-2 font-mono text-[11.5px] text-slate" title={`Spec hash ${gate.spec_hash}`}>
              spec {gate.spec_hash.slice(0, 12)}
            </span>
          ) : null}
        </p>
      ) : null}
      {state !== "approved" && gate?.reset_reason ? (
        <p className="mt-2 text-[13px] text-decision">Reopened: {gate.reset_reason}</p>
      ) : null}
      {blocked && state !== "approved" ? (
        <p className="mt-1 text-[13px] text-slate">
          <span className="font-medium text-plum">Not enabled:</span> {blocked}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-1 text-[13px] text-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
