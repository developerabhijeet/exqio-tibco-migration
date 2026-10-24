"use client";

import { useRef, useState } from "react";
import { FileUp, Library, Trash2, UploadCloud } from "lucide-react";
import { Panel } from "@/components/tabs/build/Panel";
import { RoleLock } from "@/components/shell/RoleNotice";
import { Pill } from "@/components/ui/Pill";
import { usePermission } from "@/lib/personas";
import { useAppState } from "@/lib/state";
import {
  ACCEPTED,
  MAX_BYTES,
  STANDARD_KINDS,
  formatSize,
  kindLabel,
  useStandards,
  type StandardKind,
} from "@/lib/standards";

const when = (iso: string) => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

/**
 * The architectural standards this migration has to fit: GMF's reference architecture, a blueprint,
 * an API or security standard. An architect attaches the documents here, beside the design record
 * they constrain.
 *
 * The file is read in the browser and its SHA-256 recorded, so the entry names the exact revision of
 * the document rather than a filename someone can change underneath it. The bytes stay on this
 * machine and the design generator does not read them yet, which the panel says plainly rather than
 * implying the design was generated against them.
 */
export function StandardsPanel() {
  const { projectId, user } = useAppState();
  const may = usePermission("design");
  const { docs, add, remove, ready } = useStandards(projectId);
  const input = useRef<HTMLInputElement>(null);
  const [kind, setKind] = useState<StandardKind>("reference_architecture");
  const [note, setNote] = useState("");
  const [rejected, setRejected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);

  const blocked = may.reason ?? (projectId ? null : "Open a project before attaching a standard.");

  const take = async (files: FileList | File[] | null) => {
    if (!files || blocked) return;
    setBusy(true);
    setRejected(await add(Array.from(files), kind, note, user));
    setNote("");
    setBusy(false);
    if (input.current) input.current.value = "";
  };

  return (
    <Panel
      title="Architectural standards"
      eyebrow="The documents this migration has to fit"
      testId="standards-panel"
      id="standards"
      actions={
        <>
          <Pill tone={docs.length > 0 ? "purple" : "det"}>
            {docs.length} attached
          </Pill>
          <span title={blocked ?? undefined}>
            <button
              type="button"
              className="btn-secondary"
              data-testid="standards-browse"
              disabled={!!blocked || busy}
              onClick={() => input.current?.click()}
            >
              <RoleLock reason={blocked ?? undefined} />
              <FileUp size={16} aria-hidden /> Browse files
            </button>
          </span>
        </>
      }
    >
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
        <div>
          <div className="grid gap-2.5">
            <label className="text-[13px]">
              <span className="label">Kind</span>
              <select
                className="select mt-1 w-full"
                data-testid="standards-kind"
                value={kind}
                disabled={!!blocked}
                onChange={(e) => setKind(e.target.value as StandardKind)}
              >
                {STANDARD_KINDS.map((k) => (
                  <option key={k.key} value={k.key}>
                    {k.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-[13px]">
              <span className="label">Note (optional)</span>
              <input
                className="input mt-1"
                data-testid="standards-note"
                placeholder="Which section applies, or why it is attached"
                value={note}
                disabled={!!blocked}
                onChange={(e) => setNote(e.target.value)}
              />
            </label>
          </div>

          <div
            onDragOver={(e) => {
              e.preventDefault();
              if (!blocked) setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setOver(false);
              void take(e.dataTransfer.files);
            }}
            data-testid="standards-dropzone"
            className={`mt-3 flex flex-col items-center justify-center rounded-card border-2 border-dashed px-4 py-6 text-center transition-colors ${
              blocked ? "border-line bg-warm/50 opacity-60" : over ? "border-purple bg-purple-lt" : "border-line bg-white"
            }`}
          >
            <UploadCloud size={28} className={blocked ? "text-slate" : "text-purple"} aria-hidden />
            <p className="mt-2 text-[13.5px] font-medium text-plum">Drop a spec or blueprint</p>
            <p className="mt-0.5 text-[12.5px] text-slate">
              PDF, Word, Markdown, diagrams, OpenAPI. Up to {formatSize(MAX_BYTES)} each.
            </p>
          </div>
          <input
            ref={input}
            type="file"
            multiple
            accept={ACCEPTED}
            className="sr-only"
            data-testid="standards-input"
            onChange={(e) => void take(e.target.files)}
          />
          {blocked ? <p className="mt-2 text-[12.5px] text-slate">{blocked}</p> : null}
          {rejected.length > 0 ? (
            <ul className="mt-2 space-y-1 rounded-btn bg-decision-bg px-3 py-2 text-[12.5px] text-ink" data-testid="standards-rejected">
              {rejected.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          ) : null}
        </div>

        <div>
          {!ready ? null : docs.length === 0 ? (
            <div className="flex h-full min-h-[180px] flex-col items-center justify-center rounded-card border border-dashed border-line bg-warm/40 px-6 py-8 text-center">
              <Library size={26} className="text-slate" aria-hidden />
              <p className="mt-2 font-medium text-plum">No standards attached</p>
              <p className="mt-1 max-w-[460px] text-[13px] text-slate">
                Attach GMF&apos;s reference architecture, a solution blueprint, or the API and security standards this
                process has to meet, so the design record is reviewed against them.
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-line rounded-card border border-line" data-testid="standards-list">
              {docs.map((d) => (
                <li key={d.id} className="row-hover flex flex-wrap items-start gap-x-3 gap-y-1 px-4 py-3" data-testid="standard-row">
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="truncate font-medium text-plum" title={d.name}>
                        {d.name}
                      </span>
                      <Pill tone="det">{kindLabel(d.kind)}</Pill>
                    </span>
                    {d.note ? <span className="mt-0.5 block text-[12.5px] text-ink">{d.note}</span> : null}
                    <span className="mt-0.5 block text-[12px] text-slate">
                      {formatSize(d.size)}
                      {d.type ? ` · ${d.type}` : ""} · attached by {d.addedBy} on {when(d.addedAt)}
                    </span>
                    <span className="mt-0.5 block truncate font-mono text-[11px] text-slate" title={d.sha256}>
                      sha256 {d.sha256.slice(0, 32)}
                      {d.sha256.length > 32 ? "…" : ""}
                    </span>
                  </span>
                  <span title={blocked ?? undefined}>
                    <button
                      type="button"
                      className="btn-ghost"
                      aria-label={`Remove ${d.name}`}
                      disabled={!!blocked}
                      onClick={() => remove(d.id)}
                    >
                      <Trash2 size={15} />
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <p className="mt-4 rounded-btn border border-line bg-page px-3 py-2 text-[12.5px] leading-relaxed text-slate">
        <span className="font-semibold text-plum">What this records.</span> The file is read in this browser and its
        SHA-256 taken, so the entry names the exact revision of the document, not just a filename. The contents stay on
        this machine: they are not uploaded, and the design generator does not read them yet, so attaching a standard
        does not change what a build produces. Use it to state which documents the design record is reviewed against at
        G3; the mapping, contracts and decisions below are what to review against them.
      </p>
    </Panel>
  );
}
