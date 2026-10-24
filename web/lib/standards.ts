"use client";

// Architectural standards an architect attaches to a project: the GMF reference architecture, a
// naming or layering standard, an API or security guideline, a blueprint the target has to fit.
//
// What this does and does not do, stated here because the screen says the same thing to the user:
// the file is read in the browser, its SHA-256 is computed, and the name, size, type and digest are
// recorded against the project. The bytes are not uploaded and the design generator does not read
// them yet, so attaching a standard records the intent and the exact document, and does not change
// what a build produces. The digest is the point: when the standards are wired into generation, the
// record here already says which revision of which document an architect meant.
import { useCallback, useEffect, useState } from "react";

export interface StandardDoc {
  id: string;
  name: string;
  /** Bytes, as the browser reported them. */
  size: number;
  /** The browser's media type, or "" when it could not tell. */
  type: string;
  /** SHA-256 of the file's bytes, lower-case hex: which revision of the document this is. */
  sha256: string;
  /** What kind of standard this is, chosen by the architect. */
  kind: StandardKind;
  /** The architect's note: which part applies, or why it is attached. */
  note: string;
  addedBy: string;
  addedAt: string;
}

export const STANDARD_KINDS = [
  { key: "reference_architecture", label: "Reference architecture" },
  { key: "blueprint", label: "Solution blueprint" },
  { key: "api_standard", label: "API or integration standard" },
  { key: "security_standard", label: "Security or compliance standard" },
  { key: "naming_standard", label: "Naming and layering standard" },
  { key: "other", label: "Other" },
] as const;

export type StandardKind = (typeof STANDARD_KINDS)[number]["key"];

export const ACCEPTED =
  ".pdf,.md,.markdown,.txt,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.json,.yaml,.yml,.xml,.drawio,.vsdx,.png,.jpg,.jpeg,.svg";

/** 25 MB: large enough for a blueprint deck, small enough to hash in the browser without a wait. */
export const MAX_BYTES = 25 * 1024 * 1024;

const key = (projectId: string) => `exiqo.standards.${projectId}`;

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 102.4) / 10} KB`;
  return `${Math.round(bytes / (1024 * 102.4)) / 10} MB`;
}

export function kindLabel(kind: string): string {
  return STANDARD_KINDS.find((k) => k.key === kind)?.label ?? kind;
}

/** SHA-256 of the file, or null where the browser gives no SubtleCrypto (an insecure origin). */
export async function digest(file: File): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null;
  try {
    const bytes = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return Array.from(new Uint8Array(bytes))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  } catch {
    return null;
  }
}

function read(projectId: string): StandardDoc[] {
  try {
    const raw = window.localStorage.getItem(key(projectId));
    const list: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? (list as StandardDoc[]) : [];
  } catch {
    return [];
  }
}

function write(projectId: string, docs: StandardDoc[]): void {
  try {
    window.localStorage.setItem(key(projectId), JSON.stringify(docs));
  } catch {
    /* storage unavailable or full: the list lasts for this page only */
  }
}

export interface StandardsStore {
  docs: StandardDoc[];
  /** Adds the files, returning the reason each rejected one was rejected. */
  add: (files: File[], kind: StandardKind, note: string, user: string) => Promise<string[]>;
  remove: (id: string) => void;
  ready: boolean;
}

/** The standards attached to one project, kept in this browser. */
export function useStandards(projectId: string | null): StandardsStore {
  const [docs, setDocs] = useState<StandardDoc[]>([]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setDocs(projectId ? read(projectId) : []);
    setReady(true);
  }, [projectId]);

  const add = useCallback(
    async (files: File[], kind: StandardKind, note: string, user: string): Promise<string[]> => {
      if (!projectId) return ["no project is open"];
      const rejected: string[] = [];
      const existing = read(projectId);
      const added: StandardDoc[] = [];
      for (const file of files) {
        if (file.size > MAX_BYTES) {
          rejected.push(`${file.name} is ${formatSize(file.size)}; the limit is ${formatSize(MAX_BYTES)}`);
          continue;
        }
        if (file.size === 0) {
          rejected.push(`${file.name} is empty`);
          continue;
        }
        const sha256 = await digest(file);
        // The same bytes under any name are the same document, so attaching it twice is a no-op.
        if (sha256 && [...existing, ...added].some((d) => d.sha256 === sha256)) {
          rejected.push(`${file.name} is already attached (same contents)`);
          continue;
        }
        added.push({
          id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
          name: file.name,
          size: file.size,
          type: file.type,
          sha256: sha256 ?? "not computed in this browser",
          kind,
          note: note.trim(),
          addedBy: user,
          addedAt: new Date().toISOString(),
        });
      }
      if (added.length > 0) {
        const next = [...added, ...existing];
        write(projectId, next);
        setDocs(next);
      }
      return rejected;
    },
    [projectId],
  );

  const remove = useCallback(
    (id: string) => {
      if (!projectId) return;
      const next = read(projectId).filter((d) => d.id !== id);
      write(projectId, next);
      setDocs(next);
    },
    [projectId],
  );

  return { docs, add, remove, ready };
}
