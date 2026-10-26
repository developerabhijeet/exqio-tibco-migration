"use client";

// What one revision changed, over the canvas's right edge: the nodes and relationships it added,
// changed and removed. Every item opens in the drawer; the explorer outlines the same items on the
// canvas (removed nodes drawn as ghosts, read as of the revision before).
import { useState } from "react";
import { ArrowRight, ChevronDown, Loader2, X } from "lucide-react";
import { ErrorNote } from "@/components/ui/Empty";
import { LabelChip } from "./SearchRail";
import {
  displayName,
  parseRelUid,
  phaseCss,
  relativeTime,
  sideOfLabel,
  type ChangedRef,
  type DiffSet,
  type RevisionDiff,
  type VersionRef,
} from "@/lib/memory";

export interface DiffPanelProps {
  n: number;
  diff: RevisionDiff | undefined;
  loading: boolean;
  error: unknown;
  /** `removed`: the item is gone at `n`, so it is read as of `n − 1`. */
  onSelect(uid: string, removed: boolean): void;
  onClose(): void;
  selected?: string | null;
}

type Kind = "added" | "changed" | "removed";
const SECTIONS: { kind: Kind; title: string; tone: string; mark: string }[] = [
  { kind: "added", title: "Added", tone: "text-gate", mark: "bg-gate" },
  { kind: "changed", title: "Changed", tone: "text-decision", mark: "bg-decision" },
  { kind: "removed", title: "Removed", tone: "text-error", mark: "bg-error" },
];
/** Per section; the rest is counted, not listed (a scan adds thousands). */
const SHOW = 60;
const fmt = (n: number) => n.toLocaleString("en-US");

function NodeItem({ v, kind, onClick, active }: { v: VersionRef; kind: Kind; onClick(): void; active: boolean }) {
  const keys = kind === "changed" ? (v as ChangedRef).changed_keys : [];
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        aria-current={active ? "true" : undefined}
        className={`block w-full rounded-btn px-3 py-2 text-left transition-colors hover:bg-sunken/70 ${active ? "bg-accent-soft/70" : ""}`}
      >
        <span className="flex items-center gap-2">
          <LabelChip label={v.kind} side={sideOfLabel(v.kind, v.props)} />
          <span className={`min-w-0 truncate text-md font-medium ${kind === "removed" ? "text-slate line-through decoration-error/50" : "text-heading"}`}>
            {displayName({ name: v.name, uid: v.uid })}
          </span>
        </span>
        {keys.length ? <span className="mt-1 block truncate pl-0.5 font-mono text-xs text-slate">{keys.join(", ")}</span> : null}
      </button>
    </li>
  );
}

function RelItem({ v, kind, onClick, active }: { v: VersionRef; kind: Kind; onClick(): void; active: boolean }) {
  const rel = parseRelUid(v.uid);
  const keys = kind === "changed" ? (v as ChangedRef).changed_keys : [];
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        aria-current={active ? "true" : undefined}
        title={v.uid}
        className={`block w-full rounded-btn px-3 py-2 text-left transition-colors hover:bg-sunken/70 ${active ? "bg-accent-soft/70" : ""}`}
      >
        <span className="font-mono text-2xs font-semibold tracking-wide text-accent-ink">{v.kind}</span>
        {rel ? (
          <span className={`mt-0.5 flex items-center gap-1.5 text-sm ${kind === "removed" ? "text-slate line-through decoration-error/50" : "text-ink"}`}>
            <span className="min-w-0 truncate">{displayName({ uid: rel.src })}</span>
            <ArrowRight size={12} className="shrink-0 text-slate" aria-hidden />
            <span className="min-w-0 truncate">{displayName({ uid: rel.dst })}</span>
          </span>
        ) : null}
        {keys.length ? <span className="mt-1 block truncate font-mono text-xs text-slate">{keys.join(", ")}</span> : null}
      </button>
    </li>
  );
}

function Section({
  title,
  tone,
  mark,
  kind,
  items,
  rels,
  onSelect,
  selected,
}: {
  title: string;
  tone: string;
  mark: string;
  kind: Kind;
  items: VersionRef[];
  rels: boolean;
  onSelect(uid: string, removed: boolean): void;
  selected?: string | null;
}) {
  const [open, setOpen] = useState(true);
  const [all, setAll] = useState(false);
  const list = all ? items : items.slice(0, SHOW);
  return (
    <section className="border-b border-line/70 py-2 last:border-0" aria-label={title}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        disabled={items.length === 0}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left disabled:cursor-default"
      >
        <span className={`h-2 w-2 rounded-full ${mark}`} aria-hidden />
        <span className={`text-sm font-semibold ${tone}`}>{title}</span>
        <span className="tnum text-sm text-slate">{fmt(items.length)}</span>
        {items.length ? (
          <ChevronDown size={14} className={`ml-auto text-slate transition-transform ${open ? "" : "-rotate-90"}`} aria-hidden />
        ) : null}
      </button>
      {open && items.length ? (
        <ul className="mt-0.5 space-y-0.5">
          {list.map((v) =>
            rels ? (
              <RelItem
                key={v.uid}
                v={v}
                kind={kind}
                active={false}
                onClick={() => {
                  const r = parseRelUid(v.uid);
                  if (r) onSelect(r.src, false);
                }}
              />
            ) : (
              <NodeItem key={v.uid} v={v} kind={kind} active={selected === v.uid} onClick={() => onSelect(v.uid, kind === "removed")} />
            ),
          )}
          {items.length > list.length ? (
            <li>
              <button type="button" className="px-3 py-1.5 text-sm font-medium text-accent-ink hover:underline" onClick={() => setAll(true)}>
                Show all {fmt(items.length)}
              </button>
            </li>
          ) : null}
        </ul>
      ) : null}
    </section>
  );
}

const total = (d: DiffSet) => d.added.length + d.changed.length + d.removed.length;

export function DiffPanel({ n, diff, loading, error, onSelect, onClose, selected }: DiffPanelProps) {
  const [what, setWhat] = useState<"nodes" | "relationships">("nodes");
  const set = diff ? diff[what] : null;
  return (
    <aside
      data-testid="memory-diff"
      aria-label={`Changes in revision ${n}`}
      className="mem-slide-in absolute bottom-3 right-3 top-3 z-10 flex w-[340px] flex-col overflow-hidden rounded-card bg-surface shadow-pop ring-1 ring-line backdrop-blur"
    >
      <header className="border-b border-line/80 px-4 pb-3 pt-3.5">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <p className="label">What changed</p>
            <h3 className="mt-0.5 flex items-center gap-2 text-base font-semibold text-heading">
              Revision {n}
              {diff ? (
                <span className="inline-flex items-center gap-1.5 text-sm font-medium text-slate">
                  <span className="h-2 w-2 rounded-full" style={{ background: phaseCss(diff.revision.phase) }} aria-hidden />
                  {diff.revision.phase}
                </span>
              ) : null}
            </h3>
            {diff ? (
              <p className="mt-0.5 truncate text-sm text-slate">
                {[diff.revision.actor, relativeTime(diff.revision.at)].filter(Boolean).join(" · ")}
              </p>
            ) : null}
          </div>
          <button type="button" className="btn-ghost -mr-1.5 -mt-0.5 p-1.5" aria-label="Close changes" onClick={onClose}>
            <X size={16} aria-hidden />
          </button>
        </div>
        {diff ? (
          <div className="mt-3 inline-flex w-full rounded-btn bg-sunken p-1" role="group" aria-label="Show">
            {(["nodes", "relationships"] as const).map((w) => (
              <button
                key={w}
                type="button"
                aria-pressed={what === w}
                onClick={() => setWhat(w)}
                className={`flex-1 rounded-[5px] px-3 py-1 text-sm font-medium capitalize transition-all ${
                  what === w ? "bg-surface text-heading shadow-card" : "text-slate hover:text-heading"
                }`}
              >
                {w} <span className="tnum text-slate/80">{fmt(total(diff[w]))}</span>
              </button>
            ))}
          </div>
        ) : null}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-1 py-1">
        {loading ? (
          <p className="flex items-center gap-2 px-3 py-4 text-md text-slate">
            <Loader2 size={15} className="animate-spin" aria-hidden /> Reading the revision…
          </p>
        ) : error ? (
          <div className="p-3">
            <ErrorNote error={error} />
          </div>
        ) : set ? (
          total(set) === 0 ? (
            <p className="px-3 py-4 text-md text-slate">This revision changed no {what}.</p>
          ) : (
            SECTIONS.map((s) => (
              <Section
                key={`${what}-${s.kind}`}
                {...s}
                items={set[s.kind]}
                rels={what === "relationships"}
                onSelect={onSelect}
                selected={selected}
              />
            ))
          )
        ) : null}
      </div>
      <p className="border-t border-line/80 px-4 py-2 text-xs text-slate">
        Outlined on the canvas: <span className="font-medium text-gate">added</span>, <span className="font-medium text-decision">changed</span>,{" "}
        <span className="font-medium text-error">removed</span> (faded)
      </p>
    </aside>
  );
}
