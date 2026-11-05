"use client";

// An edited copy of a build's download, uploaded back (POST /builds/{id}/edits, multipart `file`):
// the API compares it with the build it came from and opens an iteration of kind "edits". Nothing is
// built until the edit set is resumed (EditPreview).
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FileArchive, Loader2 } from "lucide-react";
import { uploadEdits, useIterationAccess } from "@/lib/iterations";
import { useAppState } from "@/lib/state";
import type { EditSetView } from "@/lib/types";
import { RoleLock } from "@/components/shell/RoleNotice";

function errorText(e: unknown): string {
  if (e && typeof e === "object" && "detail" in e && typeof (e as { detail: unknown }).detail === "string")
    return (e as { detail: string }).detail;
  return e instanceof Error ? e.message : String(e);
}

export function EditUpload({
  buildId,
  finished,
  onUploaded,
}: {
  buildId: string;
  finished: boolean;
  onUploaded: (editSet: EditSetView) => void;
}) {
  const qc = useQueryClient();
  const { projectId } = useAppState();
  const access = useIterationAccess("edits");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reason = access.reason ?? (finished ? undefined : "The build has not finished");

  const pick = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const row = await uploadEdits(buildId, file);
      qc.setQueryData(["edit-set", row.id], row);
      qc.invalidateQueries({ queryKey: ["iterations", projectId] });
      onUploaded(row);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-1.5">
      <input
        id="edit-drop"
        type="file"
        accept=".zip,application/zip"
        data-testid="edit-drop"
        className="peer sr-only"
        disabled={!!reason || busy}
        aria-describedby={reason ? "edit-drop-reason" : undefined}
        onChange={(e) => {
          void pick(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      <label
        htmlFor="edit-drop"
        title={reason}
        className={`flex items-center gap-3 peer-focus-visible:ring-2 peer-focus-visible:ring-accent/60 rounded-card border border-dashed px-4 py-3 text-md ${
          reason || busy ? "border-line bg-sunken/60 text-slate" : "cursor-pointer border-accent/50 bg-accent-soft/40 text-heading hover:border-accent"
        }`}
      >
        {busy ? <Loader2 size={18} className="animate-spin" aria-hidden /> : <FileArchive size={18} aria-hidden />}
        <RoleLock reason={access.reason} />
        <span>
          <span className="font-semibold">Upload an edited copy</span>
          <span className="block text-sm text-slate">
            The zip this build downloads, with your changes in it. You see exactly what changed before anything is built.
          </span>
        </span>
      </label>
      {reason ? (
        <p id="edit-drop-reason" className="text-sm text-slate">
          {reason}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="rounded-btn border border-error/30 bg-error-bg px-3 py-2 text-sm text-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
