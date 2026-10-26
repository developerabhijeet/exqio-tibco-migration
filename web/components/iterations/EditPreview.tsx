"use client";

// What an edit set changed against its baseline build, file by file, and where the iteration
// resumes: a build that applies the edits, the infrastructure only, or the design first when the
// approved design moved on since the baseline (then G3 must be approved again before the build).
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Play } from "lucide-react";
import { resumeEditSet, useEditDiff, useEditSet, useIterationAccess } from "@/lib/iterations";
import { useAppState } from "@/lib/state";
import type { EditResumed, FileChange } from "@/lib/types";
import { CodeDiff, OriginPill } from "@/components/tabs/build/CodeViewer";
import { Panel } from "@/components/tabs/build/Panel";
import { RoleLock } from "@/components/shell/RoleNotice";
import { ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";

const CHANGE_TONE: Record<string, "decision" | "gate" | "error"> = { modified: "decision", added: "gate", deleted: "error" };

const RESUME_HINT: Record<string, string> = {
  build: "The design is unchanged: the build starts from these edits, merged into a fresh generation.",
  infra: "Only infrastructure changed: the code is carried from the baseline build and the infrastructure is validated again.",
  design: "The approved design changed since this build: the design runs first, G3 is approved again, then resume once more to build.",
};

function errorText(e: unknown): string {
  if (e && typeof e === "object" && "detail" in e && typeof (e as { detail: unknown }).detail === "string")
    return (e as { detail: string }).detail;
  return e instanceof Error ? e.message : String(e);
}

function Diff({ editSetId, file }: { editSetId: string; file: FileChange }) {
  const diff = useEditDiff(editSetId, file.path);
  if (diff.error) return <ErrorNote error={errorText(diff.error)} />;
  if (!diff.data) return <p className="text-[13px] text-slate">Loading {file.path}</p>;
  return <CodeDiff path={file.path} baseline={diff.data.baseline} edited={diff.data.edited} />;
}

export function EditPreview({
  editSetId,
  onResumed,
}: {
  editSetId: string;
  onResumed: (r: EditResumed) => void;
}) {
  const qc = useQueryClient();
  const { projectId, processId } = useAppState();
  const access = useIterationAccess("edits");
  const editSet = useEditSet(editSetId);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const es = editSet.data;
  if (editSet.error) return <ErrorNote error={errorText(editSet.error)} />;
  if (!es) return null;
  const file = es.files.find((f) => f.path === selected) ?? null;
  const resumed = es.status === "resumed";
  const reason = access.reason ?? (resumed ? "These edits were resumed already" : undefined);

  const resume = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await resumeEditSet(es.id);
      qc.invalidateQueries({ queryKey: ["edit-set", es.id] });
      qc.invalidateQueries({ queryKey: ["iterations", projectId] });
      qc.invalidateQueries({ queryKey: ["gates", processId] });
      qc.invalidateQueries({ queryKey: ["builds", processId] });
      onResumed(r);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title={`Hand edits: iteration ${es.iteration}`}
      eyebrow={`Against build ${es.baseline_build_id?.slice(0, 8) ?? "?"}: ${es.counts.modified ?? 0} modified, ${es.counts.added ?? 0} added, ${es.counts.deleted ?? 0} deleted`}
      testId="edit-preview"
      actions={
        <span title={reason}>
          <button
            type="button"
            className="btn-primary"
            data-testid="resume-edits"
            disabled={!!reason || busy}
            onClick={resume}
          >
            {busy ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Play size={16} aria-hidden />}
            <RoleLock reason={access.reason} />
            Resume from {es.resume_phase}
          </button>
        </span>
      }
    >
      <p className="mb-3 text-[13px] text-slate">{RESUME_HINT[es.resume_phase] ?? ""}</p>
      {reason ? <p className="mb-2 text-[12.5px] text-slate">{reason}</p> : null}
      {error ? <ErrorNote error={error} /> : null}
      {es.files.length === 0 ? (
        <p className="text-[13.5px] text-slate">The upload is identical to the build: nothing to apply.</p>
      ) : (
        <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
          <ul className="space-y-1" aria-label="Changed files">
            {es.files.map((f) => (
              <li key={f.path}>
                <button
                  type="button"
                  data-testid={`edit-row-${f.path}`}
                  aria-pressed={selected === f.path}
                  onClick={() => setSelected(f.path)}
                  className={`flex w-full flex-wrap items-center gap-1.5 rounded-btn border px-2.5 py-1.5 text-left text-[12.5px] ${
                    selected === f.path ? "border-purple bg-purple-lt" : "border-line hover:bg-warm"
                  }`}
                >
                  <Pill tone={CHANGE_TONE[f.change] ?? "decision"}>{f.change}</Pill>
                  <OriginPill origin={f.origin} />
                  <span className="min-w-0 break-all font-mono text-plum">{f.path}</span>
                </button>
              </li>
            ))}
          </ul>
          {file ? (
            <Diff editSetId={es.id} file={file} />
          ) : (
            <p className="rounded-btn border border-dashed border-line bg-warm/60 p-6 text-center text-[13.5px] text-slate">
              Select a file to see the build&apos;s version beside yours.
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}
