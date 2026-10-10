"use client";

import { useEffect, useRef } from "react";
import { ChevronRight, FileText, Folder } from "lucide-react";
import { buildTree, countsByKind, formatBytes, KIND_LABELS, type TreeDir } from "@/lib/files";
import type { FileEntry } from "@/lib/types";

/** Optional: rows that open a file (the Build tab opens TIBCO process sources). */
interface OpenProps {
  onOpen?: (f: FileEntry) => void;
  canOpen?: (f: FileEntry) => boolean;
  selected?: string | null;
}

function Dir({ dir, depth, open }: { dir: TreeDir; depth: number; open: OpenProps }) {
  const holdsSelected = !!open.selected && open.selected.startsWith(`${dir.path}/`);
  // Opened when it comes to hold the selected file; never closed for the reader.
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    if (holdsSelected && ref.current) ref.current.open = true;
  }, [holdsSelected]);
  return (
    <details ref={ref} open={depth < 1}>
      <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded px-1 py-0.5 text-[14px] hover:bg-warm">
        <ChevronRight size={14} className="text-slate transition-transform [details[open]>summary>&]:rotate-90" aria-hidden />
        <Folder size={14} className="text-purple" aria-hidden />
        <span className="font-medium text-plum">{dir.name}</span>
        <span className="text-[12px] text-slate">{dir.count}</span>
      </summary>
      <div className="ml-4 border-l border-line pl-2">
        {dir.dirs.map((d) => (
          <Dir key={d.path} dir={d} depth={depth + 1} open={open} />
        ))}
        {dir.files.map((f) => (
          <FileRow key={f.path} f={f} open={open} />
        ))}
      </div>
    </details>
  );
}

function FileRow({ f, open }: { f: FileEntry; open: OpenProps }) {
  const name = f.path.split("/").pop();
  const muted = f.kind === "other";
  const body = (
    <>
      <FileText size={13} className={muted ? "text-line" : "text-slate"} aria-hidden />
      <span className={`truncate ${muted ? "text-slate" : "text-ink"}`}>{name}</span>
      <span className="ml-auto shrink-0 font-mono text-[11px] text-slate">
        {KIND_LABELS[f.kind] ?? f.kind}
        {f.dialect ? ` · ${f.dialect}` : ""} · {formatBytes(f.size)}
      </span>
    </>
  );
  if (open.onOpen && (open.canOpen?.(f) ?? true)) {
    const active = open.selected === f.path;
    return (
      <button
        type="button"
        data-testid="source-file"
        aria-current={active ? "true" : undefined}
        title={`Open ${f.path}`}
        onClick={() => open.onOpen?.(f)}
        className={`flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left text-[13px] ${
          active ? "bg-purple-lt font-semibold" : "hover:bg-warm"
        }`}
      >
        {body}
      </button>
    );
  }
  return <div className="flex items-center gap-1.5 px-1 py-0.5 text-[13px]">{body}</div>;
}

export function FileTree({ files, onOpen, canOpen, selected }: { files: FileEntry[] } & OpenProps) {
  const tree = buildTree(files);
  const counts = countsByKind(files);
  const open: OpenProps = { onOpen, canOpen, selected };
  return (
    <section className="card p-5" data-testid="file-tree">
      <div className="flex items-center justify-between">
        <h3 className="panel-title">Files</h3>
        <span className="text-[14px] text-slate">{files.length} files</span>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {counts.map((c) => (
          <span
            key={c.kind}
            className={`inline-flex items-center gap-1.5 rounded-btn border px-2 py-0.5 text-[13px] ${
              c.kind === "other" ? "border-line bg-page text-slate" : "border-purple-lt bg-purple-lt text-plum"
            }`}
          >
            <span className="font-mono">{c.label}</span>
            <span className="font-semibold">{c.count}</span>
          </span>
        ))}
      </div>
      <div className="mt-4 max-h-[380px] overflow-y-auto pr-1">
        {tree.dirs.map((d) => (
          <Dir key={d.path} dir={d} depth={0} open={open} />
        ))}
        {tree.files.map((f) => (
          <FileRow key={f.path} f={f} open={open} />
        ))}
      </div>
    </section>
  );
}
