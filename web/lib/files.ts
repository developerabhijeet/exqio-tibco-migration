// File inventory helpers for the Upload tab.
import type { FileEntry } from "./types";

export const KIND_LABELS: Record<string, string> = {
  bwp: ".bwp",
  process: ".process",
  bwm: ".bwm",
  xsd: ".xsd",
  wsdl: ".wsdl",
  json: ".json schema",
  substvar: ".substvar",
  resource: "shared resources",
  sql: "SQL",
  manifest: "MANIFEST.MF",
  application: ".application",
  other: "not parsed",
};

export const KIND_ORDER = Object.keys(KIND_LABELS);

/** Kinds the scanner reads; anything else is listed as not parsed. */
export const PARSED_KINDS = new Set(KIND_ORDER.filter((k) => k !== "other"));

export function countsByKind(files: FileEntry[]): { kind: string; label: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const f of files) counts.set(f.kind, (counts.get(f.kind) ?? 0) + 1);
  return [...counts.entries()]
    .map(([kind, count]) => ({ kind, label: KIND_LABELS[kind] ?? kind, count }))
    .sort((a, b) => {
      const ia = KIND_ORDER.indexOf(a.kind);
      const ib = KIND_ORDER.indexOf(b.kind);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
}

/** Packaged copies flagged by the scanner, plus parseable files with identical content. */
export function duplicates(files: FileEntry[]): { path: string; of: string }[] {
  const out: { path: string; of: string }[] = [];
  const seen = new Map<string, string>();
  for (const f of files) {
    if (f.duplicate_of) {
      out.push({ path: f.path, of: f.duplicate_of });
      continue;
    }
    if (!PARSED_KINDS.has(f.kind) || f.size === 0) continue;
    const first = seen.get(f.sha256);
    if (first) out.push({ path: f.path, of: first });
    else seen.set(f.sha256, f.path);
  }
  return out;
}

export interface TreeDir {
  name: string;
  path: string;
  dirs: TreeDir[];
  files: FileEntry[];
  count: number;
}

export function buildTree(files: FileEntry[]): TreeDir {
  const root: TreeDir = { name: "", path: "", dirs: [], files: [], count: 0 };
  for (const f of files) {
    const parts = f.path.split("/");
    let cur = root;
    cur.count++;
    for (const part of parts.slice(0, -1)) {
      let next = cur.dirs.find((d) => d.name === part);
      if (!next) {
        next = { name: part, path: cur.path ? `${cur.path}/${part}` : part, dirs: [], files: [], count: 0 };
        cur.dirs.push(next);
      }
      next.count++;
      cur = next;
    }
    cur.files.push(f);
  }
  const sort = (d: TreeDir) => {
    d.dirs.sort((a, b) => a.name.localeCompare(b.name));
    d.files.sort((a, b) => a.path.localeCompare(b.path));
    d.dirs.forEach(sort);
  };
  sort(root);
  return root;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** The file part of a report path such as `a/b.bwp#/bpws:process[1]/...`. */
export function reportFile(path: unknown): string {
  return typeof path === "string" ? path.split("#")[0] : "";
}
