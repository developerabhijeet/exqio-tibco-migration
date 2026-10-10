"use client";

import { ChevronRight, FileText, Folder } from "lucide-react";
import { buildTree, countsByKind, formatBytes, KIND_LABELS, type TreeDir } from "@/lib/files";
import type { FileEntry } from "@/lib/types";

function Dir({ dir, depth }: { dir: TreeDir; depth: number }) {
  return (
    <details open={depth < 1} className="group/dir">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded px-1 py-0.5 text-[14px] hover:bg-warm">
        <ChevronRight size={14} className="text-slate transition-transform group-open/dir:rotate-90" aria-hidden />
        <Folder size={14} className="text-purple" aria-hidden />
        <span className="font-medium text-plum">{dir.name}</span>
        <span className="text-[12px] text-slate">{dir.count}</span>
      </summary>
      <div className="ml-4 border-l border-line pl-2">
        {dir.dirs.map((d) => (
          <Dir key={d.path} dir={d} depth={depth + 1} />
        ))}
        {dir.files.map((f) => (
          <FileRow key={f.path} f={f} />
        ))}
      </div>
    </details>
  );
}

function FileRow({ f }: { f: FileEntry }) {
  const name = f.path.split("/").pop();
  const muted = f.kind === "other";
  return (
    <div className="flex items-center gap-1.5 px-1 py-0.5 text-[13px]">
      <FileText size={13} className={muted ? "text-line" : "text-slate"} aria-hidden />
      <span className={`truncate ${muted ? "text-slate" : "text-ink"}`}>{name}</span>
      <span className="ml-auto shrink-0 font-mono text-[11px] text-slate">
        {KIND_LABELS[f.kind] ?? f.kind}
        {f.dialect ? ` · ${f.dialect}` : ""} · {formatBytes(f.size)}
      </span>
    </div>
  );
}

export function FileTree({ files }: { files: FileEntry[] }) {
  const tree = buildTree(files);
  const counts = countsByKind(files);
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
          <Dir key={d.path} dir={d} depth={0} />
        ))}
        {tree.files.map((f) => (
          <FileRow key={f.path} f={f} />
        ))}
      </div>
    </section>
  );
}
