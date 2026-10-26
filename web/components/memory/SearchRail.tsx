"use client";

// The explorer's left rail: the search box (debounced 250 ms), the label, side and phase
// filters, and the results. Labels and side filter the canvas as well as the search.
import { useEffect, useRef, useState } from "react";
import { ChevronDown, Loader2, Search, SlidersHorizontal, X } from "lucide-react";
import { ErrorNote } from "@/components/ui/Empty";
import {
  GROUP_HEX,
  GROUP_LABEL,
  LABEL_GROUPS,
  PHASES,
  hitTitle,
  sideOfLabel,
  type MemoryHit,
  type Side,
} from "@/lib/memory";

export interface SearchRailProps {
  q: string;
  onQ(q: string): void;
  labels: string[];
  onLabels(labels: string[]): void;
  side: Side | null;
  onSide(side: Side | null): void;
  phase: string | null;
  onPhase(phase: string | null): void;
  counts: Record<string, number>;
  hits: MemoryHit[] | undefined;
  searching: boolean;
  error: unknown;
  selected: string | null;
  onPick(hit: MemoryHit): void;
}

const SIDES: { value: Side | null; label: string }[] = [
  { value: null, label: "All" },
  { value: "old", label: "Old" },
  { value: "new", label: "New" },
];

export function LabelChip({ label, side }: { label: string; side: Side }) {
  const c = GROUP_HEX[side];
  return (
    <span
      className="inline-flex shrink-0 items-center rounded-full px-2 py-px text-[11px] font-semibold tracking-[0.01em]"
      style={{ background: c.fill, color: c.strong, boxShadow: `inset 0 0 0 1px ${c.border}33` }}
    >
      {label}
    </span>
  );
}

function LabelGroup({
  group,
  labels,
  onLabels,
  counts,
}: {
  group: Side;
  labels: string[];
  onLabels(labels: string[]): void;
  counts: Record<string, number>;
}) {
  const members: readonly string[] = LABEL_GROUPS[group];
  const on = members.filter((l) => labels.includes(l));
  const [open, setOpen] = useState(on.length > 0);
  const all = on.length === members.length;
  const box = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (box.current) box.current.indeterminate = on.length > 0 && !all;
  }, [on.length, all]);
  const toggleAll = () => onLabels(all ? labels.filter((l) => !members.includes(l)) : [...new Set([...labels, ...members])]);
  const toggle = (l: string) => onLabels(labels.includes(l) ? labels.filter((x) => x !== l) : [...labels, l]);
  return (
    <div className="rounded-btn border border-line/80 bg-white">
      <div className="flex items-center gap-2.5 px-3 py-2">
        <input
          ref={box}
          type="checkbox"
          className="h-4 w-4 accent-purple"
          checked={all}
          onChange={toggleAll}
          aria-label={`All ${GROUP_LABEL[group]} labels`}
        />
        <span className="h-2.5 w-2.5 rounded-full" style={{ background: GROUP_HEX[group].border }} aria-hidden />
        <button type="button" className="flex flex-1 items-center justify-between text-left text-[14px] font-medium text-plum" aria-expanded={open} onClick={() => setOpen(!open)}>
          <span>
            {GROUP_LABEL[group]}
            {on.length > 0 && !all ? <span className="ml-1.5 text-[12.5px] font-normal text-slate">{on.length} of {members.length}</span> : null}
          </span>
          <ChevronDown size={16} className={`text-slate transition-transform ${open ? "rotate-180" : ""}`} aria-hidden />
        </button>
      </div>
      {open ? (
        <div className="grid grid-cols-2 gap-x-2 gap-y-1 border-t border-line/70 px-3 py-2.5">
          {members.map((l) => (
            <label key={l} className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-[13px] text-ink hover:bg-warm/70">
              <input type="checkbox" className="h-3.5 w-3.5 accent-purple" checked={labels.includes(l)} onChange={() => toggle(l)} />
              <span className="min-w-0 flex-1 truncate">{l}</span>
              {counts[l] ? <span className="tnum text-[11.5px] text-slate/80">{counts[l]}</span> : null}
            </label>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ScoreBar({ score }: { score: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
  return (
    <span className="flex items-center gap-2" title={`Score ${score.toFixed(2)}`}>
      <span className="h-1 w-16 overflow-hidden rounded-full bg-line/70">
        <span className="block h-full rounded-full bg-purple/70" style={{ width: `${pct}%` }} />
      </span>
      <span className="tnum text-[11px] text-slate">{score.toFixed(2)}</span>
    </span>
  );
}

export function SearchRail(props: SearchRailProps) {
  const { q, onQ, labels, onLabels, side, onSide, phase, onPhase, counts, hits, searching, error, selected, onPick } = props;
  const [text, setText] = useState(q);
  const [filtersOpen, setFiltersOpen] = useState(labels.length > 0 || side !== null || phase !== null);
  const onQRef = useRef(onQ);
  onQRef.current = onQ;

  // Keep the box in step when the URL changes underneath it (back, a shared link).
  useEffect(() => setText(q), [q]);
  useEffect(() => {
    if (text === q) return;
    const t = window.setTimeout(() => onQRef.current(text), 250);
    return () => window.clearTimeout(t);
  }, [text, q]);

  const active = labels.length + (side ? 1 : 0) + (phase ? 1 : 0);

  return (
    <aside className="card flex min-h-0 w-[300px] shrink-0 flex-col overflow-hidden" aria-label="Search memory">
      <div className="space-y-3 border-b border-line/80 p-5">
        <label className="relative block">
          <span className="sr-only">Search memory</span>
          <Search size={17} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate/70" aria-hidden />
          <input
            data-testid="memory-search"
            type="search"
            className="input py-2.5 pl-10 pr-9"
            placeholder="Search memory…"
            value={text}
            maxLength={500}
            onChange={(e) => setText(e.target.value)}
          />
          {searching ? <Loader2 size={15} className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-slate" aria-hidden /> : null}
        </label>
        <div className="flex items-center justify-between">
          <button
            type="button"
            className="inline-flex items-center gap-1.5 text-[13.5px] font-medium text-slate hover:text-plum"
            aria-expanded={filtersOpen}
            onClick={() => setFiltersOpen(!filtersOpen)}
          >
            <SlidersHorizontal size={15} aria-hidden />
            Filters
            {active > 0 ? <span className="rounded-full bg-purple-lt px-1.5 text-[11.5px] font-semibold text-purple-dk">{active}</span> : null}
            <ChevronDown size={15} className={`transition-transform ${filtersOpen ? "rotate-180" : ""}`} aria-hidden />
          </button>
          {active > 0 ? (
            <button
              type="button"
              className="inline-flex items-center gap-1 text-[12.5px] font-medium text-purple hover:underline"
              onClick={() => {
                onLabels([]);
                onSide(null);
                onPhase(null);
              }}
            >
              <X size={13} aria-hidden /> Clear
            </button>
          ) : null}
        </div>
        {filtersOpen ? (
          <div className="space-y-4 pt-1">
            <div>
              <p className="label mb-2">Side</p>
              <div className="grid grid-cols-3 rounded-btn bg-warm p-1" role="radiogroup" aria-label="Side">
                {SIDES.map((s) => (
                  <button
                    key={s.label}
                    type="button"
                    role="radio"
                    aria-checked={side === s.value}
                    onClick={() => onSide(s.value)}
                    className={`rounded-[5px] py-1.5 text-[13px] font-medium transition-all ${
                      side === s.value ? "bg-white text-plum shadow-card" : "text-slate hover:text-plum"
                    }`}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <p className="label mb-2">Labels</p>
              <div className="space-y-2">
                {(["old", "new", "knowledge"] as const).map((g) => (
                  <LabelGroup key={g} group={g} labels={labels} onLabels={onLabels} counts={counts} />
                ))}
              </div>
            </div>
            <label className="block">
              <span className="label mb-2 block">Phase</span>
              <select className="select w-full" value={phase ?? ""} onChange={(e) => onPhase(e.target.value || null)}>
                <option value="">Any phase</option>
                {PHASES.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
          </div>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto" data-testid="memory-results">
        {error ? (
          <div className="p-4">
            <ErrorNote error={error} />
          </div>
        ) : !q.trim() ? (
          <p className="px-5 py-6 text-[14px] leading-relaxed text-slate">
            Search the old code, the new code and what the team learnt along the way. Pick a result to explore around it.
          </p>
        ) : hits && hits.length === 0 && !searching ? (
          <p className="px-5 py-6 text-[14px] text-slate">No matches for “{q}”.</p>
        ) : (
          <ul className="divide-y divide-line/70">
            {(hits ?? []).map((h) => {
              const s = sideOfLabel(h.label, h.props);
              const isSel = h.uid === selected;
              return (
                <li key={h.uid}>
                  <button
                    type="button"
                    onClick={() => onPick(h)}
                    aria-current={isSel ? "true" : undefined}
                    className={`block w-full px-5 py-3.5 text-left transition-colors ${isSel ? "bg-purple-lt/60" : "hover:bg-warm/70"}`}
                  >
                    <span className="flex items-center gap-2">
                      <LabelChip label={h.label} side={s} />
                      <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-plum">{hitTitle(h)}</span>
                    </span>
                    {h.text ? <span className="mt-1 line-clamp-2 text-[13px] leading-snug text-slate">{h.text}</span> : null}
                    <span className="mt-2 flex items-center justify-between">
                      <ScoreBar score={h.score} />
                      <span className="text-[11.5px] text-slate/80">rev {h.citation.revision}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </aside>
  );
}
