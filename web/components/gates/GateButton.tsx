"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, ShieldCheck, ShieldX } from "lucide-react";
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

function Hashes({ gate }: { gate: Gate }) {
  const rows = [
    ["spec", gate.spec_hash],
    ["design", gate.design_hash],
    ["build", gate.build_hash],
    ["evidence", gate.evidence_hash],
  ].filter(([, h]) => !!h) as [string, string][];
  if (rows.length === 0) return null;
  return (
    <span className="mt-1 flex flex-wrap gap-x-3 font-mono text-[11.5px] text-slate">
      {rows.map(([label, h]) => (
        <span key={label} title={`${label} hash ${h}`} data-testid={`gate-${gate.gate}-${label}-hash`}>
          {label} {h.slice(0, 12)}
        </span>
      ))}
    </span>
  );
}

/**
 * A gate with its approve and reject buttons. The API decides whether approval is enabled: a
 * closed gate shows the server's reason as the tooltip and inline, and a 409 is shown the same
 * way. The owner may always reject, with a required note recorded as the reason.
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
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const state = gateDisplayState(gate, started);
  const label = LABEL[state] ?? LABEL.pending;
  const enabled = !!gate?.enabled && state !== "approved";
  const blocked = !gate?.enabled && state !== "approved" ? (gate?.reason ?? "The API has not reported this gate.") : null;

  const decide = async (decision: "approved" | "rejected") => {
    setBusy(decision === "approved" ? "approve" : "reject");
    setError(null);
    try {
      await api.decideGate(processId, name, decision, decision === "rejected" ? note.trim() : undefined);
      if (decision === "rejected") {
        setRejecting(false);
        setNote("");
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
      for (const q of ["gates", "analysis", "audit", "design"]) qc.invalidateQueries({ queryKey: [q, processId] });
      qc.invalidateQueries({ queryKey: ["processes"] });
      qc.invalidateQueries({ queryKey: ["readiness"] });
    }
  };

  const tone =
    state === "approved" ? "border-gate/40 bg-gate-bg/50" : state === "rejected" ? "border-error/40 bg-error-bg/50" : "border-line bg-white";
  return (
    <div data-testid={`gate-${name}`} data-state={state} className={`rounded-card border p-4 ${tone}`}>
      <div className="flex flex-wrap items-center gap-2">
        {state === "rejected" ? (
          <ShieldX size={18} className="text-error" aria-hidden />
        ) : (
          <ShieldCheck size={18} className={state === "approved" ? "text-gate" : "text-slate"} aria-hidden />
        )}
        <span className="font-semibold text-plum">{GATE_TITLES[name] ?? name}</span>
        <Pill tone={label.tone} testId={`gate-${name}-state`}>
          {label.text}
        </Pill>
        <span className="ml-auto flex items-center gap-2">
          {state !== "rejected" && !rejecting ? (
            <button
              type="button"
              disabled={!!busy}
              onClick={() => setRejecting(true)}
              className="btn border border-line bg-white px-3 text-error hover:bg-error-bg"
            >
              Reject {name}
            </button>
          ) : null}
          {state === "approved" ? null : (
            <span title={blocked ?? undefined}>
              <button
                type="button"
                disabled={!enabled || !!busy}
                onClick={() => decide("approved")}
                className="btn bg-gate text-white hover:bg-gate/90"
              >
                {busy === "approve" ? <Loader2 size={15} className="animate-spin" aria-hidden /> : null}
                Approve {name}
              </button>
            </span>
          )}
        </span>
      </div>
      {hint ? <p className="mt-1 text-[13px] text-slate">{hint}</p> : null}
      {state === "approved" && gate ? (
        <div className="mt-2 text-[13px] text-gate">
          Approved by <span className="font-semibold">{gate.decided_by}</span> on {when(gate.decided_at)}
          {gate.note ? <span className="text-ink">: {gate.note}</span> : null}
          <Hashes gate={gate} />
        </div>
      ) : null}
      {state === "rejected" && gate ? (
        <div className="mt-2 text-[13px] text-error" data-testid={`gate-${name}-rejection`}>
          Rejected by <span className="font-semibold">{gate.decided_by}</span> on {when(gate.decided_at)}
          {gate.note ? (
            <p className="mt-1 rounded-btn bg-white/70 px-2.5 py-1.5 text-ink">
              <span className="label mr-1.5">Reason</span>
              {gate.note}
            </p>
          ) : null}
          <Hashes gate={gate} />
        </div>
      ) : null}
      {state !== "approved" && state !== "rejected" && gate?.reset_reason ? (
        <p className="mt-2 text-[13px] text-decision">Reopened: {gate.reset_reason}</p>
      ) : null}
      {blocked && state !== "approved" ? (
        <p className="mt-1 text-[13px] text-slate">
          <span className="font-medium text-plum">Not enabled:</span> {blocked}
        </p>
      ) : null}
      {rejecting ? (
        <form
          className="mt-3 space-y-2 border-t border-line pt-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (note.trim()) decide("rejected");
          }}
        >
          <label className="label" htmlFor={`reject-${name}`}>
            Reason for rejecting {name} (required)
          </label>
          <textarea
            id={`reject-${name}`}
            className="input min-h-[60px] text-[14px]"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="What must change before this gate can be approved"
          />
          <p className="text-[12.5px] text-slate">Rejecting {name} also reopens every later gate that was decided.</p>
          <div className="flex gap-2">
            <button type="submit" className="btn bg-error px-3 text-white hover:bg-error/90" disabled={!note.trim() || !!busy}>
              {busy === "reject" ? <Loader2 size={15} className="animate-spin" aria-hidden /> : null}
              Confirm rejection
            </button>
            <button
              type="button"
              className="btn-secondary px-3"
              onClick={() => {
                setRejecting(false);
                setNote("");
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : null}
      {error ? (
        <p role="alert" className="mt-1 text-[13px] text-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
