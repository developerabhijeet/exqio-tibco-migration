"use client";

// The /memory explorer: search rail, graph canvas and node drawer over the selected project's
// memory graph. The view lives in the URL (uid, rev, q, labels, side, mode) so it can be shared;
// the node the neighbourhood is read around (the focus) follows the selected uid whenever the
// selection is not already on the canvas.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, GitBranch, Loader2, Share2, Waypoints } from "lucide-react";
import { Empty, ErrorNote } from "@/components/ui/Empty";
import { useAppState } from "@/lib/state";
import { ApiError } from "@/lib/api";
import {
  GROUP_HEX,
  GROUP_LABEL,
  LABEL_GROUPS,
  memoryApi,
  rankHits,
  useMemoryGraph,
  useMemorySearch,
  useMemoryStatus,
  type GraphMode,
  type GraphQuery,
  type MemoryHit,
  type Side,
  type Subgraph,
} from "@/lib/memory";
import { MemoryCanvas } from "./MemoryCanvas";
import { MemoryStatusPill } from "./MemoryStatusPill";
import { MemoryUnavailable, memoryState } from "./MemoryUnavailable";
import { NodeDrawer } from "./NodeDrawer";
import { SearchRail } from "./SearchRail";

const ALL_LABELS = new Set<string>([...LABEL_GROUPS.old, ...LABEL_GROUPS.new, ...LABEL_GROUPS.knowledge]);
const SIDES = new Set(["old", "new", "knowledge"]);

/** The explorer's URL state, read once per render. */
function useView() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const rev = Number(params.get("rev"));
  const view = {
    uid: params.get("uid") || null,
    rev: Number.isInteger(rev) && rev >= 1 ? rev : null,
    q: params.get("q") ?? "",
    labels: (params.get("labels") ?? "").split(",").filter((l) => ALL_LABELS.has(l)),
    side: (SIDES.has(params.get("side") ?? "") ? params.get("side") : null) as Side | null,
    mode: (params.get("mode") === "lineage" ? "lineage" : "neighbourhood") as GraphMode,
    phase: params.get("phase") || null,
  };
  const paramsRef = useRef(params);
  paramsRef.current = params;
  const set = useCallback(
    (patch: Partial<Record<"uid" | "rev" | "q" | "labels" | "side" | "mode" | "phase", string | null>>, push = false) => {
      const next = new URLSearchParams(paramsRef.current.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === "" || (k === "mode" && v === "neighbourhood")) next.delete(k);
        else next.set(k, v);
      }
      const url = `${pathname}${next.size ? `?${next.toString()}` : ""}`;
      if (push) router.push(url, { scroll: false });
      else router.replace(url, { scroll: false });
    },
    [pathname, router],
  );
  return { view, set };
}

function mergeGraphs(base: Subgraph, more: (Subgraph | undefined)[]): Subgraph {
  const extra = more.filter((g): g is Subgraph => !!g);
  if (extra.length === 0) return base;
  const nodes = new Map(base.nodes.map((n) => [n.uid, n]));
  const edges = new Map(base.edges.map((e) => [e.uid, e]));
  for (const g of extra) {
    g.nodes.forEach((n) => nodes.set(n.uid, n));
    g.edges.forEach((e) => edges.set(e.uid, e));
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()], truncated: base.truncated || extra.some((g) => g.truncated), total: Math.max(base.total, nodes.size) };
}

function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string; icon?: React.ReactNode; disabled?: boolean; title?: string }[];
  onChange(v: T): void;
  label: string;
}) {
  return (
    <div className="inline-flex rounded-btn bg-warm p-1" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          disabled={o.disabled}
          title={o.title}
          onClick={() => onChange(o.value)}
          className={`inline-flex items-center gap-1.5 rounded-[5px] px-3 py-1.5 text-[13px] font-medium transition-all disabled:cursor-not-allowed disabled:opacity-40 ${
            value === o.value ? "bg-white text-plum shadow-card" : "text-slate hover:text-plum"
          }`}
        >
          {o.icon}
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** The strip under the canvas: what the memory holds per group. The revision timeline (Task 15)
 * takes this slot. */
function SummaryStrip({ counts }: { counts: Record<string, number> }) {
  const groups = (["old", "new", "knowledge"] as const).map((g) => {
    const members: readonly string[] = LABEL_GROUPS[g];
    const top = members
      .filter((l) => (g === "old" || l !== "Endpoint") && counts[l])
      .map((l) => [l, counts[l]] as const)
      .sort((a, b) => b[1] - a[1]);
    return { g, total: top.reduce((s, [, n]) => s + n, 0), top: top.slice(0, 4) };
  });
  return (
    <section className="card flex h-[120px] shrink-0 items-stretch divide-x divide-line/80" data-testid="memory-timeline-slot" aria-label="Memory contents">
      {groups.map(({ g, total, top }) => (
        <div key={g} className="flex min-w-0 flex-1 flex-col justify-center px-6">
          <p className="flex items-center gap-2 text-[13px] font-medium text-slate">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: GROUP_HEX[g].border }} aria-hidden />
            {GROUP_LABEL[g]}
          </p>
          <p className="tnum mt-1 text-[24px] font-semibold leading-tight text-plum">{total.toLocaleString("en-US")}</p>
          <p className="mt-0.5 truncate text-[12.5px] text-slate">
            {top.length ? top.map(([l, n]) => `${n} ${l}`).join(" · ") : "Nothing yet"}
          </p>
        </div>
      ))}
    </section>
  );
}

export function MemoryExplorer() {
  const { projectId } = useAppState();
  const { view, set } = useView();
  const qc = useQueryClient();
  const status = useMemoryStatus(projectId);
  const state = memoryState(status.data);
  const ready = !!status.data && state === null;

  const [depth, setDepth] = useState<1 | 2>(1);
  const [focus, setFocus] = useState<string | null>(view.uid);
  const [expanded, setExpanded] = useState<string[]>([]);
  const [copied, setCopied] = useState(false);

  // Switching to another project starts from its top-level view (not the first restore of the
  // remembered project, which would wipe a shared link's view).
  const lastProject = useRef(projectId);
  useEffect(() => {
    if (lastProject.current === projectId) return;
    const switched = lastProject.current !== null;
    lastProject.current = projectId;
    if (!switched) return;
    setFocus(null);
    setExpanded([]);
    set({ uid: null, rev: null, q: null });
  }, [projectId, set]);

  // When the memory moves on (a new revision) and the view is the current one, re-read it.
  const revision = status.data?.revision ?? null;
  const seenRevision = useRef(revision);
  useEffect(() => {
    if (seenRevision.current === revision) return;
    seenRevision.current = revision;
    if (view.rev === null) {
      for (const key of ["memory-graph", "memory-node", "memory-search", "memory-revisions"]) qc.invalidateQueries({ queryKey: [key, projectId] });
    }
  }, [revision, view.rev, projectId, qc]);

  const filtered = view.labels.length > 0 || view.side !== null;
  const mode: GraphMode = view.mode === "lineage" && view.uid ? "lineage" : "neighbourhood";
  const graphQuery: GraphQuery = {
    uid: mode === "lineage" ? view.uid : focus,
    depth,
    labels: view.labels,
    side: view.side,
    asOf: view.rev,
    mode,
  };
  const graph = useMemoryGraph(projectId, graphQuery, ready);
  // The selected node's lineage chain, for the canvas highlight (lineage mode draws it already).
  const chain = useMemoryGraph(
    projectId,
    { uid: view.uid, depth: 1, labels: [], side: null, asOf: view.rev, mode: "lineage" },
    ready && mode === "neighbourhood" && !!view.uid,
  );
  const more = useQueries({
    queries: expanded.map((uid) => ({
      queryKey: ["memory-graph", projectId, { ...graphQuery, uid, depth: 1 as const, mode: "neighbourhood" as const }],
      queryFn: () => memoryApi.graph(projectId!, { ...graphQuery, uid, depth: 1, mode: "neighbourhood" }),
      enabled: ready && mode === "neighbourhood",
    })),
  });
  const moreData = more.map((m) => m.data);
  const shown = useMemo(
    () => (graph.data ? mergeGraphs(graph.data, mode === "neighbourhood" ? moreData : []) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [graph.data, mode, ...moreData],
  );

  // A selection the canvas does not show (a shared link, back, a drawer link) becomes the focus.
  useEffect(() => {
    if (!view.uid || mode !== "neighbourhood" || !shown || graph.isFetching) return;
    if (!shown.nodes.some((n) => n.uid === view.uid)) {
      setFocus(view.uid);
      setExpanded([]);
    }
  }, [view.uid, mode, shown, graph.isFetching]);

  // Filters or depth change: expansions were read under the old ones.
  const filterKey = `${view.labels.join(",")}|${view.side}|${depth}|${view.rev}`;
  useEffect(() => setExpanded([]), [filterKey]);

  const search = useMemorySearch(projectId, view.q, { labels: view.labels, side: view.side, phase: view.phase, asOf: view.rev });

  const pick = (h: MemoryHit) => {
    setFocus(h.uid);
    setExpanded([]);
    set({ uid: h.uid }, true);
  };
  const select = (uid: string) => set({ uid }, true);
  const focusOn = (uid: string) => {
    setFocus(uid);
    setExpanded([]);
    set({ uid, mode: null }, true);
  };
  const expand = (uid: string) => {
    if (mode === "lineage") return focusOn(uid);
    if (uid === focus) return;
    setExpanded((xs) => (xs.includes(uid) ? xs : [...xs, uid]));
  };

  const share = () => {
    navigator.clipboard?.writeText(window.location.href).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      },
      () => undefined,
    );
  };

  const graphError = graph.error instanceof ApiError && graph.error.status === 404 ? new Error("That node is not in this project's memory (as of this revision).") : graph.error;
  const counts = status.data?.counts ?? {};
  const focusNode = shown?.nodes.find((n) => n.uid === (mode === "lineage" ? view.uid : focus));

  return (
    <div className="flex h-[calc(100vh-13.5rem)] min-h-[640px] flex-col gap-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Link href="/" className="inline-flex items-center gap-1.5 text-[13.5px] font-medium text-purple hover:underline">
            <ArrowLeft size={15} aria-hidden /> Back to the migration
          </Link>
          <h1 className="screen-title mt-2">Memory</h1>
          <p className="mt-0.5 text-[15px] text-slate">The old code, the new code and what the team learnt, linked and versioned by revision.</p>
        </div>
        <div className="flex items-center gap-3">
          <MemoryStatusPill status={status.data} />
          {ready ? (
            <button type="button" className="btn-secondary py-1.5 text-[13.5px]" onClick={share} title="Copy a link to this view">
              <Share2 size={15} aria-hidden />
              {copied ? "Link copied" : "Share view"}
            </button>
          ) : null}
        </div>
      </header>

      {!projectId ? (
        <Empty title="No project selected">Pick a project in the top bar.</Empty>
      ) : status.error ? (
        <ErrorNote error={status.error} />
      ) : status.isLoading ? (
        <p className="flex items-center gap-2 text-slate">
          <Loader2 size={16} className="animate-spin" aria-hidden /> Reading memory…
        </p>
      ) : state ? (
        <MemoryUnavailable state={state} onRetry={() => status.refetch()} retrying={status.isFetching} />
      ) : (
        <>
          <div className="flex min-h-0 flex-1 gap-5">
            <SearchRail
              q={view.q}
              onQ={(q) => set({ q })}
              labels={view.labels}
              onLabels={(ls) => set({ labels: ls.join(",") })}
              side={view.side}
              onSide={(s) => set({ side: s })}
              phase={view.phase}
              onPhase={(p) => set({ phase: p })}
              counts={counts}
              hits={search.data ? rankHits(search.data, view.q) : undefined}
              searching={search.isFetching}
              error={search.error}
              selected={view.uid}
              onPick={pick}
            />

            <section className="card flex min-w-0 flex-1 flex-col overflow-hidden" aria-label="Memory graph">
              <div className="flex flex-wrap items-center gap-3 border-b border-line/80 px-5 py-3">
                <div className="min-w-0 flex-1">
                  <p className="label">{mode === "lineage" ? "Lineage of" : focus ? "Around" : "Project overview"}</p>
                  <p className="truncate text-[15px] font-semibold text-plum">
                    {focusNode ? focusNode.name || focusNode.uid : focus ? focus : "Domains, applications and solutions"}
                    {graph.isFetching ? <Loader2 size={14} className="ml-2 inline animate-spin text-slate" aria-hidden /> : null}
                  </p>
                </div>
                {mode === "neighbourhood" && focus ? (
                  <Segmented<1 | 2>
                    label="Depth"
                    value={depth}
                    onChange={setDepth}
                    options={[
                      { value: 1, label: "1 hop" },
                      { value: 2, label: "2 hops" },
                    ]}
                  />
                ) : null}
                <Segmented<GraphMode>
                  label="Layout"
                  value={mode}
                  onChange={(m) => set({ mode: m })}
                  options={[
                    { value: "neighbourhood", label: "Neighbourhood", icon: <Waypoints size={14} aria-hidden /> },
                    {
                      value: "lineage",
                      label: "Lineage",
                      icon: <GitBranch size={14} aria-hidden />,
                      disabled: !view.uid,
                      title: view.uid ? "Old code to new code, left to right" : "Select a node to see its lineage",
                    },
                  ]}
                />
              </div>
              <div className="relative min-h-0 flex-1 bg-[radial-gradient(circle,_#E9E6DE_1px,_transparent_1px)] [background-size:22px_22px]">
                {graphError ? (
                  <div className="p-6">
                    <ErrorNote error={graphError} />
                  </div>
                ) : shown ? (
                  <MemoryCanvas
                    graph={shown}
                    mode={mode}
                    selected={view.uid ?? undefined}
                    lineage={mode === "neighbourhood" ? chain.data : undefined}
                    focus={mode === "neighbourhood" ? focus : null}
                    filtered={filtered}
                    onSelect={select}
                    onExpand={expand}
                  />
                ) : (
                  <p className="flex items-center gap-2 p-6 text-slate">
                    <Loader2 size={16} className="animate-spin" aria-hidden /> Laying out the graph…
                  </p>
                )}
              </div>
              <p className="border-t border-line/80 px-5 py-2 text-[12.5px] text-slate">
                Click a node to open it · double-click to {mode === "lineage" ? "explore around it" : "expand its neighbours"} · scroll to zoom
              </p>
            </section>

            {view.uid ? (
              <NodeDrawer
                key={view.uid}
                projectId={projectId}
                uid={view.uid}
                asOf={view.rev}
                onClose={() => set({ uid: null, mode: null }, true)}
                onSelect={select}
                onFocus={focusOn}
              />
            ) : null}
          </div>

          <SummaryStrip counts={counts} />
        </>
      )}
    </div>
  );
}

