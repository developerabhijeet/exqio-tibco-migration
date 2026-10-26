"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Repeat, X } from "lucide-react";
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
 * One iteration, read only: what it brought in (requirements, failures, logs), its design records
 * with their decisions, its builds (a link to each) and the gate decisions while it was open.
 */
function IterationPanel({ iterationId, onClose }: { iterationId: string; onClose: () => void }) {
  const detail = useIteration(iterationId);
  const close = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    close.current?.focus();
  }, []);
  const it = detail.data;
  return (
    <section
      role="dialog"
      aria-modal="false"
      aria-labelledby="iteration-panel-title"
      data-testid="iteration-panel"
      className="card mt-2 space-y-3 p-4"
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0">
          <p className="label">Read only</p>
          <h3 id="iteration-panel-title" className="flex items-center gap-2 text-lg font-semibold text-heading">
            {it ? (
              <>
                Iteration {it.n} <span className="text-sm font-normal text-slate">{KIND[it.kind] ?? it.kind}</span>
                <Pill tone={STATUS_TONE[it.status] ?? "det"}>{it.status}</Pill>
              </>
            ) : (
              "Iteration"
            )}
          </h3>
          {it ? <p className="text-sm text-slate">{describe(it)}</p> : null}
        </div>
        <button ref={close} type="button" className="btn-secondary ml-auto" onClick={onClose} aria-label="Close the iteration">
          <X size={14} aria-hidden />
        </button>
      </div>
      {detail.error ? <ErrorNote error={detail.error} /> : null}
      {it?.requirements ? (
        <div>
          <p className="label">Requirements</p>
          <p className="whitespace-pre-wrap text-md text-ink">{it.requirements}</p>
        </div>
      ) : null}
      {it && it.inputs.length > 0 ? (
        <div>
          <p className="label">Inputs</p>
          <ul className="mt-1 space-y-1.5" data-testid="iteration-panel-inputs">
            {it.inputs.map((i) => (
              <InputRow key={i.id} iterationId={it.id} input={i} />
            ))}
          </ul>
        </div>
      ) : null}
      {it ? (
        <div>
          <p className="label">What happened</p>
          {it.timeline.length === 0 ? (
            <p className="text-sm text-slate">No design, build or gate decision in this iteration.</p>
          ) : (
            <ol className="mt-1 space-y-1 text-sm" data-testid="iteration-panel-timeline">
              {it.timeline.map((t) => (
                <li key={`${t.kind}-${t.id}`} data-kind={t.kind}>
                  <span className="text-slate">{t.at ? new Date(t.at).toLocaleString() : ""}</span>{" "}
                  {t.kind === "build" && t.process_id ? (
                    <a className="text-accent-ink underline" href={`/?step=build&process=${t.process_id}`} data-testid="iteration-build-link">
                      {t.summary}
                    </a>
                  ) : (
                    <span className="text-ink">{t.summary}</span>
                  )}
                  {t.decisions && t.decisions.length > 0 ? (
                    <ul className="ml-4 list-disc text-sm text-slate" data-testid="iteration-decisions">
                      {t.decisions.map((d) => (
                        <li key={d}>{d}</li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </div>
      ) : null}
    </section>
  );
}

/**
 * The project's iterations under the stepper: each pass through the pipeline, its kind and where
 * it stands. Iteration 1 is the first pass; every re-entry, edit set or repository fetch adds one.
 * Each is a button that opens it read only (IterationPanel).
 */
export function IterationStrip() {
  const { projectId } = useAppState();
  const iterations = useIterations(projectId);
  const [opened, setOpened] = useState<string | null>(null);
  if (!projectId || !iterations.data || iterations.data.length === 0) return null;
  const rows = [...iterations.data].sort((a, b) => a.n - b.n);
  const current = [...rows].reverse().find(isActive) ?? rows[rows.length - 1];
  return (
    <div data-testid="iteration-strip" className="mt-2 border-t border-line px-2 pt-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="label flex items-center gap-1">
          <Repeat size={13} aria-hidden /> Iterations
        </span>
        <ol className="flex flex-wrap items-center gap-1.5" aria-label="Iterations of this project">
          {rows.map((it) => (
            <li key={it.id} data-iteration={it.n} data-status={it.status}>
              <button
                type="button"
                data-testid={`iteration-${it.n}`}
                aria-current={it.id === current.id ? "true" : undefined}
                aria-expanded={opened === it.id}
                aria-label={describe(it)}
                title={describe(it)}
                onClick={() => setOpened(opened === it.id ? null : it.id)}
                className={`flex items-center gap-1.5 rounded-full border px-2 py-0.5 ${
                  it.id === current.id ? "border-accent bg-accent-soft text-heading" : "border-line text-slate"
                } hover:bg-sunken`}
              >
                <span className="font-semibold">Iteration {it.n}</span>
                <span className="text-xs">{KIND[it.kind] ?? it.kind}</span>
                <Pill tone={STATUS_TONE[it.status] ?? "det"}>{it.status}</Pill>
              </button>
            </li>
          ))}
        </ol>
      </div>
      {opened ? <IterationPanel iterationId={opened} onClose={() => setOpened(null)} /> : null}
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
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-sunken"
      >
        {open ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
        <Pill tone="det">{input.kind.replace(/_/g, " ")}</Pill>
        <span className="min-w-0 truncate font-medium text-heading">{input.title}</span>
      </button>
      {open ? (
        <div className="border-t border-line px-3 py-2">
          {error ? <ErrorNote error={error} /> : null}
          {/* Text only: a log is never rendered as HTML. */}
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap font-mono text-xs text-ink">
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
      <h3 id="iteration-inputs-title" className="flex items-center gap-2 text-xl font-semibold text-heading">
        Iteration {it.n} inputs <Pill tone={STATUS_TONE[it.status] ?? "det"}>{it.status}</Pill>
      </h3>
      {it.requirements ? (
        <div className="mt-3">
          <p className="label">Requirements</p>
          <p className="whitespace-pre-wrap text-md text-ink" data-testid="iteration-requirements">
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
        <p className="mt-2 text-md text-slate">No failures, logs or requirements were brought in.</p>
      ) : null}
    </section>
  );
}
