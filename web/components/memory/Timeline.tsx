"use client";

// The revision timeline under the canvas: one tick per revision, oldest on the left, coloured by
// the phase that wrote it and as tall as the change it made. Picking a tick reads the whole
// explorer as of that revision (the latest tick, or "Back to current", returns to the live view);
// "Show changes" opens what the active revision added, changed and removed.
import { useEffect, useMemo, useRef, useState } from "react";
import { GitCompareArrows, History, RotateCcw } from "lucide-react";
import { phaseHex, relativeTime, type RevisionOut } from "@/lib/memory";

export interface TimelineProps {
  /** Any order; drawn oldest first. */
  revisions: RevisionOut[];
  /** The revision the explorer reads as of, or null for the current memory. */
  value: number | null;
  onChange(n: number | null): void;
  onShowChanges(n: number): void;
  /** The revision whose changes are open, if any (its tick is marked). */
  showing?: number | null;
}

const fmt = (n: number) => n.toLocaleString("en-US");

/** Tick height in px: a log scale, so a 2 000-node scan does not flatten a one-line curation. */
function tickHeight(r: RevisionOut, max: number): number {
  const size = r.added + r.changed + r.removed;
  if (max <= 0) return 14;
  return Math.round(8 + 28 * (Math.log1p(size) / Math.log1p(max)));
}

export function Timeline({ revisions, value, onChange, onShowChanges, showing }: TimelineProps) {
  const ordered = useMemo(() => [...revisions].sort((a, b) => a.n - b.n), [revisions]);
  const latest = ordered.at(-1)?.n ?? null;
  const active = value ?? latest;
  const activeRev = ordered.find((r) => r.n === active) ?? null;
  const max = useMemo(() => Math.max(0, ...ordered.map((r) => r.added + r.changed + r.removed)), [ordered]);
  const phases = useMemo(() => [...new Set(ordered.map((r) => r.phase))], [ordered]);
  const track = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<RevisionOut | null>(null);

  // Keep the active tick in view (a long history scrolls sideways), newest end by default.
  useEffect(() => {
    const el = track.current?.querySelector<HTMLElement>(`[data-n="${active}"]`);
    el?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [active, ordered.length]);

  const pick = (n: number) => onChange(n === latest ? null : n);
  const onKey = (e: React.KeyboardEvent) => {
    if (active === null || ordered.length === 0) return;
    const i = ordered.findIndex((r) => r.n === active);
    let next: number | null = null;
    if (e.key === "ArrowLeft") next = ordered[Math.max(0, i - 1)].n;
    else if (e.key === "ArrowRight") next = ordered[Math.min(ordered.length - 1, i + 1)].n;
    else if (e.key === "Home") next = ordered[0].n;
    else if (e.key === "End") next = ordered[ordered.length - 1].n;
    if (next === null) return;
    e.preventDefault();
    pick(next);
    window.requestAnimationFrame(() => track.current?.querySelector<HTMLElement>(`[data-n="${next}"]`)?.focus());
  };

  const shown = hover ?? activeRev;
  // Few enough to number each tick under it.
  const numbered = ordered.length <= 40;
  const asOf = value !== null;

  return (
    <section
      data-testid="memory-timeline"
      aria-label="Revision timeline"
      className={`card flex h-[120px] shrink-0 flex-col px-5 pb-2.5 pt-3 transition-shadow ${asOf ? "ring-2 ring-inset ring-decision/40" : ""}`}
    >
      <div className="flex min-h-[30px] items-center gap-3">
        <History size={15} className="shrink-0 text-slate" aria-hidden />
        {asOf ? (
          <div className="flex items-center gap-2.5 rounded-full bg-decision-bg py-0.5 pl-3 pr-1 text-[13px] font-medium text-decision ring-1 ring-inset ring-decision/20" role="status">
            <span>
              Viewing revision {value} of {latest}
            </span>
            <button
              type="button"
              onClick={() => onChange(null)}
              className="inline-flex items-center gap-1 rounded-full bg-white px-2.5 py-0.5 text-[12.5px] font-semibold text-plum shadow-card transition-colors hover:bg-warm"
            >
              <RotateCcw size={12} aria-hidden />
              Back to current
            </button>
          </div>
        ) : (
          <p className="text-[13px] font-medium text-slate">
            <span className="text-plum">Current</span>
            {latest !== null ? ` · ${fmt(ordered.length)} ${ordered.length === 1 ? "revision" : "revisions"}` : ""}
          </p>
        )}

        {/* What the hovered (else the active) revision did. */}
        <div className="ml-auto flex min-w-0 items-center gap-3 text-[12.5px] text-slate" aria-live="polite">
          {shown ? (
            <>
              <span className="inline-flex items-center gap-1.5 font-semibold text-plum">
                <span className="h-2 w-2 rounded-full" style={{ background: phaseHex(shown.phase) }} aria-hidden />
                Revision {shown.n} · {shown.phase}
              </span>
              <span className="hidden truncate xl:inline">
                {[shown.actor, relativeTime(shown.at)].filter(Boolean).join(" · ")}
              </span>
              <span className="tnum whitespace-nowrap">
                <span className="text-gate">+{fmt(shown.added)}</span>{" "}
                <span className="text-decision">~{fmt(shown.changed)}</span>{" "}
                <span className="text-error">−{fmt(shown.removed)}</span>
              </span>
            </>
          ) : null}
          {active !== null ? (
            <button
              type="button"
              className={`btn-secondary shrink-0 py-1 text-[13px] ${showing === active ? "bg-purple-lt text-purple-dk" : ""}`}
              onClick={() => onShowChanges(active)}
              aria-pressed={showing === active}
            >
              <GitCompareArrows size={14} aria-hidden />
              Show changes
            </button>
          ) : null}
        </div>
      </div>

      <div className="mt-1.5 flex min-h-0 flex-1 items-end gap-4">
        <div
          ref={track}
          role="group"
          aria-label="Revisions, oldest first"
          onKeyDown={onKey}
          onMouseLeave={() => setHover(null)}
          className={`flex h-full min-w-0 flex-1 items-end gap-[3px] overflow-x-auto overflow-y-hidden [scrollbar-width:thin]`}
        >
          {ordered.length === 0 ? <p className="self-center text-[13px] text-slate">No revisions yet.</p> : null}
          {ordered.map((r) => {
            const on = r.n === active;
            const colour = phaseHex(r.phase);
            return (
              <button
                key={r.n}
                type="button"
                data-n={r.n}
                data-testid={`rev-${r.n}`}
                aria-label={`Revision ${r.n}, ${r.phase}`}
                aria-current={on ? "true" : undefined}
                tabIndex={on ? 0 : -1}
                onClick={() => pick(r.n)}
                onMouseEnter={() => setHover(r)}
                onFocus={() => setHover(r)}
                onBlur={() => setHover(null)}
                className="group relative flex h-full min-w-[9px] max-w-[30px] flex-1 flex-col items-center justify-end rounded-[3px] outline-none focus-visible:ring-2 focus-visible:ring-purple"
              >
                {on ? <span className="mb-1 h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: colour }} aria-hidden /> : null}
                <span
                  className={`block w-full shrink-0 rounded-t-[3px] transition-all duration-150 ${on ? "opacity-100" : "opacity-45 group-hover:opacity-80"} ${
                    showing === r.n ? "ring-2 ring-purple ring-offset-1" : ""
                  }`}
                  style={{ height: tickHeight(r, max), background: colour }}
                  aria-hidden
                />
                {numbered ? (
                  <span
                    className={`tnum mt-0.5 h-[14px] border-t border-line pt-px text-center text-[10.5px] leading-[13px] w-full ${on ? "font-semibold text-plum" : "text-slate/80"}`}
                    aria-hidden
                  >
                    {r.n}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
        <ul className="hidden shrink-0 grid-cols-2 gap-x-4 gap-y-0.5 self-end pb-4 text-[11.5px] leading-4 text-slate lg:grid" aria-label="Phases">
          {phases.slice(0, 6).map((p) => (
            <li key={p} className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-sm" style={{ background: phaseHex(p) }} aria-hidden />
              {p}
            </li>
          ))}
          {phases.length > 6 ? <li className="text-slate/80">+{phases.length - 6} more</li> : null}
        </ul>
      </div>
    </section>
  );
}
