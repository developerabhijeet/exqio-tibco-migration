"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Pill } from "@/components/ui/Pill";
import type { FindingView } from "@/lib/types";
import { CitationChip } from "./CitationChip";

const DECISIONS = [
  { value: "keep", label: "Keep behaviour", help: "Generate the same behaviour as TIBCO" },
  { value: "fix", label: "Fix", help: "Correct it in .NET; recorded as an expected difference" },
  { value: "ask", label: "Ask business", help: "Keep the behaviour and record the question for the business owner" },
] as const;

const SEVERITY: Record<string, "error" | "decision" | "det"> = { high: "error", medium: "decision", low: "det" };

/** Short display key: S2 for S2-DealerFundingRequest-1, F1 for F1. */
export function shortKey(key: string): string {
  return key.split("-")[0];
}

export function FindingCard({
  finding,
  onDecide,
}: {
  finding: FindingView;
  onDecide: (findingId: string, body: { decision: string; note: string | null }) => Promise<void>;
}) {
  const [note, setNote] = useState(finding.note ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = finding.finding_key ?? finding.id;
  const decision = finding.decision ?? "";
  const noteId = `note-${finding.id}`;

  const decide = async (value: string) => {
    setBusy(value);
    setError(null);
    try {
      await onDecide(finding.id, { decision: value, note: note.trim() || null });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <article data-testid="finding-card" data-finding={key} data-decision={decision} className="card p-4">
      <header className="flex flex-wrap items-center gap-2">
        <span className="rounded-btn bg-plum px-2 py-0.5 font-mono text-[12px] font-semibold text-white" title={key}>
          {shortKey(key)}
        </span>
        <Pill tone={SEVERITY[finding.severity ?? ""] ?? "det"}>{finding.severity ?? "unrated"}</Pill>
        <Pill tone={finding.source === "ai" ? "ai" : "det"} title={finding.source === "ai" ? "Raised by the risk reviewer model" : "Raised by a code rule"}>
          {finding.source === "ai" ? "AI" : "code"}
        </Pill>
        {decision ? (
          <span className="ml-auto text-[12.5px] text-slate">
            decided <span className="font-semibold text-plum">{DECISIONS.find((d) => d.value === decision)?.label ?? decision}</span>
            {finding.decided_by ? ` by ${finding.decided_by}` : ""}
          </span>
        ) : (
          <span className="ml-auto">
            <Pill tone="decision">needs decision</Pill>
          </span>
        )}
      </header>
      <p className="mt-2 text-[15px] leading-relaxed text-ink">{finding.text}</p>
      {finding.business_impact ? (
        <p className="mt-2 rounded-btn bg-warm px-3 py-2 text-[13.5px] text-ink">
          <span className="label mr-1.5">Business impact</span>
          {finding.business_impact}
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <span className="label mr-1">Evidence</span>
        {(finding.citations ?? [])
          .filter((c, i, all) => all.findIndex((o) => o.activity_id === c.activity_id && o.kind === c.kind) === i)
          .map((c, i) => (
            <CitationChip key={`${c.activity_id}-${i}`} citation={c} />
          ))}
      </div>
      <div className="mt-3 border-t border-line pt-3">
        <label htmlFor={noteId} className="label">
          Note
        </label>
        <textarea
          id={noteId}
          className="input mt-1 min-h-[44px] text-[14px]"
          placeholder="Why this decision; kept with the audit trail"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <div className="mt-2 flex flex-wrap items-center gap-2" role="group" aria-label={`Decision for ${key}`}>
          {DECISIONS.map((d) => {
            const on = decision === d.value;
            return (
              <button
                key={d.value}
                type="button"
                aria-pressed={on}
                title={d.help}
                disabled={!!busy || (on && note.trim() === (finding.note ?? ""))}
                onClick={() => decide(d.value)}
                className={`btn px-3 py-1.5 text-[14px] ${
                  on ? "bg-plum text-white disabled:opacity-100" : "border border-line bg-white text-plum hover:bg-warm"
                }`}
              >
                {busy === d.value ? <Loader2 size={14} className="animate-spin" aria-hidden /> : null}
                {d.label}
              </button>
            );
          })}
          {decision && note.trim() !== (finding.note ?? "") ? (
            <span className="text-[12.5px] text-slate">Click the chosen decision again to save the note.</span>
          ) : null}
        </div>
      </div>
      {error ? (
        <p role="alert" className="mt-2 text-[13px] text-error">
          {error}
        </p>
      ) : null}
    </article>
  );
}
