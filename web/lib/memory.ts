"use client";

// The migration memory (the /memory API): types mirroring engine/memory/model.py and the API's
// schemas, the label groups the explorer filters and colours by, and the read hooks. Every read
// takes the project from the path; `asOf` reads the graph as of a revision (current when null).
import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { json } from "./api";

export type Side = "old" | "new" | "knowledge";
export type GraphMode = "neighbourhood" | "lineage";
export type EdgeStatus = "generated" | "corrected" | "rejected";

export interface MemoryStatus {
  configured: boolean;
  available: boolean;
  revision: number | null;
  at: string | null;
  phase: string | null;
  counts: Record<string, number>;
  pending_ingests: number;
  embedding_backend: string | null;
}

export interface Citation {
  uid: string;
  label: string;
  phase: string;
  revision: number;
  artifact_type: string;
  artifact_id: string | null;
  process_id: string | null;
  path: string | null;
}

export interface MemoryHit {
  uid: string;
  label: string;
  kind: string | null;
  text: string;
  score: number;
  keyword_score: number;
  vector_score: number;
  citation: Citation;
  props: Record<string, unknown>;
}

export interface MemNodeOut {
  uid: string;
  label: string;
  name: string;
  side: Side;
  props: Record<string, unknown>;
  valid_from: number;
  /** null while the version is current. */
  valid_to: number | null;
  curated: boolean;
  obsolete: boolean;
  text: string;
}

export interface EdgeOut {
  uid: string;
  type: string;
  src: string;
  dst: string;
  props: Record<string, unknown>;
  /** MIGRATED_TO only. */
  status: EdgeStatus | null;
}

export interface Subgraph {
  nodes: MemNodeOut[];
  edges: EdgeOut[];
  truncated: boolean;
  total: number;
}

export interface VersionOut {
  valid_from: number;
  valid_to: number | null;
  phase: string;
  actor: string | null;
  at: string | null;
  props: Record<string, unknown>;
  changed_keys: string[];
}

export interface LineageOut {
  edge: EdgeOut;
  other: MemNodeOut;
  direction: "in" | "out";
}

export interface NodeDetail {
  node: MemNodeOut;
  /** Newest first. */
  versions: VersionOut[];
  /** Knowledge ABOUT the node, newest first. */
  about: MemNodeOut[];
  lineage: LineageOut[];
  curations: MemNodeOut[];
}

export interface RevisionOut {
  n: number;
  phase: string;
  scopes: string[];
  actor: string;
  at: string;
  job_id: string | null;
  iteration: number;
  added: number;
  changed: number;
  removed: number;
}

export interface VersionRef {
  uid: string;
  /** Node label or relationship type. */
  kind: string;
  name: string;
  content_hash: string;
  props: Record<string, unknown>;
  text_hash?: string | null;
}

export interface ChangedRef extends VersionRef {
  changed_keys: string[];
}

export interface DiffSet {
  added: VersionRef[];
  changed: ChangedRef[];
  removed: VersionRef[];
}

export interface RevisionDiff {
  revision: RevisionOut;
  nodes: DiffSet;
  relationships: DiffSet;
}

// The three groups of spec section 1 (engine/memory/model.py OLD_LABELS, NEW_LABELS,
// KNOWLEDGE_GROUP). Endpoint is in both code groups: its `side` says which one a node is.
export const LABEL_GROUPS = {
  old: ["Domain", "Application", "Process", "Activity", "Contract", "SharedResource", "SubstVar", "Table", "Queue", "Topic", "Endpoint"],
  new: ["Solution", "CodeProject", "CodeFile", "Class", "Method", "ConfigKey", "MessagingEntity", "Test", "Endpoint"],
  knowledge: ["Chunk", "Memory", "GateDecision", "Error", "Curation"],
} as const satisfies Record<Side, readonly string[]>;

export const GROUP_LABEL: Record<Side, string> = { old: "Old code", new: "New code", knowledge: "Knowledge" };

/** The colour tokens (lib/theme.css) per group: old code the accent, new code the AI tone,
 * knowledge the decision tone; `strong` is the picked node's fill and a chip's text. */
export const GROUP_TOKEN: Record<Side, { fill: string; border: string; strong: string }> = {
  old: { fill: "accent-soft", border: "accent-ink", strong: "heading" },
  new: { fill: "ai-bg", border: "ai", strong: "ai" },
  knowledge: { fill: "decision-bg", border: "decision", strong: "decision" },
};

/** A curation (POST /memory/{p}/curations): `reason` is always required (1 to 2000 characters);
 * `text` for a note, `replacement_uid` (a Method or Class) for a corrected mapping. */
export type CurationAction = "note" | "reject_mapping" | "correct_mapping" | "obsolete";

export interface CurationIn {
  action: CurationAction;
  target_uid: string;
  reason: string;
  text?: string | null;
  replacement_uid?: string | null;
}

export interface CurationOut {
  event_id: string;
  job_id: string;
}

/** The ingest scope a curation lands under: the revision whose scopes hold it applied it. */
export const curationScope = (eventId: string) => `curation:${eventId}`;

/** A group colour as a CSS colour over its token, for inline styles (follows the theme). */
export function groupCss(side: Side, part: "fill" | "border" | "strong", alpha?: number): string {
  const v = `var(--c-${GROUP_TOKEN[side][part]})`;
  return alpha === undefined ? `rgb(${v})` : `rgb(${v} / ${alpha})`;
}

export const PHASES = ["discover", "analysis", "design", "build", "verify", "deploy", "gate", "curation"] as const;

/** One colour token (lib/theme.css) per phase, with an optional alpha, for the timeline's ticks
 * and legend; readable on the surface in both themes. */
export const PHASE_TOKEN: Record<string, [token: string, alpha?: number]> = {
  seed: ["slate", 0.6],
  discover: ["heading"],
  analysis: ["accent-ink"],
  design: ["accent"],
  build: ["ai"],
  verify: ["gate"],
  deploy: ["ai", 0.55],
  gate: ["decision"],
  decision: ["decision", 0.6],
  curation: ["coral-dk"],
};
/** A phase's colour as a CSS colour over its token, for inline styles (follows the theme). */
export function phaseCss(phase: string): string {
  const [token, alpha] = PHASE_TOKEN[phase] ?? ["slate"];
  return alpha === undefined ? `rgb(var(--c-${token}))` : `rgb(var(--c-${token}) / ${alpha})`;
}

/** A relationship's uid is `TYPE:src->dst` (engine/memory/model.py). */
export function parseRelUid(uid: string): { type: string; src: string; dst: string } | null {
  const colon = uid.indexOf(":");
  const arrow = uid.indexOf("->", colon + 1);
  if (colon < 1 || arrow < 0) return null;
  return { type: uid.slice(0, colon), src: uid.slice(colon + 1, arrow), dst: uid.slice(arrow + 2) };
}

export interface SearchFilters {
  labels: string[];
  side: Side | null;
  phase: string | null;
  asOf: number | null;
}

export interface GraphQuery {
  uid: string | null;
  depth: 1 | 2;
  labels: string[];
  side: Side | null;
  asOf: number | null;
  mode: GraphMode;
}

/** A query string; arrays repeat their key (FastAPI's list query parameters). */
function qs(params: Record<string, string | number | readonly string[] | null | undefined>): string {
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => sp.append(key, v));
    else sp.set(key, String(value));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

const base = (projectId: string) => `/memory/${encodeURIComponent(projectId)}`;

export const memoryApi = {
  status: (projectId: string) => json<MemoryStatus>(`${base(projectId)}/status`),
  search: (projectId: string, q: string, f: SearchFilters, k = 20) =>
    json<{ hits: MemoryHit[] }>(
      `${base(projectId)}/search${qs({ q, labels: f.labels, side: f.side, phase: f.phase, as_of: f.asOf, k })}`,
    ).then((r) => r.hits),
  graph: (projectId: string, g: GraphQuery) =>
    json<Subgraph>(
      `${base(projectId)}/graph${qs({
        uid: g.uid,
        depth: g.mode === "neighbourhood" ? g.depth : null,
        labels: g.mode === "neighbourhood" ? g.labels : [],
        side: g.mode === "neighbourhood" ? g.side : null,
        as_of: g.asOf,
        mode: g.mode,
      })}`,
    ),
  node: (projectId: string, uid: string, asOf: number | null) =>
    json<NodeDetail>(`${base(projectId)}/node${qs({ uid, as_of: asOf })}`),
  /** Newest first; `iteration` keeps only that iteration's revisions (sub-project 6). */
  revisions: (projectId: string, before?: number, limit = 200, iteration?: number) =>
    json<{ revisions: RevisionOut[] }>(`${base(projectId)}/revisions${qs({ before, limit, iteration })}`).then((r) => r.revisions),
  diff: (projectId: string, n: number) => json<RevisionDiff>(`${base(projectId)}/revisions/${n}/diff`),
  curate: (projectId: string, body: CurationIn) =>
    json<CurationOut>(`${base(projectId)}/curations`, { method: "POST", body: JSON.stringify(body) }),
  reingest: (projectId: string) => json<{ job_ids: string[] }>(`${base(projectId)}/reingest`, { method: "POST" }),
};

/** Polls while ingests are queued or the project has no revision yet, so the header catches up. */
export const useMemoryStatus = (projectId: string | null) =>
  useQuery({
    queryKey: ["memory-status", projectId],
    queryFn: () => memoryApi.status(projectId!),
    enabled: !!projectId,
    refetchInterval: (q) => {
      const s = q.state.data;
      if (!s || !s.configured) return false;
      return !s.available || s.pending_ingests > 0 || s.revision === null ? 4000 : 30_000;
    },
  });

export const useMemorySearch = (projectId: string | null, q: string, filters: SearchFilters) =>
  useQuery({
    queryKey: ["memory-search", projectId, q, filters],
    queryFn: () => memoryApi.search(projectId!, q, filters),
    enabled: !!projectId && q.trim().length > 0,
    placeholderData: keepPreviousData,
  });

export const useMemoryGraph = (projectId: string | null, g: GraphQuery, enabled = true) =>
  useQuery({
    queryKey: ["memory-graph", projectId, g],
    queryFn: () => memoryApi.graph(projectId!, g),
    enabled: enabled && !!projectId && (g.mode === "neighbourhood" || !!g.uid),
    placeholderData: keepPreviousData,
  });

export const useMemoryNode = (projectId: string | null, uid: string | null, asOf: number | null) =>
  useQuery({
    queryKey: ["memory-node", projectId, uid, asOf],
    queryFn: () => memoryApi.node(projectId!, uid!, asOf),
    enabled: !!projectId && !!uid,
  });

/** Revisions per page (the API's maximum). */
export const REVISIONS_PAGE = 200;

/** The revisions, newest first, a page at a time: `fetchNextPage` reads the page before the
 * oldest one loaded (`before`). `revisions` is every loaded page, newest first. With `iteration`
 * only that iteration's revisions (the API's `?iteration=`). */
export function useRevisions(projectId: string | null, iteration: number | null = null) {
  const q = useInfiniteQuery({
    queryKey: ["memory-revisions", projectId, iteration],
    queryFn: ({ pageParam }) =>
      memoryApi.revisions(projectId!, pageParam ?? undefined, REVISIONS_PAGE, iteration ?? undefined),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => (last.length === REVISIONS_PAGE && last[last.length - 1].n > 1 ? last[last.length - 1].n : undefined),
    enabled: !!projectId,
  });
  return { ...q, revisions: q.data?.pages.flat() };
}

/**
 * The revision that applied an ingest scope (a curation's `curation:<id>`), searching every
 * revision after `base` a page at a time, so it is found however far back the loaded timeline
 * reaches. null until it lands. Re-read whenever `latest` (the status revision) moves on.
 */
export function useRevisionOfScope(projectId: string, scope: string | null, base: number | null, latest: number | null) {
  return useQuery({
    queryKey: ["memory-scope-revision", projectId, scope, base, latest],
    enabled: !!scope && latest !== null && latest > (base ?? 0),
    staleTime: Infinity,
    queryFn: async () => {
      for (let from = (base ?? 0) + 1; from <= latest!; from += REVISIONS_PAGE) {
        const before = Math.min(from + REVISIONS_PAGE, latest! + 1);
        const page = await memoryApi.revisions(projectId, before, REVISIONS_PAGE);
        const hit = page.find((r) => r.scopes.includes(scope!));
        if (hit) return hit.n;
      }
      return null;
    },
  });
}

export const useRevisionDiff = (projectId: string | null, n: number | null) =>
  useQuery({
    queryKey: ["memory-diff", projectId, n],
    queryFn: () => memoryApi.diff(projectId!, n!),
    enabled: !!projectId && n !== null,
    staleTime: Infinity,
  });

/** Submits a curation. It shows once an ingest revision applies it, so the status is re-read at
 * once: its pending count switches the status poll to the fast interval. */
export function useCurate(projectId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CurationIn) => memoryApi.curate(projectId!, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memory-status", projectId] }),
  });
}

/** Admin only: queues a fresh ingest of every phase the project has run. */
export function useReingest(projectId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => memoryApi.reingest(projectId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memory-status", projectId] }),
  });
}

/** What a node is called on screen: its name, else the last segment of its uid. */
export function displayName(node: { name?: string; uid: string }): string {
  if (node.name) return node.name;
  const tail = node.uid.replace(/^[A-Za-z_]+:/, "");
  return tail.split("/").pop() || tail;
}

/** A search hit's title: code nodes lead their text with their name ("Name (app/module), ..."). */
export function hitTitle(hit: MemoryHit): string {
  const group = sideOfLabel(hit.label, hit.props);
  if (group !== "knowledge" && hit.text) {
    const lead = hit.text.split(/ \(|, |: /)[0].trim();
    if (lead && lead.length <= 80) return lead;
  }
  if (hit.kind) return `${hit.label} · ${hit.kind}`;
  return displayName({ uid: hit.uid });
}

/** The API's ranking, except that a node whose name is exactly the query comes first: someone
 * typing a process name wants that process, not the steps that mention it. */
export function rankHits(hits: MemoryHit[], q: string): MemoryHit[] {
  const want = q.trim().toLowerCase();
  const exact = (h: MemoryHit) => hitTitle(h).toLowerCase() === want;
  return [...hits.filter(exact), ...hits.filter((h) => !exact(h))];
}

export function sideOfLabel(label: string, props?: Record<string, unknown>): Side {
  if (label === "Endpoint") return props?.side === "new" ? "new" : "old";
  if ((LABEL_GROUPS.knowledge as readonly string[]).includes(label)) return "knowledge";
  if ((LABEL_GROUPS.new as readonly string[]).includes(label)) return "new";
  return "old";
}

/** "just now", "3 min ago", "2 h ago", "4 d ago", else the date. */
export function relativeTime(iso: string | null, now = Date.now()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 14) return `${d} d ago`;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
