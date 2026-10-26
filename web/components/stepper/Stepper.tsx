"use client";

import {
  AlertTriangle,
  Check,
  CheckCheck,
  CheckCircle2,
  ChevronDown,
  Eye,
  Hammer,
  Loader2,
  PencilRuler,
  Rocket,
  Search,
  ShieldCheck,
  Upload,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { StatusPill } from "@/components/ui/Pill";
import { blockedReason, canActOn, useActingPersona } from "@/lib/personas";
import { useAppState, type StepKey } from "@/lib/state";
import type { StepState } from "@/lib/types";

type Status = StepState["status"];

const STATUS_LABEL: Record<Status, string> = {
  not_started: "not started",
  running: "running",
  needs_review: "needs review",
  complete: "complete",
  approved: "approved",
  failed: "failed",
};

/** The 8 px dot is the colour cue; the glyph beside the label and the text carry the same status. */
const DOT: Record<Status, string> = {
  not_started: "bg-slate",
  running: "bg-accent",
  needs_review: "bg-decision",
  complete: "bg-det",
  approved: "bg-gate",
  failed: "bg-error",
};

const FINISHED: Status[] = ["approved", "complete"];

/** One icon per step, so a glance at the bar says what the step is, not only where it sits. */
const STEP_ICON: Record<string, LucideIcon> = {
  upload: Upload,
  discover: Search,
  validate: CheckCircle2,
  design: PencilRuler,
  build: Hammer,
  verify: ShieldCheck,
  deploy: Rocket,
};

function Glyph({ status }: { status: Status }) {
  const common = { size: 13, "aria-hidden": true, className: "shrink-0" } as const;
  if (status === "approved") return <CheckCheck {...common} strokeWidth={3} />;
  if (status === "complete") return <Check {...common} strokeWidth={3} />;
  if (status === "failed") return <X {...common} strokeWidth={3} />;
  if (status === "needs_review") return <AlertTriangle {...common} />;
  if (status === "running") return <Loader2 {...common} className="shrink-0 motion-safe:animate-spin" />;
  return null;
}

export function Stepper({
  steps,
  active,
  onSelect,
  compact = false,
  trailing,
}: {
  steps: StepState[];
  active: number;
  onSelect: (index: number) => void;
  /** The pinned header has scrolled: tighter padding, same height budget (the caller reserves space). */
  compact?: boolean;
  /** Slot after the last step for the iteration indicator (a later sub-project fills it). */
  trailing?: ReactNode;
}) {
  // Which steps the current persona may act in. A step that is not theirs stays open to read, so it
  // is never disabled; it carries an eye and a "view only" marker instead, and the reason is the
  // tooltip. That way the bar says what this user can do before they select anything.
  const { auth } = useAppState();
  const persona = useActingPersona();
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const lastFinished = steps.reduce((acc, s, i) => (FINISHED.includes(s.status) ? i : acc), -1);

  const onKeyDown = (e: KeyboardEvent<HTMLElement>, i: number) => {
    const last = steps.length - 1;
    const to =
      e.key === "ArrowRight" ? Math.min(i + 1, last) : e.key === "ArrowLeft" ? Math.max(i - 1, 0)
      : e.key === "Home" ? 0 : e.key === "End" ? last : null;
    if (to === null) return;
    e.preventDefault();
    tabs.current[to]?.focus();
  };

  return (
    <div className="flex items-stretch gap-3">
      <nav aria-label="Migration steps" className="hidden min-w-0 flex-1 lg:block">
      <ol className={`card flex min-w-0 items-stretch shadow-card ${compact ? "p-1" : "p-2"}`}>
        {steps.map((s, i) => {
          const isActive = i === active;
          const mine = canActOn(persona, s.key as StepKey);
          const reason = blockedReason(persona, s.key as StepKey, auth.mode === "oidc") ?? undefined;
          const Icon = STEP_ICON[s.key];
          const title = `${s.label}: ${STATUS_LABEL[s.status]}${reason ? `. ${reason}` : ""}`;
          return (
            // The open step takes its full width (label and status pill) when it fits and shrinks,
            // its label truncating, when it does not; the others share the rest and never go below
            // their dot and icon (min-w-10).
            <li key={s.key} className={`flex items-center ${isActive ? "min-w-0 flex-[0_1_auto]" : "min-w-10 flex-1"}`}>
              <button
                ref={(el) => {
                  tabs.current[i] = el;
                }}
                type="button"
                aria-current={isActive ? "step" : undefined}
                data-testid={`step-${s.key}`}
                data-status={s.status}
                data-view-only={mine ? undefined : "true"}
                title={title}
                onClick={() => onSelect(i)}
                onKeyDown={(e) => onKeyDown(e, i)}
                className={`flex min-w-0 flex-1 items-center gap-1.5 rounded-[10px] px-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${
                  compact ? "py-1" : "py-2"
                } ${isActive ? "bg-accent-soft" : "hover:bg-sunken"} ${mine ? "" : "opacity-80"}`}
              >
                <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${DOT[s.status]}`} />
                {Icon ? <Icon size={15} aria-hidden className="shrink-0 text-slate" /> : null}
                <span
                  className={`min-w-0 truncate text-step font-semibold ${isActive ? "text-accent-ink" : "text-heading"}`}
                >
                  {s.label}
                </span>
                {mine ? null : <Eye size={13} aria-hidden className="shrink-0 text-slate" />}
                <Glyph status={s.status} />
                {isActive ? (
                  <span className="ml-auto shrink-0" data-testid="step-status-pill">
                    <StatusPill status={s.status} />
                  </span>
                ) : (
                  <span className="sr-only">{STATUS_LABEL[s.status]}</span>
                )}
                {mine ? null : <span className="sr-only">view only</span>}
              </button>
              {i < steps.length - 1 ? (
                <span
                  aria-hidden
                  data-filled={i + 1 <= lastFinished ? "true" : "false"}
                  className={`mx-0.5 h-0.5 w-3 shrink-0 rounded-full ${i + 1 <= lastFinished ? "bg-gate" : "bg-line"}`}
                />
              ) : null}
            </li>
          );
        })}
      </ol>
      </nav>
      <CompactStepper steps={steps} active={active} onSelect={onSelect} compact={compact} />
      {trailing ? <div className="flex shrink-0 items-center" data-testid="stepper-trailing">{trailing}</div> : null}
    </div>
  );
}

/** Below 1024 px: one button that says where you are, opening a list of all seven steps. */
function CompactStepper({
  steps,
  active,
  onSelect,
  compact,
}: {
  steps: StepState[];
  active: number;
  onSelect: (index: number) => void;
  compact: boolean;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  const options = useRef<(HTMLButtonElement | null)[]>([]);
  const cur = steps[active];

  useEffect(() => {
    if (!open) return;
    options.current[active]?.focus();
    const away = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open, active]);

  const onKeyDown = (e: KeyboardEvent<HTMLElement>, i: number) => {
    const last = steps.length - 1;
    const to =
      e.key === "ArrowDown" ? Math.min(i + 1, last) : e.key === "ArrowUp" ? Math.max(i - 1, 0)
      : e.key === "Home" ? 0 : e.key === "End" ? last : null;
    if (to === null) return;
    e.preventDefault();
    options.current[to]?.focus();
  };
  // Escape anywhere inside closes it and returns focus to the button; so does focus leaving.
  const onRootKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key !== "Escape" || !open) return;
    e.preventDefault();
    setOpen(false);
    button.current?.focus();
  };

  if (!cur) return null;
  return (
    <div
      ref={root}
      className="relative min-w-0 flex-1 lg:hidden"
      onKeyDown={onRootKeyDown}
      onBlur={(e) => {
        if (open && !e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <button
        ref={button}
        type="button"
        data-testid="stepper-compact"
        data-status={cur.status}
        aria-expanded={open}
        aria-controls={open ? "stepper-compact-list" : undefined}
        onClick={() => setOpen((o) => !o)}
        className={`card flex w-full items-center gap-2 px-3 text-left shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${
          compact ? "py-1.5" : "py-2.5"
        }`}
      >
        <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${DOT[cur.status]}`} />
        <span className="min-w-0 flex-1 truncate text-step font-semibold text-heading">
          Step {active + 1} of {steps.length} · {cur.label}
        </span>
        <StatusPill status={cur.status} />
        <ChevronDown size={16} aria-hidden className={`shrink-0 text-slate ${open ? "rotate-180" : ""}`} />
      </button>
      {open ? (
        <ul
          id="stepper-compact-list"
          className="card absolute left-0 right-0 top-full z-30 mt-1 space-y-0.5 p-1 shadow-card"
        >
          {steps.map((s, i) => {
            const Icon = STEP_ICON[s.key];
            return (
              <li key={s.key}>
                <button
                  ref={(el) => {
                    options.current[i] = el;
                  }}
                  type="button"
                  aria-current={i === active ? "step" : undefined}
                  data-testid={`step-option-${s.key}`}
                  data-status={s.status}
                  onKeyDown={(e) => onKeyDown(e, i)}
                  onClick={() => {
                    onSelect(i);
                    setOpen(false);
                    button.current?.focus();
                  }}
                  className={`flex w-full items-center gap-2 rounded-[8px] px-2.5 py-2 text-left text-step focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${
                    i === active ? "bg-accent-soft font-semibold text-accent-ink" : "text-heading hover:bg-sunken"
                  }`}
                >
                  <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${DOT[s.status]}`} />
                  {Icon ? <Icon size={15} aria-hidden className="shrink-0 text-slate" /> : null}
                  <span className="min-w-0 flex-1 truncate">{s.label}</span>
                  <Glyph status={s.status} />
                  <span className="text-xs text-slate">{STATUS_LABEL[s.status]}</span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
