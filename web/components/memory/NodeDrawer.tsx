"use client";

// The selected node, on the right: Overview, History, Knowledge, Lineage and, for curators only,
// Curate. Everything is read as of the explorer's revision.
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Check, Copy, Crosshair, Loader2, X } from "lucide-react";
import { ErrorNote } from "@/components/ui/Empty";
import { ApiError } from "@/lib/api";
import { Pill } from "@/components/ui/Pill";
import { useCan } from "@/lib/personas";
import { CurateForm, type Submission } from "./CurateForm";
import { LabelChip } from "./SearchRail";
import {
  GROUP_LABEL,
  displayName,
  relativeTime,
  useMemoryNode,
  type LineageOut,
  type MemNodeOut,
  type NodeDetail,
  type VersionOut,
} from "@/lib/memory";

export const DRAWER_TABS = ["Overview", "History", "Knowledge", "Lineage", "Curate"] as const;
export type DrawerTab = (typeof DRAWER_TABS)[number];

export interface NodeDrawerProps {
  projectId: string;
  uid: string;
  asOf: number | null;
  onClose(): void;
  onSelect(uid: string): void;
  onFocus(uid: string): void;
  /** Leaves the as-of view (the Curate tab offers it: curation acts on the current memory). */
  onBackToCurrent?(): void;
  /** Shown under the uid when the node is a removed item of a revision diff (read as of the revision before). */
  removedNote?: string | null;
}

function show(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function CopyUid({ uid }: { uid: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="mt-3 flex items-center gap-2 rounded-btn bg-warm/80 px-3 py-2">
      <code className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-ink" title={uid} data-testid="node-uid">
        {uid}
      </code>
      <button
        type="button"
        className="shrink-0 text-slate hover:text-plum"
        aria-label="Copy uid"
        title="Copy uid"
        onClick={() => {
          navigator.clipboard?.writeText(uid).then(
            () => {
              setDone(true);
              window.setTimeout(() => setDone(false), 1500);
            },
            () => undefined,
          );
        }}
      >
        {done ? <Check size={15} className="text-gate" aria-hidden /> : <Copy size={15} aria-hidden />}
      </button>
    </div>
  );
}

function Overview({ d }: { d: NodeDetail }) {
  const n = d.node;
  const props = Object.entries(n.props).filter(([, v]) => v !== null && v !== undefined && v !== "");
  return (
    <div className="space-y-6">
      {n.text ? <p className="text-[14.5px] leading-relaxed text-ink">{n.text}</p> : null}
      <dl className="grid grid-cols-2 gap-3">
        <div className="rounded-btn border border-line/80 px-3.5 py-2.5">
          <dt className="label">Last changed</dt>
          <dd className="mt-0.5 text-[14px] font-medium text-plum">Revision {n.valid_from}</dd>
        </div>
        <div className="rounded-btn border border-line/80 px-3.5 py-2.5">
          <dt className="label">State</dt>
          <dd className="mt-0.5 text-[14px] font-medium text-plum">
            {n.valid_to === null ? "Current" : `Closed in revision ${n.valid_to}`}
          </dd>
        </div>
      </dl>
      <div>
        <p className="label mb-2">Properties</p>
        {props.length === 0 ? (
          <p className="text-[14px] text-slate">No properties.</p>
        ) : (
          <table className="w-full text-[13.5px]">
            <tbody className="divide-y divide-line/70">
              {props.map(([k, v]) => (
                <tr key={k} className="align-top">
                  <th scope="row" className="w-[38%] py-2 pr-3 text-left font-medium text-slate">
                    {k}
                  </th>
                  <td className="break-words py-2 font-mono text-[12.5px] text-ink">{show(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function History({ versions }: { versions: VersionOut[] }) {
  if (versions.length === 0) return <p className="text-[14px] text-slate">No versions recorded.</p>;
  return (
    <ol className="relative space-y-5 border-l border-line pl-5">
      {versions.map((v, i) => {
        const first = i === versions.length - 1;
        return (
          <li key={v.valid_from} className="relative">
            <span className={`absolute -left-[26px] top-1.5 h-3 w-3 rounded-full ring-4 ring-white ${i === 0 ? "bg-purple" : "bg-line-dk"}`} aria-hidden />
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[14px] font-semibold text-plum">Revision {v.valid_from}</span>
              <Pill tone="purple">{v.phase}</Pill>
              {v.valid_to === null ? <Pill tone="gate">current</Pill> : null}
            </div>
            <p className="mt-0.5 text-[13px] text-slate">
              {[v.actor, v.at ? relativeTime(v.at) : null].filter(Boolean).join(" · ") || " "}
            </p>
            {first ? (
              <p className="mt-1.5 text-[13px] text-slate">First recorded.</p>
            ) : v.changed_keys.length === 0 ? (
              <p className="mt-1.5 text-[13px] text-slate">No property changes.</p>
            ) : (
              <ul className="mt-2 space-y-1">
                {v.changed_keys.map((k) => (
                  <li key={k} className="flex gap-2 text-[13px]">
                    <span className="shrink-0 font-medium text-plum">{k}</span>
                    <span className="min-w-0 truncate font-mono text-[12px] text-slate" title={show(v.props[k])}>
                      {k in v.props ? show(v.props[k]) : k === "text" || k === "name" ? "updated" : "removed"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function KnowledgeItem({ n, onSelect }: { n: MemNodeOut; onSelect(uid: string): void }) {
  const kind = typeof n.props.kind === "string" ? n.props.kind : null;
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(n.uid)}
        className="block w-full rounded-btn border border-line/80 px-4 py-3 text-left transition-colors hover:border-line-dk hover:bg-warm/50"
      >
        <span className="flex items-center gap-2">
          <LabelChip label={n.label} side={n.side} />
          {kind ? <span className="text-[12.5px] text-slate">{kind}</span> : null}
          {n.obsolete ? <Pill tone="det">obsolete</Pill> : null}
          <span className="ml-auto text-[11.5px] text-slate/80">rev {n.valid_from}</span>
        </span>
        <span className={`mt-1.5 block text-[13.5px] leading-relaxed ${n.obsolete ? "text-slate line-through" : "text-ink"}`}>
          {n.text || displayName(n)}
        </span>
      </button>
    </li>
  );
}

function Knowledge({ d, onSelect }: { d: NodeDetail; onSelect(uid: string): void }) {
  const about = [...d.about].sort((a, b) => b.valid_from - a.valid_from);
  return (
    <div className="space-y-6">
      {about.length === 0 ? (
        <p className="text-[14px] text-slate">No knowledge about this node yet. Analyses, decisions and curations add to it.</p>
      ) : (
        <ul className="space-y-2.5">
          {about.map((n) => (
            <KnowledgeItem key={n.uid} n={n} onSelect={onSelect} />
          ))}
        </ul>
      )}
      {d.curations.length > 0 ? (
        <div>
          <p className="label mb-2">Curations</p>
          <ul className="space-y-2.5">
            {d.curations.map((n) => (
              <KnowledgeItem key={n.uid} n={n} onSelect={onSelect} />
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

const STATUS_TONE = { generated: "det", corrected: "gate", rejected: "error" } as const;

function LineageRow({ l, onSelect }: { l: LineageOut; onSelect(uid: string): void }) {
  const Arrow = l.direction === "out" ? ArrowRight : ArrowLeft;
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(l.other.uid)}
        className="flex w-full items-center gap-3 rounded-btn border border-line/80 px-4 py-3 text-left transition-colors hover:border-line-dk hover:bg-warm/50"
      >
        <Arrow size={15} className="shrink-0 text-slate" aria-label={l.direction === "out" ? "outgoing" : "incoming"} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="font-mono text-[11.5px] font-semibold tracking-wide text-purple-dk">{l.edge.type}</span>
            {l.edge.status ? <Pill tone={STATUS_TONE[l.edge.status]}>{l.edge.status}</Pill> : null}
          </span>
          <span className="mt-1 flex items-center gap-2">
            <LabelChip label={l.other.label} side={l.other.side} />
            <span className="truncate text-[13.5px] font-medium text-plum">{displayName(l.other)}</span>
          </span>
          <span className="mt-0.5 block truncate font-mono text-[11.5px] text-slate" title={l.other.uid}>
            {l.other.uid}
          </span>
        </span>
      </button>
    </li>
  );
}

function Lineage({ lineage, onSelect }: { lineage: LineageOut[]; onSelect(uid: string): void }) {
  if (lineage.length === 0) return <p className="text-[14px] text-slate">No migration, test or failure links yet. The build and verify phases add them.</p>;
  const groups = [
    { title: "Outgoing", rows: lineage.filter((l) => l.direction === "out") },
    { title: "Incoming", rows: lineage.filter((l) => l.direction === "in") },
  ];
  return (
    <div className="space-y-6">
      {groups.map((g) =>
        g.rows.length ? (
          <div key={g.title}>
            <p className="label mb-2">
              {g.title} · {g.rows.length}
            </p>
            <ul className="space-y-2.5">
              {g.rows.map((l) => (
                <LineageRow key={`${l.edge.uid}`} l={l} onSelect={onSelect} />
              ))}
            </ul>
          </div>
        ) : null,
      )}
    </div>
  );
}

export function NodeDrawer({ projectId, uid, asOf, onClose, onSelect, onFocus, onBackToCurrent, removedNote }: NodeDrawerProps) {
  // The tab stays put as the selection moves from node to node.
  const [chosenTab, setTab] = useState<DrawerTab>("Overview");
  const can = useCan();
  const tabs = DRAWER_TABS.filter((t) => t !== "Curate" || can.curate);
  const tab: DrawerTab = tabs.includes(chosenTab) ? chosenTab : "Overview";
  // Curations sent from here, so "Pending" outlives switching tab or node.
  const [submissions, setSubmissions] = useState<Submission[]>([]);
  const panel = useRef<HTMLElement>(null);

  // Opening moves focus into the drawer; closing gives it back to whatever opened it (a search
  // result, a lineage row), when that is still on the page.
  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : null;
    panel.current?.focus({ preventScroll: true });
    return () => {
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
  }, []);

  // Escape closes it from anywhere but a text field outside it (Escape in the search box clears
  // the search, not the drawer).
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const at = document.activeElement;
      const typing = at instanceof HTMLInputElement || at instanceof HTMLTextAreaElement || at instanceof HTMLSelectElement;
      if (typing && !panel.current?.contains(at)) return;
      e.preventDefault();
      close.current();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const detail = useMemoryNode(projectId, uid, asOf);
  const d = detail.data;
  const counts: Partial<Record<DrawerTab, number>> = d
    ? { History: d.versions.length, Knowledge: d.about.length + d.curations.length, Lineage: d.lineage.length }
    : {};

  return (
    <aside
      ref={panel}
      tabIndex={-1}
      data-testid="node-drawer"
      className="card flex min-h-0 w-[440px] shrink-0 flex-col overflow-hidden outline-none"
      aria-label="Node detail"
    >
      <div className="border-b border-line/80 px-6 pb-0 pt-5">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            {d ? (
              <>
                <div className="flex items-center gap-2">
                  <LabelChip label={d.node.label} side={d.node.side} />
                  <span className="text-[12.5px] text-slate">{GROUP_LABEL[d.node.side]}</span>
                  {d.node.curated ? <Pill tone="purple">curated</Pill> : null}
                  {d.node.obsolete ? <Pill tone="det">obsolete</Pill> : null}
                </div>
                <h2 className="mt-2 break-words text-[19px] font-semibold leading-snug text-plum">{displayName(d.node)}</h2>
              </>
            ) : (
              <h2 className="text-[19px] font-semibold text-plum">Node</h2>
            )}
          </div>
          <button type="button" className="btn-ghost -mr-2 -mt-1 p-1.5" aria-label="Close node detail" onClick={onClose}>
            <X size={18} aria-hidden />
          </button>
        </div>
        <CopyUid uid={uid} />
        {removedNote ? (
          <p className="mt-2 rounded-btn bg-error-bg px-3 py-1.5 text-[12.5px] text-error ring-1 ring-inset ring-error/15">{removedNote}</p>
        ) : null}
        <div className="mt-2 flex justify-end">
          <button type="button" className="inline-flex items-center gap-1.5 text-[13px] font-medium text-purple hover:underline" onClick={() => onFocus(uid)}>
            <Crosshair size={14} aria-hidden /> Explore around this node
          </button>
        </div>
        <div role="tablist" aria-label="Node detail" className="-mb-px mt-3 flex gap-5">
          {tabs.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              id={`node-tab-${t}`}
              aria-selected={tab === t}
              aria-controls="node-tabpanel"
              onClick={() => setTab(t)}
              className={`border-b-2 pb-2.5 text-[14px] font-medium transition-colors ${
                tab === t ? "border-purple text-plum" : "border-transparent text-slate hover:text-plum"
              }`}
            >
              {t}
              {counts[t] ? <span className="ml-1.5 text-[12px] text-slate/80">{counts[t]}</span> : null}
            </button>
          ))}
        </div>
      </div>
      <div id="node-tabpanel" role="tabpanel" aria-labelledby={`node-tab-${tab}`} className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        {detail.isLoading ? (
          <p className="flex items-center gap-2 text-[14px] text-slate">
            <Loader2 size={15} className="animate-spin" aria-hidden /> Reading the node…
          </p>
        ) : detail.error instanceof ApiError && detail.error.status === 404 ? (
          <p className="rounded-btn bg-warm/80 px-4 py-3 text-[14px] leading-relaxed text-slate">
            {asOf !== null ? `Not in the memory at revision ${asOf}: recorded after it, or removed before it.` : "Not in this project's memory."}
          </p>
        ) : detail.error ? (
          <ErrorNote error={detail.error} />
        ) : !d ? null : tab === "Overview" ? (
          <Overview d={d} />
        ) : tab === "History" ? (
          <History versions={d.versions} />
        ) : tab === "Knowledge" ? (
          <Knowledge d={d} onSelect={onSelect} />
        ) : tab === "Lineage" ? (
          <Lineage lineage={d.lineage} onSelect={onSelect} />
        ) : (
          <CurateForm
            projectId={projectId}
            detail={d}
            asOf={asOf}
            submissions={submissions}
            onSubmitted={(s) => setSubmissions((xs) => [...xs, s])}
            onShowKnowledge={() => setTab("Knowledge")}
            onBackToCurrent={onBackToCurrent}
          />
        )}
      </div>
    </aside>
  );
}
