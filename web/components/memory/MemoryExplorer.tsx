"use client";

// The /memory explorer: search rail, graph canvas and node drawer over the selected project's
// memory graph, with the revision timeline underneath. The view lives in the URL (uid, rev, q,
// labels, side, mode, and `changes`: the revision whose diff is open) so it can be shared;
// the node the neighbourhood is read around (the focus) follows the selected uid whenever the
// selection is not already on the canvas.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { DatabaseBackup, GitBranch, Loader2, Share2, Waypoints } from "lucide-react";
import { Empty, ErrorNote } from "@/components/ui/Empty";
import { PageHeader } from "@/components/ui/PageHeader";
import { useAppState } from "@/lib/state";
import { ApiError } from "@/lib/api";
import {
  LABEL_GROUPS,
  memoryApi,
  parseRelUid,
  rankHits,
  useMemoryGraph,
  useMemorySearch,
  useMemoryStatus,
  useReingest,
  useRevisionDiff,
  useRevisions,
  type EdgeOut,
  type GraphMode,
  type GraphQuery,
  type MemNodeOut,
  type MemoryHit,
  type RevisionDiff,
  type Side,
  type Subgraph,
} from "@/lib/memory";
import { useCan } from "@/lib/personas";
import { DiffPanel } from "./DiffPanel";
import { MemoryCanvas, type CanvasHighlight } from "./MemoryCanvas";
import { MemoryStatusPill } from "./MemoryStatusPill";
import { MemoryUnavailable, memoryState } from "./MemoryUnavailable";
import { NodeDrawer } from "./NodeDrawer";
import { SearchRail } from "./SearchRail";
import { Timeline } from "./Timeline";

const ALL_LABELS = new Set<string>([...LABEL_GROUPS.old, ...LABEL_GROUPS.new, ...LABEL_GROUPS.knowledge]);
const SIDES = new Set(["old", "new", "knowledge"]);

/** The explorer's URL state, read once per render. */
function useView() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const rev = Number(params.get("rev"));
  const changes = Number(params.get("changes"));
  const view = {
    uid: params.get("uid") || null,
    rev: Number.isInteger(rev) && rev >= 1 ? rev : null,
    changes: Number.isInteger(changes) && changes >= 1 ? changes : null,
    q: params.get("q") ?? "",
    labels: (params.get("labels") ?? "").split(",").filter((l) => ALL_LABELS.has(l)),
    side: (SIDES.has(params.get("side") ?? "") ? params.get("side") : null) as Side | null,
    mode: (params.get("mode") === "lineage" ? "lineage" : "neighbourhood") as GraphMode,
    phase: params.get("phase") || null,
  };
  // Several updates in one tick (Clear resets three filters) must build on each other, not each
  // on the params of the last render: `pending` holds the URL written but not yet rendered.
  const pending = useRef<string | null>(null);
  const paramsRef = useRef(params);
  paramsRef.current = params;
  const rendered = params.toString();
  useEffect(() => {
    pending.current = null;
  }, [rendered]);
  const set = useCallback(
    (patch: Partial<Record<"uid" | "rev" | "changes" | "q" | "labels" | "side" | "mode" | "phase", string | null>>, push = false) => {
      const next = new URLSearchParams(pending.current ?? paramsRef.current.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === "" || (k === "mode" && v === "neighbourhood")) next.delete(k);
        else next.set(k, v);
      }
      pending.current = next.toString();
      const url = `${pathname}${next.size ? `?${next.toString()}` : ""}`;
      if (push) router.push(url, { scroll: false });
      else router.replace(url, { scroll: false });
    },
    [pathname, router],
  );
  return { view, set };
}

/** useQueries' combine, stable at module level so its result is structurally shared between
 * renders: the merged graph is rebuilt only when an expansion's data changes. */
const graphsOnly = (results: { data?: Subgraph }[]) => results.map((r) => r.data);
const ghostsOnly = (results: { data?: MemNodeOut }[]) => results.map((r) => r.data);

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
    <div className="inline-flex rounded-btn bg-sunken p-1" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          disabled={o.disabled}
          title={o.title}
          onClick={() => onChange(o.value)}
          className={`inline-flex items-center gap-1.5 rounded-[5px] px-3 py-1.5 text-sm font-medium transition-all disabled:cursor-not-allowed disabled:opacity-40 ${
            value === o.value ? "bg-surface text-heading shadow-card" : "text-slate hover:text-heading"
          }`}
        >
          {o.icon}
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** The canvas outlines of a revision diff: node and relationship uids by kind. */
function diffHighlight(d: RevisionDiff): CanvasHighlight {
  const uids = (k: "added" | "changed" | "removed") => [...d.nodes[k], ...d.relationships[k]].map((v) => v.uid);
  return { added: uids("added"), changed: uids("changed"), removed: uids("removed") };
}

/** Removed nodes worth drawing as ghosts: those a removed relationship tied to a node on screen. */
const MAX_GHOSTS = 40;
function ghostCandidates(d: RevisionDiff, shown: Subgraph): string[] {
  const on = new Set(shown.nodes.map((n) => n.uid));
  const removed = new Set(d.nodes.removed.map((v) => v.uid));
  const out = new Set<string>();
  for (const r of d.relationships.removed) {
    const p = parseRelUid(r.uid);
    if (!p) continue;
    if (removed.has(p.src) && on.has(p.dst) && !on.has(p.src)) out.add(p.src);
    if (removed.has(p.dst) && on.has(p.src) && !on.has(p.dst)) out.add(p.dst);
    if (out.size >= MAX_GHOSTS) break;
  }
  return [...out];
}

/** The shown graph plus the ghosts and the removed relationships that tie them in. */
function withGhosts(shown: Subgraph, ghosts: MemNodeOut[], d: RevisionDiff): Subgraph {
  if (ghosts.length === 0) return shown;
  const nodes = [...shown.nodes, ...ghosts.filter((g) => !shown.nodes.some((n) => n.uid === g.uid))];
  const on = new Set(nodes.map((n) => n.uid));
  const have = new Set(shown.edges.map((e) => e.uid));
  const edges: EdgeOut[] = [...shown.edges];
  for (const r of d.relationships.removed) {
    const p = parseRelUid(r.uid);
    if (!p || have.has(r.uid) || !on.has(p.src) || !on.has(p.dst)) continue;
    edges.push({ uid: r.uid, type: p.type, src: p.src, dst: p.dst, props: r.props, status: null });
  }
  return { ...shown, nodes, edges };
}

/** Admin only: re-ingest the whole project, behind a small modal confirmation. */
function ReingestButton({ projectId }: { projectId: string }) {
  const { admin } = useCan();
  const reingest = useReingest(projectId);
  const [open, setOpen] = useState(false);
  const [announce, setAnnounce] = useState("");
  const [done, setDone] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const { reset } = reingest;

  const close = useCallback(() => {
    setOpen(false);
    trigger.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    box.current?.querySelector<HTMLElement>("button")?.focus();
    // Escape closes this before anything underneath (the node drawer listens on the document in
    // the bubble phase); a pointer outside dismisses it; Tab stays inside it.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close();
        return;
      }
      if (e.key !== "Tab" || !box.current) return;
      const items = [...box.current.querySelectorAll<HTMLElement>("button:not([disabled])")];
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const at = document.activeElement;
      if (e.shiftKey && (at === first || !box.current.contains(at))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (at === last || !box.current.contains(at))) {
        e.preventDefault();
        first.focus();
      }
    };
    const away = (e: PointerEvent) => {
      if (box.current?.contains(e.target as Node) || trigger.current?.contains(e.target as Node)) return;
      close();
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", away);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("pointerdown", away);
    };
  }, [open, close]);

  useEffect(() => {
    if (done === null) return;
    const t = window.setTimeout(() => setDone(null), 4000);
    return () => window.clearTimeout(t);
  }, [done]);

  if (!admin) return null;
  const toggle = () => {
    if (open) return close();
    reset(); // a fresh confirmation, not the last attempt's error
    setAnnounce("");
    setOpen(true);
  };
  return (
    <div className="relative">
      <button
        ref={trigger}
        type="button"
        className="btn-secondary py-1.5 text-[13.5px]"
        onClick={toggle}
        aria-expanded={open}
        aria-haspopup="dialog"
        disabled={reingest.isPending}
      >
        {reingest.isPending ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <DatabaseBackup size={15} aria-hidden />}
        {done !== null ? `Queued ${done} ${done === 1 ? "ingest" : "ingests"}` : "Re-ingest project"}
      </button>
      <span className="sr-only" aria-live="polite">
        {announce}
      </span>
      {open ? (
        <div
          ref={box}
          role="dialog"
          aria-modal="true"
          aria-labelledby="reingest-title"
          aria-describedby="reingest-body"
          className="mem-slide-in absolute right-0 top-full z-30 mt-2 w-[320px] rounded-card bg-white p-4 shadow-pop ring-1 ring-line"
        >
          <p id="reingest-title" className="text-[14px] font-semibold text-plum">
            Re-ingest this project?
          </p>
          <p id="reingest-body" className="mt-1 text-[13px] leading-relaxed text-slate">
            Every phase it has run is projected into memory again, as new revisions. Nothing already recorded is lost.
          </p>
          {reingest.error ? (
            <div className="mt-3" aria-live="assertive">
              <ErrorNote error={reingest.error} />
            </div>
          ) : null}
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className="btn-ghost" onClick={close}>
              Cancel
            </button>
            <button
              type="button"
              className="btn-primary py-1.5 text-[13.5px]"
              disabled={reingest.isPending}
              onClick={() =>
                reingest.mutate(undefined, {
                  onSuccess: (r) => {
                    const n = r.job_ids.length;
                    setDone(n);
                    setAnnounce(`Re-ingest queued: ${n} ${n === 1 ? "ingest" : "ingests"}.`);
                    close();
                  },
                })
              }
            >
              Re-ingest
            </button>
          </div>
        </div>
      ) : null}
    </div>
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
    set({ uid: null, rev: null, changes: null, q: null });
  }, [projectId, set]);

  // When the memory moves on (a new revision): the timeline always grows; the view is re-read
  // when it is the current one (an as-of view stays as it was).
  const revision = status.data?.revision ?? null;
  const seenRevision = useRef(revision);
  useEffect(() => {
    if (seenRevision.current === revision) return;
    seenRevision.current = revision;
    qc.invalidateQueries({ queryKey: ["memory-revisions", projectId] });
    if (view.rev === null) {
      for (const key of ["memory-graph", "memory-node", "memory-search"]) qc.invalidateQueries({ queryKey: [key, projectId] });
    }
  }, [revision, view.rev, projectId, qc]);

  const revisions = useRevisions(ready ? projectId : null);
  // A link to an older revision than the loaded pages reach: read back until it is there.
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = revisions;
  const oldestLoaded = revisions.revisions?.at(-1)?.n ?? null;
  useEffect(() => {
    if (view.rev === null || oldestLoaded === null || oldestLoaded <= view.rev) return;
    if (hasNextPage && !isFetchingNextPage) void fetchNextPage();
  }, [view.rev, oldestLoaded, hasNextPage, isFetchingNextPage, fetchNextPage]);
  const diff = useRevisionDiff(ready ? projectId : null, view.changes);

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
  // While a new focus loads, the canvas still shows the previous focus's graph
  // (keepPreviousData): pair it with the focus it was read around, not the one requested.
  const settledFocus = useRef<string | null>(focus);
  useEffect(() => {
    if (graph.data && !graph.isPlaceholderData) settledFocus.current = graphQuery.uid;
  });
  const canvasFocus = graph.isPlaceholderData ? settledFocus.current : focus;
  // The selected node's lineage chain, for the canvas highlight (lineage mode draws it already).
  const chain = useMemoryGraph(
    projectId,
    { uid: view.uid, depth: 1, labels: [], side: null, asOf: view.rev, mode: "lineage" },
    ready && mode === "neighbourhood" && !!view.uid,
  );
  const moreData = useQueries({
    combine: graphsOnly,
    queries: expanded.map((uid) => ({
      queryKey: ["memory-graph", projectId, { ...graphQuery, uid, depth: 1 as const, mode: "neighbourhood" as const }],
      queryFn: () => memoryApi.graph(projectId!, { ...graphQuery, uid, depth: 1, mode: "neighbourhood" }),
      enabled: ready && mode === "neighbourhood",
    })),
  });
  const merged = useMemo(
    () => (graph.data ? mergeGraphs(graph.data, mode === "neighbourhood" ? moreData : []) : null),
    [graph.data, mode, moreData],
  );

  // A diff is open: outline what it touched, and draw the nodes it removed from around here as
  // ghosts, read as of the revision before (only when the view is at or after that revision).
  const diffData = view.changes !== null ? diff.data : undefined;
  const highlight = useMemo(() => (diffData ? diffHighlight(diffData) : undefined), [diffData]);
  const ghostAt = diffData && (view.rev === null || view.rev >= diffData.revision.n) ? diffData.revision.n - 1 : null;
  const ghostUids = useMemo(
    () => (diffData && merged && ghostAt !== null && ghostAt >= 1 && mode === "neighbourhood" ? ghostCandidates(diffData, merged) : []),
    [diffData, merged, ghostAt, mode],
  );
  const ghosts = useQueries({
    combine: ghostsOnly,
    queries: ghostUids.map((uid) => ({
      queryKey: ["memory-ghost", projectId, uid, ghostAt],
      queryFn: () => memoryApi.node(projectId!, uid, ghostAt).then((d) => d.node),
      staleTime: Infinity,
      retry: 0,
    })),
  });
  const shown = useMemo(
    () => (merged && diffData ? withGhosts(merged, ghosts.filter((n): n is MemNodeOut => !!n), diffData) : merged),
    [merged, diffData, ghosts],
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

  // A diff item: a removed one is gone at the diff's revision, so it opens as of the one before.
  const pickFromDiff = (uid: string, removed: boolean) => {
    if (removed && view.changes !== null) set({ uid, rev: String(view.changes - 1) }, true);
    else select(uid);
  };
  const removedNote =
    diffData && view.uid && view.rev === diffData.revision.n - 1 && diffData.nodes.removed.some((v) => v.uid === view.uid)
      ? `Removed in revision ${diffData.revision.n}: shown as of revision ${view.rev}.`
      : null;

  // A node the view's revision does not hold (added later, or removed before): say so plainly.
  const missing = graph.error instanceof ApiError && graph.error.status === 404;
  const graphError = missing ? null : graph.error;
  const overview = () => {
    setFocus(null);
    setExpanded([]);
    set({ uid: null, mode: null }, true);
  };
  const counts = status.data?.counts ?? {};
  const projectReady = ready && projectId ? projectId : null;
  const focusNode = shown?.nodes.find((n) => n.uid === (mode === "lineage" ? view.uid : focus));

  return (
    <div className="flex h-[calc(100vh-15rem)] min-h-[780px] flex-col gap-6">
      <PageHeader
        back={{ href: "/", label: "Back to the migration" }}
        eyebrow="Project"
        title="Memory"
        description="The old code, the new code and what the team learnt, linked and versioned by revision."
        actions={
          <>
            <MemoryStatusPill status={status.data} />
            {projectReady ? <ReingestButton projectId={projectReady} /> : null}
            {ready ? (
              <button type="button" className="btn-secondary py-1.5 text-md" onClick={share} title="Copy a link to this view">
                <Share2 size={15} aria-hidden />
                {copied ? "Link copied" : "Share view"}
              </button>
            ) : null}
          </>
        }
      />

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
              onClear={() => set({ labels: null, side: null, phase: null })}
              counts={counts}
              hits={search.data ? rankHits(search.data, view.q) : undefined}
              searching={search.isFetching}
              error={search.error}
              selected={view.uid}
              onPick={pick}
            />

            <section className="card flex p-0 min-w-0 flex-1 flex-col overflow-hidden" aria-label="Memory graph">
              <div className="flex flex-wrap items-center gap-3 border-b border-line/80 px-5 py-3">
                <div className="min-w-[120px] flex-1">
                  <p className="label">{mode === "lineage" ? "Lineage of" : focus ? "Around" : "Project overview"}</p>
                  <p className="truncate text-base font-semibold text-heading">
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
                {missing ? (
                  <div className="flex h-full items-center justify-center p-6" data-testid="memory-node-missing">
                    <div className="max-w-sm text-center">
                      <p className="text-[15.5px] font-semibold text-plum">
                        {view.rev !== null ? `Not in the memory at revision ${view.rev}` : "Not in this project's memory"}
                      </p>
                      <p className="mt-1.5 text-[13.5px] leading-relaxed text-slate">
                        {view.rev !== null
                          ? "This node was recorded after that revision, or removed before it."
                          : "It may have been removed by a later revision."}
                      </p>
                      <div className="mt-4 flex justify-center gap-2">
                        {view.rev !== null ? (
                          <button type="button" className="btn-secondary py-1.5 text-[13.5px]" onClick={() => set({ rev: null })}>
                            See the current memory
                          </button>
                        ) : null}
                        <button type="button" className="btn-ghost text-[13.5px]" onClick={overview}>
                          Project overview
                        </button>
                      </div>
                    </div>
                  </div>
                ) : graphError ? (
                  <div className="p-6">
                    <ErrorNote error={graphError} />
                  </div>
                ) : shown ? (
                  <MemoryCanvas
                    graph={shown}
                    mode={mode}
                    selected={view.uid ?? undefined}
                    lineage={mode === "neighbourhood" ? chain.data : undefined}
                    focus={mode === "neighbourhood" ? canvasFocus : null}
                    filtered={filtered}
                    highlight={highlight}
                    onSelect={select}
                    onExpand={expand}
                  />
                ) : (
                  <p className="flex items-center gap-2 p-6 text-slate">
                    <Loader2 size={16} className="animate-spin" aria-hidden /> Laying out the graph…
                  </p>
                )}
                {view.changes !== null ? (
                  <DiffPanel
                    n={view.changes}
                    diff={diff.data}
                    loading={diff.isLoading}
                    error={diff.error}
                    selected={view.uid}
                    onSelect={pickFromDiff}
                    onClose={() => set({ changes: null })}
                  />
                ) : null}
              </div>
              <p className="border-t border-line/80 px-5 py-2 text-sm text-slate">
                Click a node to open it · double-click to {mode === "lineage" ? "explore around it" : "expand its neighbours"} · scroll to zoom
              </p>
            </section>

            {view.uid ? (
              <NodeDrawer
                projectId={projectId}
                uid={view.uid}
                asOf={view.rev}
                onClose={() => set({ uid: null, mode: null }, true)}
                onSelect={select}
                onFocus={focusOn}
                onBackToCurrent={() => set({ rev: null })}
                removedNote={removedNote}
              />
            ) : null}
          </div>

          <Timeline
            revisions={revisions.revisions ?? []}
            hasEarlier={!!hasNextPage}
            loadingEarlier={isFetchingNextPage}
            onLoadEarlier={() => void fetchNextPage()}
            value={view.rev}
            showing={view.changes}
            onChange={(n) => set({ rev: n === null ? null : String(n) })}
            onShowChanges={(n) => set({ changes: view.changes === n ? null : String(n) })}
          />
        </>
      )}
    </div>
  );
}

