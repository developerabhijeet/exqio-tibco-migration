"use client";

// The drawer's Curate tab (curators only: useCan().curate). The actions offered are the ones that
// apply to the node: a note on anything; reject or correct a mapping when it has an outgoing
// MIGRATED_TO; obsolete on a Memory item. Every action needs a reason. A curation becomes part of
// the memory with the next ingest revision, so a submission shows "Pending" until the revision
// whose scopes hold it lands, then "Applied in revision <n>".
import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Archive, ArrowRight, Ban, CheckCircle2, Clock, Loader2, PenLine, Pin, RotateCw, Search } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { ErrorNote } from "@/components/ui/Empty";
import { ApiError, api } from "@/lib/api";
import { LabelChip } from "./SearchRail";
import {
  curationScope,
  displayName,
  hitTitle,
  useCurate,
  useMemorySearch,
  useMemoryStatus,
  useRevisionOfScope,
  type CurationAction,
  type LineageOut,
  type NodeDetail,
} from "@/lib/memory";

/** One submitted curation, kept by the drawer so it outlives tab and node switches. */
export interface Submission {
  eventId: string;
  uid: string;
  action: CurationAction;
  /** The memory's revision when it was sent. */
  base: number | null;
  /** The memory.ingest job that applies it. */
  jobId: string;
  /** Date.now() when it was sent. */
  sentAt: number;
}

export interface CurateFormProps {
  projectId: string;
  detail: NodeDetail;
  /** The explorer's as-of revision: curation acts on the current memory only. */
  asOf: number | null;
  submissions: Submission[];
  onSubmitted(s: Submission): void;
  onShowKnowledge(): void;
  onBackToCurrent?(): void;
}

const ACTIONS: Record<CurationAction, { title: string; blurb: string; submit: string; icon: typeof Pin }> = {
  note: { title: "Pin a note", blurb: "Context agents and reviewers read with this node.", submit: "Pin note", icon: Pin },
  reject_mapping: { title: "Reject mapping", blurb: "The generated code does not implement this.", submit: "Reject mapping", icon: Ban },
  correct_mapping: { title: "Correct mapping", blurb: "Point it at the method or class that does.", submit: "Correct mapping", icon: PenLine },
  obsolete: { title: "Mark obsolete", blurb: "Agents stop retrieving this memory.", submit: "Mark obsolete", icon: Archive },
};

const REASON_MAX = 2000;
const NOTE_MAX = 4000;

/** After this long without a revision, a submission says it is still waiting and why. */
const WAIT_MS = 45_000;

const APPLIED_TEXT: Record<CurationAction, string> = {
  note: "Your note is part of the memory.",
  reject_mapping: "The mapping is marked rejected.",
  correct_mapping: "The mapping now points at the replacement.",
  obsolete: "Agents no longer retrieve this memory.",
};

function SubmissionState({ projectId, s, onShowKnowledge }: { projectId: string; s: Submission; onShowKnowledge(): void }) {
  const status = useMemoryStatus(projectId);
  const latest = status.data?.revision ?? null;
  const lookup = useRevisionOfScope(projectId, curationScope(s.eventId), s.base, latest);
  const applied = lookup.data ?? null;
  // The ingest job: a final failure carries its reason.
  const job = useQuery({
    queryKey: ["job", s.jobId],
    queryFn: () => api.job(s.jobId),
    enabled: applied === null,
    refetchInterval: (q) => (q.state.data && ["done", "failed"].includes(q.state.data.status) ? false : 4000),
  });
  const failed = applied === null && job.data?.status === "failed";
  // Waiting long, or memory reported down: say so rather than spin forever.
  const [late, setLate] = useState(false);
  useEffect(() => {
    if (applied !== null) return;
    const t = window.setTimeout(() => setLate(true), Math.max(0, s.sentAt + WAIT_MS - Date.now()));
    return () => window.clearTimeout(t);
  }, [applied, s.sentAt]);
  const unavailable = status.data?.available === false;
  const waiting = applied === null && !failed && (late || unavailable);

  // The submit button sits at the foot of a scrolled panel: bring the new state into view.
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const smooth = !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    box.current?.scrollIntoView({ block: "nearest", behavior: smooth ? "smooth" : "auto" });
  }, [s.eventId]);

  const refresh = () => {
    void status.refetch();
    void job.refetch();
    void lookup.refetch();
  };
  const checking = status.isFetching || job.isFetching || lookup.isFetching;
  const tone = applied ? "bg-gate-bg ring-gate/20" : failed ? "bg-error-bg ring-error/20" : waiting ? "bg-decision-bg ring-decision/20" : "bg-sunken/80 ring-line";
  const title = applied
    ? `Applied in revision ${applied}`
    : failed
      ? "Not applied: the memory ingest failed"
      : waiting
        ? "Still waiting — memory may be unavailable"
        : "Pending";
  const pending = status.data?.pending_ingests ?? 0;

  return (
    <div ref={box} className={`flex scroll-mt-4 items-start gap-3 rounded-btn px-4 py-3 ring-1 ring-inset transition-colors ${tone}`} role="status">
      {applied ? (
        <CheckCircle2 size={17} className="mt-0.5 shrink-0 text-gate" aria-hidden />
      ) : failed ? (
        <AlertTriangle size={17} className="mt-0.5 shrink-0 text-error" aria-hidden />
      ) : waiting ? (
        <Clock size={17} className="mt-0.5 shrink-0 text-decision" aria-hidden />
      ) : (
        <Loader2 size={17} className="mt-0.5 shrink-0 animate-spin text-slate" aria-hidden />
      )}
      <div className="min-w-0 flex-1">
        <p
          data-testid="curate-state"
          className={`text-md font-semibold ${applied ? "text-gate" : failed ? "text-error" : waiting ? "text-decision" : "text-heading"}`}
        >
          {title}
        </p>
        <div className="mt-0.5 text-sm leading-relaxed text-slate">
          {applied ? (
            <>
              {APPLIED_TEXT[s.action]}{" "}
              {s.action === "note" ? (
                <button type="button" className="font-medium text-accent-ink hover:underline" onClick={onShowKnowledge}>
                  See it under Knowledge
                </button>
              ) : null}
            </>
          ) : failed ? (
            <>
              <p className="break-words font-mono text-xs text-error">{job.data?.error || "No reason recorded."}</p>
              <p className="mt-1">The curation is stored: an admin re-ingest of the project applies it.</p>
            </>
          ) : waiting ? (
            <p>
              {unavailable
                ? "Neo4j is not reachable from the platform. The curation is stored and lands when memory returns."
                : `No revision has applied it yet${pending ? ` (${pending} ${pending === 1 ? "ingest" : "ingests"} queued)` : ""}${
                    job.data?.error ? `. Last attempt: ${job.data.error}` : ""
                  }.`}
            </p>
          ) : (
            "Recorded. It joins the memory with the next ingest revision, usually within seconds."
          )}
          {!applied && (failed || waiting) ? (
            <button
              type="button"
              className="mt-2 inline-flex items-center gap-1.5 font-medium text-accent-ink hover:underline disabled:opacity-60"
              onClick={refresh}
              disabled={checking}
            >
              <RotateCw size={13} className={checking ? "animate-spin" : ""} aria-hidden />
              Check again
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** Text fields: Escape with something typed clears focus rather than closing the drawer (and the
 * draft with it). */
const keepDraft = (e: React.KeyboardEvent<HTMLTextAreaElement | HTMLInputElement>) => {
  if (e.key === "Escape" && e.currentTarget.value) {
    e.preventDefault();
    e.currentTarget.blur();
  }
};

function ReplacementPicker({
  projectId,
  value,
  onChange,
}: {
  projectId: string;
  value: { uid: string; name: string } | null;
  onChange(v: { uid: string; name: string } | null): void;
}) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q), 250);
    return () => window.clearTimeout(t);
  }, [q]);
  const hits = useMemorySearch(projectId, debounced, { labels: ["Method", "Class"], side: null, phase: null, asOf: null });
  if (value) {
    return (
      <div className="flex items-center gap-2 rounded-btn border border-accent/40 bg-accent-soft/40 px-3 py-2">
        <LabelChip label="Method" side="new" />
        <span className="min-w-0 flex-1 truncate font-mono text-sm text-ink" title={value.uid}>
          {value.name}
        </span>
        <button type="button" className="text-sm font-medium text-accent-ink hover:underline" onClick={() => onChange(null)}>
          Change
        </button>
      </div>
    );
  }
  return (
    <div>
      <div className="relative">
        <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate" aria-hidden />
        <input
          id="curate-replacement"
          className="input w-full pl-8"
          placeholder="Search methods and classes"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={keepDraft}
          autoComplete="off"
        />
      </div>
      {debounced.trim() ? (
        <ul className="mt-1.5 max-h-44 overflow-y-auto rounded-btn border border-line/80" aria-label="Replacement candidates">
          {hits.isFetching && !hits.data ? (
            <li className="px-3 py-2 text-sm text-slate">Searching…</li>
          ) : (hits.data ?? []).length === 0 ? (
            <li className="px-3 py-2 text-sm text-slate">No method or class matches.</li>
          ) : (
            (hits.data ?? []).slice(0, 8).map((h) => (
              <li key={h.uid}>
                <button
                  type="button"
                  className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-sunken/70"
                  onClick={() => onChange({ uid: h.uid, name: hitTitle(h) })}
                >
                  <LabelChip label={h.label} side="new" />
                  <span className="min-w-0 truncate text-sm text-heading">{hitTitle(h)}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  );
}

export function CurateForm({ projectId, detail, asOf, submissions, onSubmitted, onShowKnowledge, onBackToCurrent }: CurateFormProps) {
  const node = detail.node;
  const mappings: LineageOut[] = useMemo(
    () => detail.lineage.filter((l) => l.direction === "out" && l.edge.type === "MIGRATED_TO" && l.edge.status !== "rejected"),
    [detail.lineage],
  );
  const available = useMemo(() => {
    const a: CurationAction[] = ["note"];
    if (mappings.length) a.push("reject_mapping", "correct_mapping");
    if (node.label === "Memory" && !node.obsolete) a.push("obsolete");
    return a;
  }, [mappings.length, node.label, node.obsolete]);

  const [action, setAction] = useState<CurationAction>("note");
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [mapping, setMapping] = useState<string | null>(null);
  const [replacement, setReplacement] = useState<{ uid: string; name: string } | null>(null);
  const [tried, setTried] = useState(false);
  const curate = useCurate(projectId);
  const status = useMemoryStatus(projectId);

  // Another node: start over (the action may not apply to it).
  useEffect(() => {
    setAction("note");
    setText("");
    setReason("");
    setMapping(null);
    setReplacement(null);
    setTried(false);
    curate.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset on the node only
  }, [node.uid]);
  useEffect(() => {
    if (!available.includes(action)) setAction("note");
  }, [available, action]);

  const latest = [...submissions].reverse().find((s) => s.uid === node.uid) ?? null;
  const chosenMapping = mapping ?? mappings[0]?.edge.uid ?? null;

  if (asOf !== null) {
    return (
      <div className="rounded-btn bg-decision-bg px-4 py-3.5 text-md leading-relaxed text-decision ring-1 ring-inset ring-decision/20">
        You are viewing revision {asOf}. Curation applies to the current memory.
        {onBackToCurrent ? (
          <button type="button" className="ml-1 font-semibold underline" onClick={onBackToCurrent}>
            Curate the current memory
          </button>
        ) : null}
      </div>
    );
  }

  const missing = {
    text: action === "note" && !text.trim(),
    reason: !reason.trim(),
    replacement: action === "correct_mapping" && !replacement,
  };
  const invalid = missing.text || missing.reason || missing.replacement;

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setTried(true);
    if (invalid || curate.isPending) return;
    const mapped = action === "reject_mapping" || action === "correct_mapping";
    curate.mutate(
      {
        action,
        target_uid: mapped && chosenMapping ? chosenMapping : node.uid,
        reason: reason.trim(),
        text: action === "note" ? text.trim() : null,
        replacement_uid: action === "correct_mapping" ? replacement?.uid ?? null : null,
      },
      {
        onSuccess: (out) => {
          onSubmitted({ eventId: out.event_id, jobId: out.job_id, uid: node.uid, action, base: status.data?.revision ?? null, sentAt: Date.now() });
          setText("");
          setReason("");
          setReplacement(null);
          setTried(false);
        },
      },
    );
  };

  const err = curate.error;
  const errText =
    err instanceof ApiError && err.status === 403
      ? new Error("Your role can read the memory but not curate it.")
      : err instanceof ApiError && err.status === 503 && /stored/.test(err.detail)
        ? new Error("Recorded, but memory is not reachable right now: it is applied when memory catches up.")
        : err;

  return (
    <div className="space-y-5">
      {latest ? <SubmissionState projectId={projectId} s={latest} onShowKnowledge={onShowKnowledge} /> : null}

      <form onSubmit={submit} className="space-y-5" noValidate aria-label="Curate this node">
        {available.length > 1 ? (
          <div role="group" aria-label="Action" className="grid grid-cols-2 gap-2">
            {available.map((a) => {
              const A = ACTIONS[a];
              const on = action === a;
              return (
                <button
                  key={a}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setAction(a)}
                  className={`rounded-btn border px-3 py-2.5 text-left transition-all ${
                    on ? "border-accent bg-accent-soft/50 shadow-card" : "border-line/80 hover:border-line-strong hover:bg-sunken/50"
                  }`}
                >
                  <span className={`flex items-center gap-1.5 text-md font-semibold ${on ? "text-accent-ink" : "text-heading"}`}>
                    <A.icon size={14} aria-hidden />
                    {A.title}
                  </span>
                  <span className="mt-0.5 block text-xs leading-snug text-slate">{A.blurb}</span>
                </button>
              );
            })}
          </div>
        ) : (
          <p className="text-md leading-relaxed text-slate">
            Pin context to <span className="font-medium text-heading">{displayName(node)}</span> that agents and reviewers read with it.
          </p>
        )}

        {action === "reject_mapping" || action === "correct_mapping" ? (
          <fieldset>
            <legend className="label mb-2">Mapping</legend>
            <ul className="space-y-1.5">
              {mappings.map((m) => (
                <li key={m.edge.uid}>
                  <button
                    type="button"
                    aria-pressed={chosenMapping === m.edge.uid}
                    onClick={() => setMapping(m.edge.uid)}
                    disabled={mappings.length === 1}
                    className={`flex w-full items-center gap-2 rounded-btn border px-3 py-2 text-left text-sm disabled:cursor-default ${
                      chosenMapping === m.edge.uid ? "border-accent/50 bg-accent-soft/30" : "border-line/80 hover:bg-sunken/50"
                    }`}
                  >
                    <span className="min-w-0 truncate text-heading">{displayName(node)}</span>
                    <ArrowRight size={13} className="shrink-0 text-accent-ink" aria-hidden />
                    <LabelChip label={m.other.label} side={m.other.side} />
                    <span className="min-w-0 truncate font-medium text-heading">{displayName(m.other)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </fieldset>
        ) : null}

        {action === "correct_mapping" ? (
          <div>
            <label htmlFor="curate-replacement" className="label mb-1.5 block">
              Replacement
            </label>
            <ReplacementPicker projectId={projectId} value={replacement} onChange={setReplacement} />
            {tried && missing.replacement ? <p className="mt-1 text-sm text-error">Pick the method or class that implements it.</p> : null}
          </div>
        ) : null}

        {action === "note" ? (
          <div>
            <label htmlFor="curate-note" className="label mb-1.5 block">
              Note
            </label>
            <textarea
              id="curate-note"
              className="input min-h-[96px] w-full resize-y leading-relaxed"
              value={text}
              maxLength={NOTE_MAX}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={keepDraft}
              placeholder="What should anyone working on this know?"
              aria-invalid={tried && missing.text}
            />
            {tried && missing.text ? <p className="mt-1 text-sm text-error">Write the note.</p> : null}
          </div>
        ) : null}

        <div>
          <div className="mb-1.5 flex items-baseline justify-between">
            <label htmlFor="curate-reason" className="label block">
              Reason
            </label>
            <span className="tnum text-xs text-slate/80">
              {reason.length}/{REASON_MAX.toLocaleString("en-US")}
            </span>
          </div>
          <textarea
            id="curate-reason"
            className="input min-h-[64px] w-full resize-y leading-relaxed"
            value={reason}
            maxLength={REASON_MAX}
            required
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={keepDraft}
            placeholder="Why: recorded in the audit trail with your name."
            aria-invalid={tried && missing.reason}
            aria-describedby="curate-reason-hint"
          />
          <p id="curate-reason-hint" className={`mt-1 text-sm ${tried && missing.reason ? "text-error" : "text-slate"}`}>
            {tried && missing.reason ? "A reason is required." : "Every curation needs a reason."}
          </p>
        </div>

        {errText ? <ErrorNote error={errText} /> : null}

        <div className="flex items-center justify-end gap-3 border-t border-line/70 pt-4">
          <button type="submit" className="btn-primary" disabled={curate.isPending}>
            {curate.isPending ? <Loader2 size={15} className="animate-spin" aria-hidden /> : null}
            {ACTIONS[action].submit}
          </button>
        </div>
      </form>
    </div>
  );
}
