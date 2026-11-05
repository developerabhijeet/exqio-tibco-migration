"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, FileUp, Loader2, ScrollText, Upload } from "lucide-react";
import { RoleLock } from "@/components/shell/RoleNotice";
import { ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import {
  MAX_RULES_BYTES,
  expertRulesApi,
  importFailure,
  useExpertRules,
  type ImportIssue,
  type ImportMode,
  type ImportReport,
} from "@/lib/expertRules";
import { useCan } from "@/lib/personas";
import { useAppState } from "@/lib/state";

const READ_ONLY = "You are acting as a viewer: expert rules are read only.";

function IssueTable({ issues, testId }: { issues: ImportIssue[]; testId: string }) {
  if (issues.length === 0) return null;
  return (
    <div className="table-scroll mt-3 max-h-64 overflow-y-auto rounded-card border border-line" data-testid={testId}>
      <table className="table table-dense">
        <thead>
          <tr>
            <th>Row</th>
            <th>Rule</th>
            <th>Field</th>
            <th>Problem</th>
          </tr>
        </thead>
        <tbody>
          {issues.map((e, i) => (
            <tr key={`${e.row}-${e.field}-${i}`}>
              <td className="tnum">{e.row}</td>
              <td className="font-mono">{e.rule_id ?? "-"}</td>
              <td>{e.field ?? "-"}</td>
              <td className="text-error">{e.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Step 1: import the project's custom TIBCO expert rules (CSV, previewed first) or the bundled set. */
export function ExpertRulesCard() {
  const { projectId } = useAppState();
  const { curate } = useCan();
  const blocked = !projectId ? "Create a project first." : curate ? undefined : READ_ONLY;
  const qc = useQueryClient();
  const rules = useExpertRules(projectId);
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<ImportMode>("merge");
  const [preview, setPreview] = useState<ImportReport | null>(null);
  const [done, setDone] = useState<ImportReport | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: ["expert-rules", projectId] });

  const dryRun = useMutation({
    mutationFn: (args: { f: File; m: ImportMode }) => expertRulesApi.importCsv(projectId!, args.f, args.m, true),
    onSuccess: (r) => setPreview(r),
  });
  const commit = useMutation({
    mutationFn: () => expertRulesApi.importCsv(projectId!, file!, mode, false),
    onSuccess: async (r) => {
      setDone(r);
      setPreview(null);
      setFile(null);
      await refresh();
    },
  });
  const defaults = useMutation({
    mutationFn: () => expertRulesApi.loadDefaults(projectId!),
    onSuccess: async (r) => {
      setDone(r);
      setPreview(null);
      await refresh();
    },
  });

  const choose = (f: File | undefined) => {
    setDone(null);
    setPreview(null);
    setLocalError(null);
    dryRun.reset();
    commit.reset();
    if (!f) return;
    if (f.size > MAX_RULES_BYTES) {
      setLocalError("The file is larger than 2 MB.");
      return;
    }
    setFile(f);
    dryRun.mutate({ f, m: mode });
  };

  const changeMode = (m: ImportMode) => {
    setMode(m);
    if (file) dryRun.mutate({ f: file, m });
  };

  const failure = importFailure(dryRun.error) ?? importFailure(commit.error);
  const otherError = (!importFailure(dryRun.error) && dryRun.error) || (!importFailure(commit.error) && commit.error) || defaults.error;
  const count = rules.data?.total ?? 0;
  const busy = dryRun.isPending || commit.isPending || defaults.isPending;
  const n = preview ? preview.imported + preview.updated : 0;

  return (
    <section className="card" data-testid="expert-rules-card" aria-labelledby="expert-rules-title">
      <div className="card-header">
        <div>
          <h3 id="expert-rules-title" className="card-title flex items-center gap-2">
            <ScrollText size={18} aria-hidden className="text-accent-ink" /> Custom TIBCO rules
          </h3>
          <p className="card-desc">
            Import expert rules as CSV. They are checked row by row, then added to the prompts of the phases each rule&apos;s
            agent covers, overriding the default guidance where they conflict.
          </p>
        </div>
        <div className="flex items-center gap-2 text-md">
          <Pill tone={count ? "gate" : "det"} testId="expert-rules-count">
            {count} rule{count === 1 ? "" : "s"}
            {rules.data && rules.data.enabled !== count ? `, ${rules.data.enabled} on` : ""}
          </Pill>
          <Link href="/rules" className="font-medium text-accent-ink hover:underline" data-testid="expert-rules-view">
            View rules →
          </Link>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={input}
          type="file"
          accept=".csv,text/csv"
          className="sr-only"
          data-testid="expert-rules-file"
          onChange={(e) => {
            choose(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          className="btn-secondary"
          disabled={!!blocked || busy}
          title={blocked}
          onClick={() => input.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            if (!blocked) choose(e.dataTransfer.files?.[0]);
          }}
        >
          <RoleLock reason={blocked} />
          <FileUp size={16} aria-hidden /> Choose or drop a CSV
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={!!blocked || busy}
          title={blocked}
          data-testid="expert-rules-defaults"
          onClick={() => defaults.mutate()}
        >
          {defaults.isPending ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <RoleLock reason={blocked} />}
          Load GMF expert rules (125)
        </button>
        {projectId ? (
          <a href={expertRulesApi.templateUrl()} className="btn-ghost" download>
            <Download size={16} aria-hidden /> Download template
          </a>
        ) : null}
      </div>

      {localError ? <p className="mt-3 text-md text-error">{localError}</p> : null}
      {dryRun.isPending ? (
        <p className="mt-3 flex items-center gap-2 text-md text-slate">
          <Loader2 size={16} className="animate-spin" aria-hidden /> Checking {file?.name}
        </p>
      ) : null}

      {preview && file ? (
        <div className="mt-4 rounded-card border border-line bg-sunken/50 p-4" data-testid="expert-rules-preview">
          <p className="text-md text-ink">
            <span className="font-semibold">{file.name}</span>: {preview.total} row{preview.total === 1 ? "" : "s"} read.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Pill tone="gate">{preview.imported} new</Pill>
            <Pill tone="purple">{preview.updated} updated</Pill>
            <Pill tone={preview.skipped ? "error" : "det"}>{preview.skipped} skipped</Pill>
            {mode === "replace" && preview.removed ? <Pill tone="decision">{preview.removed} removed</Pill> : null}
            {preview.warnings.length ? <Pill tone="decision">{preview.warnings.length} warnings</Pill> : null}
          </div>
          <IssueTable issues={preview.errors} testId="expert-rules-errors" />
          <IssueTable issues={preview.warnings} testId="expert-rules-warnings" />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <fieldset className="flex items-center gap-3 text-md" aria-label="Import mode">
              {(["merge", "replace"] as const).map((m) => (
                <label key={m} className="flex items-center gap-1.5">
                  <input type="radio" name="expert-rules-mode" checked={mode === m} onChange={() => changeMode(m)} />
                  {m === "merge" ? "Merge (update by Rule ID)" : "Replace all rules"}
                </label>
              ))}
            </fieldset>
            <button
              type="button"
              className="btn-primary"
              disabled={!!blocked || busy || n === 0}
              data-testid="expert-rules-import"
              onClick={() => commit.mutate()}
            >
              {commit.isPending ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Upload size={16} aria-hidden />}
              Import {n} rule{n === 1 ? "" : "s"}
            </button>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setPreview(null);
                setFile(null);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {failure ? (
        <div className="mt-3">
          <ErrorNote error={failure.message} />
          <IssueTable issues={failure.errors} testId="expert-rules-errors" />
        </div>
      ) : null}
      {otherError ? (
        <div className="mt-3">
          <ErrorNote error={otherError} />
        </div>
      ) : null}
      {done ? (
        <p role="status" className="mt-3 text-md text-slate" data-testid="expert-rules-done">
          {done.imported} imported, {done.updated} updated{done.skipped ? `, ${done.skipped} skipped` : ""}.{" "}
          <Link href="/rules" className="font-medium text-accent-ink hover:underline">
            View rules →
          </Link>
        </p>
      ) : null}
    </section>
  );
}
