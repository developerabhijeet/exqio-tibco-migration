"use client";

import { useState } from "react";
import { Check, Loader2, Pencil, X } from "lucide-react";
import { Pill } from "@/components/ui/Pill";
import type { RuleView } from "@/lib/types";
import { ChallengeBadge } from "./ChallengeBadge";
import { CitationChip } from "./CitationChip";

const STATUS: Record<string, { label: string; tone: "gate" | "decision" | "error" | "det" }> = {
  draft: { label: "draft", tone: "decision" },
  approved: { label: "approved", tone: "gate" },
  edited: { label: "edited", tone: "gate" },
  rejected: { label: "rejected", tone: "error" },
  unverified: { label: "unverified", tone: "error" },
};

export function RuleCard({
  rule,
  onDecide,
}: {
  rule: RuleView;
  onDecide: (ruleId: string, body: { status?: "approved" | "rejected"; text?: string }) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(rule.text);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const status = rule.status ?? "draft";
  const s = STATUS[status] ?? STATUS.draft;
  const key = rule.rule_key ?? rule.id;

  const act = async (what: string, body: { status?: "approved" | "rejected"; text?: string }) => {
    setBusy(what);
    setError(null);
    try {
      await onDecide(rule.id, body);
      if (what === "save") setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <article
      data-testid="rule-card"
      data-rule={key}
      data-status={status}
      className={`card p-4 ${status === "rejected" ? "opacity-70" : ""}`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <span className="rounded-btn bg-plum px-2 py-0.5 font-mono text-[12px] font-semibold text-white">{key}</span>
        <Pill tone={s.tone}>{s.label}</Pill>
        {typeof rule.confidence === "number" ? (
          <span className="text-[12.5px] text-slate" title="Analyst confidence">
            confidence {Math.round(rule.confidence * 100)}%
          </span>
        ) : null}
        <span className="ml-auto">
          <ChallengeBadge challenge={rule.challenge} />
        </span>
      </header>

      {editing ? (
        <div className="mt-3 space-y-2">
          <textarea
            aria-label={`Text of rule ${key}`}
            className="input min-h-[96px] text-[14.5px]"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <p className="text-[12.5px] text-slate">
            Saving re-verifies the citations against the parsed model and reopens G1.
          </p>
        </div>
      ) : (
        <p className="mt-2 text-[15px] leading-relaxed text-ink">{rule.text}</p>
      )}
      {rule.original_text && rule.original_text !== rule.text && !editing ? (
        <p className="mt-1 text-[12.5px] text-slate">
          Drafted as: <span className="italic">{rule.original_text}</span>
        </p>
      ) : null}

      {rule.challenge?.verdict && rule.challenge.verdict !== "agree" ? (
        <p className="mt-2 rounded-btn bg-decision-bg px-3 py-2 text-[13.5px] text-decision">{rule.challenge.reason}</p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <span className="label mr-1">Cites</span>
        {(rule.citations ?? []).map((c, i) => (
          <CitationChip key={`${c.activity_id}-${i}`} citation={c} />
        ))}
        {rule.owner ? <span className="ml-2 text-[12.5px] text-slate">owner: {rule.owner}</span> : null}
        {rule.memory_refs && rule.memory_refs.length > 0 ? (
          <Pill tone="purple" title="Earlier decisions retrieved from migration memory">
            {rule.memory_refs.length} earlier decision{rule.memory_refs.length > 1 ? "s" : ""}
          </Pill>
        ) : null}
      </div>

      <footer className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
        {editing ? (
          <>
            <button
              type="button"
              className="btn-primary px-3 py-1.5 text-[14px]"
              disabled={!!busy || text.trim() === "" || text.trim() === rule.text}
              onClick={() => act("save", { text: text.trim() })}
            >
              {busy === "save" ? <Loader2 size={14} className="animate-spin" aria-hidden /> : null}
              Save
            </button>
            <button
              type="button"
              className="btn-secondary px-3 py-1.5 text-[14px]"
              onClick={() => {
                setEditing(false);
                setText(rule.text);
              }}
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            {status === "approved" ? (
              <span className="inline-flex items-center gap-1.5 rounded-btn border border-gate/30 bg-gate-bg px-3 py-1.5 text-[14px] font-medium text-gate">
                <Check size={14} aria-hidden /> Approved
              </span>
            ) : (
              <button
                type="button"
                className="btn bg-gate px-3 py-1.5 text-[14px] text-white hover:bg-gate/90"
                disabled={!!busy || status === "unverified"}
                title={status === "unverified" ? "A rule with an unverified citation cannot be approved" : undefined}
                onClick={() => act("approve", { status: "approved" })}
              >
                {busy === "approve" ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <Check size={14} aria-hidden />}
                Approve
              </button>
            )}
            <button
              type="button"
              className="btn-secondary px-3 py-1.5 text-[14px]"
              disabled={!!busy}
              onClick={() => {
                setText(rule.text);
                setEditing(true);
              }}
            >
              <Pencil size={14} aria-hidden /> Edit
            </button>
            <button
              type="button"
              className="btn-secondary px-3 py-1.5 text-[14px] text-error"
              disabled={!!busy || status === "rejected"}
              onClick={() => act("reject", { status: "rejected" })}
            >
              {busy === "reject" ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <X size={14} aria-hidden />}
              Reject
            </button>
          </>
        )}
        {rule.updated_by ? (
          <span className="ml-auto text-[12px] text-slate">
            {status} by {rule.updated_by}
          </span>
        ) : null}
      </footer>
      {error ? (
        <p role="alert" className="mt-2 text-[13px] text-error">
          {error}
        </p>
      ) : null}
    </article>
  );
}
