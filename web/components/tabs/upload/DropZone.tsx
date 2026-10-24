"use client";

import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileArchive, FolderOpen, Server, UploadCloud } from "lucide-react";
import { api } from "@/lib/api";
import { walkEntries, zipFiles } from "@/lib/zip";
import { RoleLock } from "@/components/shell/RoleNotice";

export type UploadSource =
  | { kind: "zip"; blob: Blob; name: string }
  | { kind: "folder"; files: { path: string; file: File }[]; name: string }
  | { kind: "path"; path: string };

function allowedRoots(settings: Record<string, unknown> | null | undefined): string[] | null {
  if (!settings) return null;
  const v = settings.upload_allowed_roots ?? settings.allowed_upload_roots ?? settings.allowed_roots;
  return Array.isArray(v) ? v.map(String) : null;
}

/** `blocked` is the persona model's reason this user may not upload; it disables every control here. */
export function DropZone({
  busy,
  blocked = null,
  onSource,
}: {
  busy: boolean;
  blocked?: string | null;
  onSource: (s: UploadSource) => void;
}) {
  const zipInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [path, setPath] = useState("");
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings, retry: false, staleTime: 60_000 });
  const roots = allowedRoots(settings.data);

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setOver(false);
    if (busy || blocked) return;
    const items = Array.from(e.dataTransfer.items ?? []);
    const entries = items.map((i) => i.webkitGetAsEntry?.()).filter((x): x is FileSystemEntry => !!x);
    if (entries.some((x) => x.isDirectory)) {
      const files = await walkEntries(entries);
      onSource({ kind: "folder", files, name: entries[0].name });
      return;
    }
    const file = e.dataTransfer.files[0];
    if (file) onSource({ kind: "zip", blob: file, name: file.name });
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_380px]">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        className={`flex flex-col items-center justify-center rounded-card border-2 border-dashed px-6 py-10 text-center transition-colors ${
          over ? "border-purple bg-purple-lt" : "border-line bg-white"
        }`}
      >
        <UploadCloud size={36} className="text-purple" aria-hidden />
        <p className="mt-3 font-medium text-plum">Drop a zip or a folder of TIBCO sources</p>
        <p className="text-[14px] text-slate">BW6 modules and applications, BW5 projects, schemas, resources, SQL</p>
        <div className="mt-4 flex gap-2">
          <span title={blocked ?? undefined}>
            <button
              type="button"
              className="btn-primary"
              disabled={busy || !!blocked}
              onClick={() => zipInput.current?.click()}
            >
              <RoleLock reason={blocked ?? undefined} />
              <FileArchive size={16} aria-hidden /> Choose zip
            </button>
          </span>
          <span title={blocked ?? undefined}>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy || !!blocked}
              onClick={() => folderInput.current?.click()}
            >
              <RoleLock reason={blocked ?? undefined} />
              <FolderOpen size={16} aria-hidden /> Choose folder
            </button>
          </span>
        </div>
        <input
          ref={zipInput}
          data-testid="zip-input"
          type="file"
          accept=".zip,application/zip"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onSource({ kind: "zip", blob: f, name: f.name });
            e.target.value = "";
          }}
        />
        <input
          ref={folderInput}
          data-testid="folder-input"
          type="file"
          multiple
          className="hidden"
          {...{ webkitdirectory: "", directory: "" }}
          onChange={(e) => {
            const list = Array.from(e.target.files ?? []);
            if (list.length) {
              const files = list.map((f) => ({ path: f.webkitRelativePath || f.name, file: f }));
              onSource({ kind: "folder", files, name: files[0].path.split("/")[0] });
            }
            e.target.value = "";
          }}
        />
      </div>
      <form
        className="card flex flex-col p-5"
        onSubmit={(e) => {
          e.preventDefault();
          if (path.trim()) onSource({ kind: "path", path: path.trim() });
        }}
      >
        <p className="label flex items-center gap-1.5">
          <Server size={14} aria-hidden /> Server path
        </p>
        <label htmlFor="server-path" className="sr-only">
          Server path
        </label>
        <input
          id="server-path"
          className="input mt-2 font-mono text-[13px]"
          placeholder="/srv/estates/gmf or /srv/estates/gmf.zip"
          value={path}
          onChange={(e) => setPath(e.target.value)}
        />
        <p className="mt-2 text-[13px] text-slate">
          {roots && roots.length > 0 ? (
            <>
              Allowed roots:{" "}
              {roots.map((r) => (
                <code key={r} className="mr-1 rounded bg-warm px-1 font-mono text-[12px]">
                  {r}
                </code>
              ))}
            </>
          ) : (
            <>
              A folder or zip the API server can read, under its allowed roots (UPLOAD_ALLOWED_ROOTS).
            </>
          )}
        </p>
        <div className="mt-auto pt-3">
          <button type="submit" className="btn-secondary w-full" disabled={busy || !path.trim()}>
            Use this path
          </button>
        </div>
      </form>
    </div>
  );
}

export async function sourceToUpload(projectId: string, s: UploadSource) {
  if (s.kind === "zip") return api.uploadZip(projectId, s.blob, s.name);
  if (s.kind === "path") return api.uploadPath(projectId, s.path);
  const blob = await zipFiles(s.files);
  return api.uploadZip(projectId, blob, `${s.name || "folder"}.zip`);
}
