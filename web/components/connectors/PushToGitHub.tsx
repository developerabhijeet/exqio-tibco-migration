"use client";

// Build tab: push the finished build to an exiqo/ branch of a GitHub repository and open the pull
// request (POST /builds/{id}/github/push, a `connector.github.push` job). Hidden when GitHub is off;
// shown disabled, with the reason, to anyone the API would refuse (engineer or admin only).
import { useEffect, useRef, useState } from "react";
import { GitPullRequest, Loader2, Upload, X } from "lucide-react";
import { api } from "@/lib/api";
import { useConnectors } from "@/lib/connectors";
import { useJob } from "@/lib/jobs";
import { actionError, useActionAccess, useLinks } from "@/lib/publish";
import { RoleLock } from "@/components/shell/RoleNotice";
import { ActionStatus, LinkChips } from "./LinkChips";

const REPO = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

/** `exiqo/<process slug>/build-<id>`: the API's default branch (connectors/github.py). */
export function defaultBranch(processName: string, buildId: string): string {
  const slug =
    processName
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60)
      .replace(/-+$/g, "") || "process";
  return `exiqo/${slug}/build-${buildId.slice(0, 8)}`;
}

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), select, textarea, a[href], [tabindex]:not([tabindex="-1"])';

/** Keep Tab inside the dialog while it is open, and give focus back to its trigger when it closes. */
function useDialogFocus(open: boolean, dialog: React.RefObject<HTMLElement | null>, trigger: React.RefObject<HTMLElement | null>) {
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open) wasOpen.current = true;
    else if (wasOpen.current) {
      wasOpen.current = false;
      trigger.current?.focus();
    }
  }, [open, trigger]);
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

function summary(result: Record<string, unknown>): string | null {
  const pr = result.pull_request as { url?: string; number?: number } | undefined;
  if (pr?.number) return `pushed ${String(result.branch ?? "")}, pull request #${pr.number}`;
  if (typeof result.message === "string") return result.message;
  return result.branch ? `pushed ${String(result.branch)}` : null;
}

export function PushToGitHub({
  processId,
  processName,
  buildId,
  finished,
}: {
  processId: string;
  processName: string;
  buildId: string;
  finished: boolean;
}) {
  const flags = useConnectors();
  const access = useActionAccess("github");
  const links = useLinks(processId);
  const { stream, watch } = useJob("github", processId);
  const [open, setOpen] = useState(false);
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("");
  const [base, setBase] = useState("");
  const [openPr, setOpenPr] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLFormElement>(null);
  const trapTab = useDialogFocus(open, dialogRef, triggerRef);

  if (!flags.data?.github.enabled) return null;
  const mine = (links.data ?? []).filter(
    (l) => l.kind === "github" && l.local_ref === buildId && (l.object_type === "pull_request" || l.object_type === "branch"),
  );
  const running = stream?.status === "running";
  const reason = access.reason ?? (!finished ? "The build has not finished" : undefined);
  const repoOk = REPO.test(repo.trim());
  const branchOk = !branch.trim() || branch.trim().startsWith("exiqo/");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!repoOk || !branchOk) return;
    setBusy(true);
    setError(null);
    try {
      const { job_id } = await api.pushToGithub(buildId, {
        repo: repo.trim(),
        open_pr: openPr,
        ...(branch.trim() ? { branch: branch.trim() } : {}),
        ...(base.trim() ? { base: base.trim() } : {}),
      });
      watch(job_id);
      setOpen(false);
    } catch (err) {
      setError(actionError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card card-compact space-y-2" data-testid="github-publish" aria-labelledby="github-publish-title">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="github-publish-title" className="text-lg font-semibold text-heading">
          GitHub
        </h3>
        <LinkChips links={mine} testId="github-links" />
        <span className="ml-auto" title={reason}>
          <button
            type="button"
            className="btn-secondary"
            data-testid="push-github"
            ref={triggerRef}
            aria-haspopup="dialog"
            disabled={!!reason || running}
            onClick={() => {
              setError(null);
              setOpen(true);
            }}
          >
            {running ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Upload size={16} aria-hidden />}
            <RoleLock reason={access.reason} />
            Push to GitHub
          </button>
        </span>
      </div>
      {access.reason ? <p className="text-sm text-slate">{access.reason}</p> : null}
      <ActionStatus stream={stream} error={open ? null : error} testId="push-github" summary={summary} />

      {open ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6" role="presentation">
          <button type="button" aria-label="Close the push dialog" className="absolute inset-0 bg-plum/40" onClick={() => setOpen(false)} />
          <form
            role="dialog"
            aria-modal="true"
            aria-labelledby="push-github-heading"
            data-testid="push-github-dialog"
            className="relative w-[480px] max-w-full space-y-3 rounded-card bg-surface p-5 shadow-pop"
            onSubmit={submit}
            ref={dialogRef}
            onKeyDown={(e) => {
              if (e.key === "Escape") setOpen(false);
              else trapTab(e);
            }}
          >
            <div className="flex items-center justify-between">
              <h4 id="push-github-heading" className="text-lg font-semibold text-heading">
                Push build {buildId.slice(0, 8)} to GitHub
              </h4>
              <button type="button" className="btn-ghost" aria-label="Close" onClick={() => setOpen(false)}>
                <X size={16} />
              </button>
            </div>
            <label className="block text-sm">
              <span className="label">Repository (owner/name)</span>
              <input
                className="input mt-1 w-full"
                data-testid="push-github-repo"
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
                placeholder="gmf/credit-services"
                aria-invalid={repo.trim() !== "" && !repoOk}
                aria-describedby="push-github-repo-hint"
                autoFocus
                required
              />
              <span id="push-github-repo-hint" className="mt-0.5 block text-xs text-slate">
                {repo.trim() !== "" && !repoOk ? "Not owner/name." : "The repository must already exist."}
              </span>
            </label>
            <label className="block text-sm">
              <span className="label">Branch</span>
              <input
                className="input mt-1 w-full font-mono"
                data-testid="push-github-branch"
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
                placeholder={defaultBranch(processName, buildId)}
                aria-invalid={!branchOk}
                aria-describedby="push-github-branch-hint"
              />
              <span id="push-github-branch-hint" className={`mt-0.5 block text-xs ${branchOk ? "text-slate" : "text-error"}`}>
                {branchOk ? "Empty: the default above. Pushes go to exiqo/ branches only." : "Pushes go to exiqo/ branches only."}
              </span>
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={openPr} onChange={(e) => setOpenPr(e.target.checked)} data-testid="push-github-open-pr" />
              Open a pull request
            </label>
            {openPr ? (
              <label className="block text-sm">
                <span className="label">Base branch</span>
                <input
                  className="input mt-1 w-full font-mono"
                  data-testid="push-github-base"
                  value={base}
                  onChange={(e) => setBase(e.target.value)}
                  placeholder="the repository's default branch"
                />
              </label>
            ) : null}
            {error ? (
              <p role="alert" className="rounded-btn border border-error/30 bg-error-bg px-3 py-2 text-sm text-error">
                {error}
              </p>
            ) : null}
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>
                Cancel
              </button>
              <button type="submit" className="btn-primary" data-testid="push-github-submit" disabled={busy || !repoOk || !branchOk}>
                {busy ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <GitPullRequest size={16} aria-hidden />}
                Push
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </section>
  );
}
