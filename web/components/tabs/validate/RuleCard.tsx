"use client";

import { useState } from "react";
import { Check, Loader2, Pencil, X } from "lucide-react";
import { Pill } from "@/components/ui/Pill";
import type { RuleView } from "@/lib/types";
import { ChallengeBadge } from "./ChallengeBadge";
import { CitedText } from "@/components/citations/CitedText";

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
      className={`card card-compact ${status === "rejected" ? "opacity-70" : ""}`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <span className="rounded-btn bg-heading px-2 py-0.5 font-mono text-xs font-semibold text-page">{key}</span>
        <Pill tone={s.tone}>{s.label}</Pill>
        {typeof rule.confidence === "number" ? (
          <span className="text-sm text-slate" title="Analyst confidence">
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
            className="input min-h-[96px] text-base"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <p className="text-sm text-slate">
            Saving re-verifies the citations against the parsed model and reopens G1.
          </p>
        </div>
      ) : (
        <p className="mt-2 text-base leading-relaxed text-ink" data-testid="rule-text">
          {rule.cited ? <CitedText value={rule.cited} /> : rule.text}
        </p>
      )}
      {rule.original_text && rule.original_text !== rule.text && !editing ? (
        <p className="mt-1 text-sm text-slate">
          Drafted as: <span className="italic">{rule.original_text}</span>
        </p>
      ) : null}

      {rule.challenge?.verdict && rule.challenge.verdict !== "agree" ? (
        <p className="mt-2 rounded-btn bg-decision-bg px-3 py-2 text-md text-decision">{rule.challenge.reason}</p>
      ) : null}

      <footer className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
        {editing ? (
          <>
            <button
              type="button"
              className="btn-primary px-3 py-1.5 text-md"
              disabled={!!busy || text.trim() === "" || text.trim() === rule.text}
              onClick={() => act("save", { text: text.trim() })}
            >
              {busy === "save" ? <Loader2 size={14} className="animate-spin" aria-hidden /> : null}
              Save
            </button>
            <button
              type="button"
              className="btn-secondary px-3 py-1.5 text-md"
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
              <span className="inline-flex items-center gap-1.5 rounded-btn border border-gate/30 bg-gate-bg px-3 py-1.5 text-md font-medium text-gate">
                <Check size={14} aria-hidden /> Approved
              </span>
            ) : (
              <button
                type="button"
                className="btn bg-gate px-3 py-1.5 text-md text-surface hover:bg-gate/90"
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
              className="btn-secondary px-3 py-1.5 text-md"
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
              className="btn-secondary px-3 py-1.5 text-md text-error"
              disabled={!!busy || status === "rejected"}
              onClick={() => act("reject", { status: "rejected" })}
            >
              {busy === "reject" ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <X size={14} aria-hidden />}
              Reject
            </button>
          </>
        )}
        <span className="ml-auto flex flex-wrap items-center gap-2">
          {rule.owner ? <span className="text-sm text-slate">owner: {rule.owner}</span> : null}
          {rule.memory_refs && rule.memory_refs.length > 0 ? (
            <Pill tone="purple" title="Earlier decisions retrieved from migration memory">
              {rule.memory_refs.length} earlier decision{rule.memory_refs.length > 1 ? "s" : ""}
            </Pill>
          ) : null}
          {rule.updated_by ? (
            <span className="text-xs text-slate">
              {status} by {rule.updated_by}
            </span>
          ) : null}
        </span>
      </footer>
      {error ? (
        <p role="alert" className="mt-2 text-sm text-error">
          {error}
        </p>
      ) : null}
    </article>
  );
}
