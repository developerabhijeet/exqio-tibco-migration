"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Repeat } from "lucide-react";
import { api } from "@/lib/api";
import { isActive, iterationOf, useIteration, useIterations } from "@/lib/iterations";
import { useAppState } from "@/lib/state";
import type { IterationInputView, IterationView } from "@/lib/types";
import { Pill } from "@/components/ui/Pill";
import { ErrorNote } from "@/components/ui/Empty";

const KIND: Record<string, string> = {
  initial: "first pass",
  reentry: "re-entered design",
  edits: "hand edits",
  repo: "from a repository",
};

const STATUS_TONE: Record<string, "purple" | "gate" | "det" | "error" | "decision"> = {
  open: "decision",
  designing: "purple",
  building: "purple",
  verifying: "purple",
  deploying: "purple",
  closed: "gate",
  abandoned: "det",
};

function describe(it: IterationView): string {
  const when = new Date(it.opened_at).toLocaleString();
  const end = it.closed_at ? `, ended ${new Date(it.closed_at).toLocaleString()}` : "";
  return `Iteration ${it.n}: ${KIND[it.kind] ?? it.kind}, opened by ${it.opened_by} ${when}${end}${it.note ? `. ${it.note}` : ""}`;
}

/**
 * The project's iterations under the stepper: each pass through the pipeline, its kind and where
 * it stands. Iteration 1 is the first pass; every re-entry, edit set or repository fetch adds one.
 */
export function IterationStrip() {
  const { projectId } = useAppState();
  const iterations = useIterations(projectId);
  if (!projectId || !iterations.data || iterations.data.length === 0) return null;
  const rows = [...iterations.data].sort((a, b) => a.n - b.n);
  const current = [...rows].reverse().find(isActive) ?? rows[rows.length - 1];
  return (
    <div
      data-testid="iteration-strip"
      className="mt-2 flex flex-wrap items-center gap-2 border-t border-line px-2 pt-2 text-[12.5px]"
    >
      <span className="label flex items-center gap-1">
        <Repeat size={13} aria-hidden /> Iterations
      </span>
      <ol className="flex flex-wrap items-center gap-1.5" aria-label="Iterations of this project">
        {rows.map((it) => (
          <li
            key={it.id}
            data-iteration={it.n}
            data-status={it.status}
            aria-current={it.id === current.id ? "true" : undefined}
            title={describe(it)}
            className={`flex items-center gap-1.5 rounded-full border px-2 py-0.5 ${
              it.id === current.id ? "border-purple bg-purple-lt text-plum" : "border-line text-slate"
            }`}
          >
            <span className="font-semibold">Iteration {it.n}</span>
            <span className="text-[11.5px]">{KIND[it.kind] ?? it.kind}</span>
            <Pill tone={STATUS_TONE[it.status] ?? "det"}>{it.status}</Pill>
          </li>
        ))}
      </ol>
    </div>
  );
}

function InputRow({ iterationId, input }: { iterationId: string; input: IterationInputView }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && text === null) {
      try {
        setText((await api.iterationInput(iterationId, input.id)).text);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    }
  };
  return (
    <li className="rounded-btn border border-line">
      <button
        type="button"
        aria-expanded={open}
        onClick={toggle}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] hover:bg-warm"
      >
        {open ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
        <Pill tone="det">{input.kind.replace(/_/g, " ")}</Pill>
        <span className="min-w-0 truncate font-medium text-plum">{input.title}</span>
      </button>
      {open ? (
        <div className="border-t border-line px-3 py-2">
          {error ? <ErrorNote error={error} /> : null}
          {/* Text only: a log is never rendered as HTML. */}
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap font-mono text-[12px] text-ink">
            {text ?? "Loading"}
          </pre>
        </div>
      ) : null}
    </li>
  );
}

/** Design tab: what the process's current iteration brought in (failures, logs, requirements). */
export function IterationInputs({ processId }: { processId: string }) {
  const { projectId } = useAppState();
  const iterations = useIterations(projectId);
  const it = iterationOf(iterations.data, processId);
  const detail = useIteration(it?.id ?? null);
  if (!it) return null;
  const inputs = (detail.data?.inputs ?? []).filter((i) => !i.process_id || i.process_id === processId);
  return (
    <section className="card p-5" data-testid="iteration-inputs" aria-labelledby="iteration-inputs-title">
      <p className="label">What the designer reads for this iteration</p>
      <h3 id="iteration-inputs-title" className="flex items-center gap-2 text-[18px] font-semibold text-plum">
        Iteration {it.n} inputs <Pill tone={STATUS_TONE[it.status] ?? "det"}>{it.status}</Pill>
      </h3>
      {it.requirements ? (
        <div className="mt-3">
          <p className="label">Requirements</p>
          <p className="whitespace-pre-wrap text-[14px] text-ink" data-testid="iteration-requirements">
            {it.requirements}
          </p>
        </div>
      ) : null}
      {detail.error ? <ErrorNote error={detail.error} /> : null}
      {inputs.length > 0 ? (
        <ul className="mt-3 space-y-1.5">
          {inputs.map((i) => (
            <InputRow key={i.id} iterationId={it.id} input={i} />
          ))}
        </ul>
      ) : detail.data && !it.requirements ? (
        <p className="mt-2 text-[13.5px] text-slate">No failures, logs or requirements were brought in.</p>
      ) : null}
    </section>
  );
}
