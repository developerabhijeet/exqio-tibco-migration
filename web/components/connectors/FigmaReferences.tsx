"use client";

// Design tab: Figma frames attached to the process as design references. The designer reads their
// text on the next design run; everyone sees them here with their thumbnails. An architect or
// admin browses a file's frames (GET /figma/files/{key}/frames) and attaches a selection
// (POST /processes/{id}/figma/frames, a `connector.figma.sync` job). Hidden when Figma is off.
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Frame, Loader2, Paperclip, Search } from "lucide-react";
import { api } from "@/lib/api";
import { useConnectors } from "@/lib/connectors";
import { useJob } from "@/lib/jobs";
import { actionError, useActionAccess } from "@/lib/publish";
import type { FigmaFrame, FigmaFrameLink } from "@/lib/types";
import { RoleLock } from "@/components/shell/RoleNotice";
import { Pill } from "@/components/ui/Pill";
import { ActionStatus } from "./LinkChips";

const FILE_KEY = /^[A-Za-z0-9_-]{1,64}$/;
const MAX_FRAMES = 20;

function Thumb({ frame }: { frame: FigmaFrameLink }) {
  const image = useQuery({
    queryKey: ["figma-image", frame.id, frame.updated_at],
    queryFn: () => api.figmaFrameImage(frame.id),
    enabled: frame.has_image,
    staleTime: 300_000,
  });
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    if (!image.data) return;
    const url = URL.createObjectURL(image.data);
    setSrc(url);
    return () => URL.revokeObjectURL(url);
  }, [image.data]);
  if (!frame.has_image) {
    return (
      <span className="flex h-16 w-24 shrink-0 items-center justify-center rounded-btn border border-dashed border-line text-[11px] text-slate" title={frame.image_note ?? undefined}>
        no thumbnail
      </span>
    );
  }
  return src ? (
    // eslint-disable-next-line @next/next/no-img-element -- a blob URL of a stored thumbnail
    <img src={src} alt={`Figma frame ${frame.name}`} className="h-16 w-24 shrink-0 rounded-btn border border-line object-cover" />
  ) : (
    <span className="h-16 w-24 shrink-0 rounded-btn border border-line bg-warm" aria-hidden />
  );
}

function summary(result: Record<string, unknown>): string | null {
  const frames = Array.isArray(result.frames) ? result.frames.length : 0;
  return frames ? `${frames} frame(s) attached, ${String(result.thumbnails ?? 0)} with thumbnails` : null;
}

export function FigmaReferences({ processId }: { processId: string }) {
  const flags = useConnectors();
  const access = useActionAccess("figma");
  const on = !!flags.data?.figma.enabled;
  const attached = useQuery({
    queryKey: ["figma-frames", processId],
    queryFn: () => api.figmaFrames(processId),
    enabled: on,
  });
  const { stream, watch } = useJob("figma", processId);
  const [fileKey, setFileKey] = useState("");
  const [browsed, setBrowsed] = useState<{ key: string; frames: FigmaFrame[] } | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<"browse" | "attach" | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!on) return null;
  const keyOk = FILE_KEY.test(fileKey.trim());
  const running = stream?.status === "running";
  const attachedIds = new Set((attached.data ?? []).map((f) => `${f.file_key}:${f.node_id}`));

  const browse = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!keyOk) return;
    setBusy("browse");
    setError(null);
    try {
      const key = fileKey.trim();
      setBrowsed({ key, frames: await api.figmaFileFrames(key) });
      setPicked(new Set());
    } catch (err) {
      setBrowsed(null);
      setError(actionError(err));
    } finally {
      setBusy(null);
    }
  };

  const attach = async () => {
    if (!browsed || picked.size === 0) return;
    setBusy("attach");
    setError(null);
    try {
      const { job_id } = await api.attachFigmaFrames(processId, browsed.key, [...picked]);
      watch(job_id);
      setPicked(new Set());
    } catch (err) {
      setError(actionError(err));
    } finally {
      setBusy(null);
    }
  };

  const toggle = (node: string, checked: boolean) =>
    setPicked((p) => {
      const next = new Set(p);
      if (checked) next.add(node);
      else if (next.has(node)) next.delete(node);
      return next;
    });

  return (
    <section className="card space-y-3 p-4 print:hidden" data-testid="figma-references" aria-labelledby="figma-references-title">
      <div>
        <h3 id="figma-references-title" className="text-[16px] font-semibold text-plum">
          Figma references
        </h3>
        <p className="text-[12.5px] text-slate">
          Frames attached here are design references: the designer reads their text on the next design run.
        </p>
      </div>

      {attached.error ? <p role="alert" className="text-[13px] text-error">{actionError(attached.error)}</p> : null}
      {(attached.data ?? []).length === 0 ? (
        <p className="text-[13px] text-slate">{attached.isLoading ? "Loading." : "No frames attached."}</p>
      ) : (
        <ul className="space-y-2" data-testid="figma-attached">
          {(attached.data ?? []).map((f) => (
            <li key={f.id} className="flex items-start gap-3 text-[13px]" data-testid={`figma-attached-${f.node_id}`}>
              <Thumb frame={f} />
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-1.5">
                  {f.url ? (
                    <a href={f.url} target="_blank" rel="noopener noreferrer" className="font-medium text-purple-dk underline-offset-2 hover:underline">
                      {f.name}
                    </a>
                  ) : (
                    <span className="font-medium text-plum">{f.name}</span>
                  )}
                  {f.page ? <span className="text-slate">{f.page}</span> : null}
                  {f.stale ? <Pill tone="decision">gone from Figma</Pill> : null}
                </span>
                {f.text ? <span className="mt-0.5 line-clamp-2 block text-slate">{f.text}</span> : null}
              </span>
            </li>
          ))}
        </ul>
      )}

      <form className="flex flex-wrap items-end gap-2" onSubmit={browse}>
        <label className="text-[13px]">
          <span className="label">Figma file key</span>
          <input
            className="input mt-1 w-[260px] max-w-full font-mono"
            data-testid="figma-file"
            value={fileKey}
            onChange={(e) => setFileKey(e.target.value)}
            placeholder="the key in figma.com/file/<key>/…"
            disabled={!access.allowed}
            aria-invalid={fileKey.trim() !== "" && !keyOk}
          />
        </label>
        <span title={access.reason}>
          <button type="submit" className="btn-secondary" data-testid="figma-browse" disabled={!access.allowed || !keyOk || busy !== null}>
            {busy === "browse" ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Search size={16} aria-hidden />}
            <RoleLock reason={access.reason} />
            Browse frames
          </button>
        </span>
      </form>
      {access.reason ? <p className="text-[12.5px] text-slate">{access.reason}</p> : null}

      {browsed ? (
        browsed.frames.length === 0 ? (
          <p className="text-[13px] text-slate">File {browsed.key} has no frames.</p>
        ) : (
          <div className="space-y-2">
            <ul className="max-h-[260px] divide-y divide-line overflow-y-auto rounded-btn border border-line text-[13px]">
              {browsed.frames.map((fr) => {
                const already = attachedIds.has(`${browsed.key}:${fr.node_id}`);
                const full = picked.size >= MAX_FRAMES && !picked.has(fr.node_id);
                return (
                  <li key={fr.node_id} className="flex items-center gap-2 px-3 py-1.5">
                    <input
                      id={`figma-frame-${fr.node_id}`}
                      type="checkbox"
                      data-testid={`figma-frame-${fr.node_id}`}
                      checked={picked.has(fr.node_id)}
                      disabled={full || running}
                      title={full ? `At most ${MAX_FRAMES} frames at once` : undefined}
                      onChange={(e) => toggle(fr.node_id, e.target.checked)}
                    />
                    <Frame size={13} aria-hidden className="text-slate" />
                    <label htmlFor={`figma-frame-${fr.node_id}`} className="min-w-0 flex-1 truncate">
                      {fr.name} <span className="text-slate">{fr.page}</span>
                    </label>
                    {already ? <Pill tone="gate">attached: refresh</Pill> : null}
                  </li>
                );
              })}
            </ul>
            <button
              type="button"
              className="btn-primary"
              data-testid="figma-attach"
              disabled={picked.size === 0 || busy !== null || running}
              onClick={attach}
            >
              {busy === "attach" || running ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Paperclip size={16} aria-hidden />}
              Attach {picked.size || ""} frame{picked.size === 1 ? "" : "s"}
            </button>
          </div>
        )
      ) : null}
      <ActionStatus stream={stream} error={error} testId="figma-attach" summary={summary} />
    </section>
  );
}
