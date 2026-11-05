"use client";

// Re-enter Design (POST /processes/{id}/iterations): a new iteration with the failures chosen from
// the latest build, verify and plan, uploaded logs and requirements; G3 to G6 go back to pending and
// the design job is queued. An architect or admin chooses all three. An SME may give requirements
// only, so an SME's request is always JSON: the API refuses an SME's multipart request outright.
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FileText, Loader2, PencilRuler, Repeat, X } from "lucide-react";
import { useJob } from "@/lib/jobs";
import {
  FULL_OPENERS,
  MAX_LOG_BYTES,
  MAX_LOGS,
  MAX_REQUIREMENTS,
  openIteration,
  useIterationAccess,
  usePreview,
} from "@/lib/iterations";
import { useAppState } from "@/lib/state";
import type { FailureItem, IterationOpenBody } from "@/lib/types";
import { RoleLock } from "@/components/shell/RoleNotice";
import { Pill } from "@/components/ui/Pill";

const FOCUSABLE =
  'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

/** Tab stays inside the dialog while it is open; focus goes back to the trigger when it closes. */
export function useDialogFocus(
  open: boolean,
  dialog: React.RefObject<HTMLElement | null>,
  trigger: React.RefObject<HTMLElement | null>,
) {
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open) {
      wasOpen.current = true;
      const first = dialog.current?.querySelector<HTMLElement>("[data-autofocus]") ??
        dialog.current?.querySelector<HTMLElement>(FOCUSABLE);
      first?.focus();
    } else if (wasOpen.current) {
      wasOpen.current = false;
      trigger.current?.focus();
    }
  }, [open, dialog, trigger]);
  return (e: React.KeyboardEvent) => {
    if (e.key !== "Tab" || !dialog.current) return;
    const items = Array.from(dialog.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };
}

const KIND_LABEL: Record<string, string> = {
  build_failure: "build",
  test_failure: "test",
  parity_defect: "parity",
  plan_error: "plan",
  uploaded_log: "log",
};

function errorText(e: unknown): string {
  if (e && typeof e === "object" && "detail" in e && typeof (e as { detail: unknown }).detail === "string")
    return (e as { detail: string }).detail;
  return e instanceof Error ? e.message : String(e);
}

function FailureRow({
  item,
  checked,
  locked,
  onToggle,
}: {
  item: FailureItem;
  checked: boolean;
  locked: boolean;
  onToggle: () => void;
}) {
  const id = `failure-${item.key}`;
  return (
    <li data-testid={id} className="rounded-btn border border-line px-3 py-2">
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" checked={checked} disabled={locked} onChange={onToggle} />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <Pill tone={item.kind === "uploaded_log" ? "det" : "error"}>{KIND_LABEL[item.kind] ?? item.kind}</Pill>
            <span className="font-medium text-heading">{item.title}</span>
          </span>
          <details className="mt-1">
            <summary className="cursor-pointer text-xs text-slate">Excerpt</summary>
            {/* Text only: a log excerpt is never rendered as HTML. */}
            <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded-btn bg-page p-2 font-mono text-xs text-ink">
              {item.text}
            </pre>
          </details>
        </span>
      </label>
    </li>
  );
}

/**
 * The "Re-enter design" button and its dialog, for one process. Shown disabled, with the reason
 * beside it, to anyone the API would refuse.
 */
export function ReenterDesign({ processId, processName }: { processId: string; processName?: string }) {
  const qc = useQueryClient();
  const { projectId, setStep } = useAppState();
  const access = useIterationAccess("iterate");
  const full = access.roles.some((r) => (FULL_OPENERS as readonly string[]).includes(r));
  const design = useJob("design", processId);
  const [open, setOpen] = useState(false);
  const preview = usePreview(open ? processId : null);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [logs, setLogs] = useState<File[]>([]);
  const [logError, setLogError] = useState<string | null>(null);
  const [requirements, setRequirements] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLFormElement>(null);
  const trapTab = useDialogFocus(open, dialogRef, triggerRef);

  const items = preview.data?.items ?? [];
  const smeOnly = !full;
  const req = requirements.trim();
  const canSubmit = access.allowed && !busy && (!smeOnly || req.length > 0);

  const close = () => {
    setOpen(false);
    setError(null);
  };

  const pickLogs = (files: FileList | null) => {
    setLogError(null);
    const picked = Array.from(files ?? []);
    const all = [...logs, ...picked.filter((f) => !logs.some((l) => l.name === f.name && l.size === f.size))];
    const big = all.find((f) => f.size > MAX_LOG_BYTES);
    if (big) {
      setLogError(`${big.name} is larger than 5 MB`);
      return;
    }
    if (all.length > MAX_LOGS) {
      setLogError(`at most ${MAX_LOGS} log files per iteration`);
      return;
    }
    setLogs(all);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    // An SME sends requirements only (JSON); an architect names the failures to keep.
    const body: IterationOpenBody = smeOnly
      ? { requirements: req }
      : { ...(req ? { requirements: req } : {}), include: items.filter((i) => !excluded.has(i.key)).map((i) => i.key) };
    try {
      const row = await openIteration(processId, body, smeOnly ? [] : logs);
      if (row.job_id) design.watch(row.job_id);
      for (const q of [["iterations", projectId], ["gates", processId], ["design", processId], ["iteration-preview", processId]])
        qc.invalidateQueries({ queryKey: q });
      qc.invalidateQueries({ queryKey: ["readiness"] });
      setOpen(false);
      setRequirements("");
      setLogs([]);
      setExcluded(new Set());
      setStep("design");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col items-start gap-1">
      <span title={access.reason}>
        <button
          type="button"
          ref={triggerRef}
          className="btn-secondary"
          data-testid="reenter-design"
          aria-haspopup="dialog"
          aria-describedby={access.reason ? "reenter-design-reason" : undefined}
          disabled={!access.allowed}
          onClick={() => setOpen(true)}
        >
          <Repeat size={16} aria-hidden />
          <RoleLock reason={access.reason} />
          Re-enter design
        </button>
      </span>
      {access.reason ? (
        <span id="reenter-design-reason" data-testid="reenter-design-reason" className="text-sm text-slate">
          {access.reason}
        </span>
      ) : null}

      {open ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6" role="presentation">
          <button type="button" tabIndex={-1} aria-label="Close the re-enter dialog" className="absolute inset-0 bg-plum/40" onClick={close} />
          <form
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="reenter-heading"
            aria-describedby="reenter-summary"
            data-testid="reenter-dialog"
            className="relative max-h-[90vh] w-[640px] max-w-full space-y-4 overflow-y-auto rounded-card bg-surface p-5 shadow-pop"
            onSubmit={submit}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.stopPropagation();
                close();
              } else trapTab(e);
            }}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <h4 id="reenter-heading" className="text-lg font-semibold text-heading">
                  Re-enter design{processName ? ` for ${processName}` : ""}
                </h4>
                <p id="reenter-summary" className="mt-0.5 text-sm text-slate">
                  Opens a new iteration: G3 to G6 go back to pending and the designer runs again with what you bring in.
                </p>
              </div>
              <button type="button" className="btn-ghost" aria-label="Close" onClick={close}>
                <X size={16} aria-hidden />
              </button>
            </div>

            <fieldset className="space-y-2">
              <legend className="label">What failed</legend>
              {smeOnly ? (
                <p className="text-sm text-slate">
                  As an SME you add requirements only; the failures below go in as they are, and an architect may choose
                  among them.
                </p>
              ) : null}
              {preview.isLoading ? <p className="text-sm text-slate">Collecting the failures</p> : null}
              {preview.error ? <p className="text-sm text-error">{errorText(preview.error)}</p> : null}
              {preview.data && items.length === 0 ? (
                <p className="text-sm text-slate">Nothing failed in the latest build, verify or plan.</p>
              ) : null}
              <ul className="space-y-1.5" data-testid="failure-list">
                {items.map((item) => (
                  <FailureRow
                    key={item.key}
                    item={item}
                    checked={smeOnly || !excluded.has(item.key)}
                    locked={smeOnly}
                    onToggle={() =>
                      setExcluded((s) => {
                        const next = new Set(s);
                        if (next.has(item.key)) next.delete(item.key);
                        else next.add(item.key);
                        return next;
                      })
                    }
                  />
                ))}
              </ul>
            </fieldset>

            <div className="space-y-1.5">
              <label htmlFor="reenter-logs" className="label block">
                Logs (text, at most {MAX_LOGS} files of 5 MB)
              </label>
              <input
                id="reenter-logs"
                type="file"
                multiple
                accept=".log,.txt,text/plain"
                data-testid="log-drop"
                disabled={smeOnly || busy}
                aria-describedby="reenter-logs-hint"
                className="block w-full text-sm file:mr-3 file:rounded-btn file:border file:border-line file:bg-sunken file:px-3 file:py-1.5 file:text-heading disabled:opacity-60"
                onChange={(e) => {
                  pickLogs(e.target.files);
                  e.target.value = "";
                }}
              />
              <span id="reenter-logs-hint" className="block text-xs text-slate">
                {smeOnly
                  ? "An SME may open an iteration with requirements only: logs need an architect."
                  : "Masked before they are stored; never shown as HTML."}
              </span>
              {logError ? <p role="alert" className="text-sm text-error">{logError}</p> : null}
              {logs.length > 0 ? (
                <ul className="flex flex-wrap gap-1.5">
                  {logs.map((f) => (
                    <li key={`${f.name}:${f.size}`} className="flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-xs">
                      <FileText size={12} aria-hidden />
                      {f.name}
                      <button
                        type="button"
                        className="btn-ghost px-1 py-0"
                        aria-label={`Remove ${f.name}`}
                        onClick={() => setLogs((l) => l.filter((x) => x !== f))}
                      >
                        <X size={12} aria-hidden />
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>

            <div className="space-y-1">
              <label htmlFor="reenter-requirements" className="label block">
                Requirements{smeOnly ? " (required)" : ""}
              </label>
              <textarea
                id="reenter-requirements"
                data-testid="requirements"
                data-autofocus
                className="input min-h-[110px] w-full"
                value={requirements}
                maxLength={MAX_REQUIREMENTS}
                onChange={(e) => setRequirements(e.target.value)}
                placeholder="What the next design must do differently, in plain words."
                aria-describedby="reenter-requirements-count"
                required={smeOnly}
              />
              <span id="reenter-requirements-count" className="block text-right text-xs text-slate">
                {requirements.length} of {MAX_REQUIREMENTS.toLocaleString()} characters
              </span>
            </div>

            {error ? (
              <p role="alert" className="rounded-btn border border-error/30 bg-error-bg px-3 py-2 text-sm text-error">
                {error}
              </p>
            ) : null}
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-secondary" onClick={close}>
                Cancel
              </button>
              <button type="submit" className="btn-primary" data-testid="open-iteration" disabled={!canSubmit}>
                {busy ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <PencilRuler size={16} aria-hidden />}
                Open iteration
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Verify and Deploy: "Re-enter design" once something of the kinds given failed (a parity defect,
 * a failed plan), with one line saying what.
 */
export function ReenterOnFailure({
  processId,
  processName,
  kinds,
  what,
}: {
  processId: string;
  processName?: string;
  kinds: readonly FailureItem["kind"][];
  what: string;
}) {
  const preview = usePreview(processId);
  const failed = (preview.data?.items ?? []).filter((i) => kinds.includes(i.kind));
  if (failed.length === 0) return null;
  return (
    <div className="rounded-card border border-line bg-surface px-4 py-3" data-testid="reenter-on-failure">
      <p className="mb-2 text-md text-ink">
        {failed.length} {what} to take back to the design: the next iteration&apos;s designer reads them with any logs and
        requirements you add.
      </p>
      <ReenterDesign processId={processId} processName={processName} />
    </div>
  );
}
