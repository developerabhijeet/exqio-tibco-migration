"use client";

// A GitHub repository as a source (sub-project 6, Tasks 9-10): connect a branch, pull it again,
// and push an iteration's build to it. An estate repository (Upload) becomes a new upload and a
// scan; a build repository (Build) holding an exported build becomes an edit set to preview.
// Hidden when GitHub is off, and when this API has no repository routes (GET answers 404).
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { GitBranch, GitPullRequest, Loader2, RefreshCw, Upload } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useConnectors } from "@/lib/connectors";
import { connectRepo, pullRepo, pushRepo, useIterationAccess, useRepos } from "@/lib/iterations";
import { useAppState } from "@/lib/state";
import type { RepoView } from "@/lib/types";
import { RoleLock } from "@/components/shell/RoleNotice";

const REPO = /^[A-Za-z0-9_.-]{1,100}\/[A-Za-z0-9_.-]{1,100}$/;
const BUILDERS = ["engineer", "admin"] as const;

function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  return e instanceof Error ? e.message : String(e);
}

/** Polls a fetch job to its end; the result names what it made (an upload or an edit set). */
function useFetchJob(onResult: (result: Record<string, unknown>) => void) {
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  return async (jobId: string): Promise<string | null> => {
    for (let i = 0; i < 400 && live.current; i++) {
      const job = await api.job(jobId);
      if (job.status === "done") {
        onResult(job.result ?? {});
        return null;
      }
      if (job.status === "failed") return job.error ?? "the fetch failed";
      await new Promise((r) => setTimeout(r, 1500));
    }
    return null;
  };
}

function Row({
  repo,
  canPull,
  pullReason,
  buildId,
  pushReason,
  watch,
}: {
  repo: RepoView;
  canPull: boolean;
  pullReason: string | undefined;
  buildId: string | null;
  pushReason: string | undefined;
  watch: (jobId: string) => Promise<string | null>;
}) {
  const qc = useQueryClient();
  const { projectId } = useAppState();
  const [busy, setBusy] = useState<"pull" | "push" | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pr, setPr] = useState<{ number: number; url: string } | null>(null);

  const pull = async () => {
    setBusy("pull");
    setError(null);
    setStatus(null);
    try {
      const r = await pullRepo(repo.id);
      if (!r.changed) setStatus(`no new commit on ${repo.branch}`);
      else {
        setStatus(`fetching ${r.commit.slice(0, 12)}`);
        const failed = await watch(r.job_id);
        if (failed) setError(failed);
        qc.invalidateQueries({ queryKey: ["repos", projectId] });
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const push = async () => {
    if (!buildId) return;
    setBusy("push");
    setError(null);
    try {
      const r = await pushRepo(repo.id, buildId);
      setPr(r.pull_request);
      setStatus(`pushed ${r.branch} at ${r.commit.slice(0, 12)}`);
    } catch (e) {
      setError(e instanceof ApiError && (e.status === 404 || e.status === 405) ? "This API cannot push to a repository yet." : errorText(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <li className="rounded-btn border border-line px-3 py-2 text-[13px]" data-testid={`repo-${repo.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <GitBranch size={14} aria-hidden className="text-slate" />
        <span className="font-mono font-semibold text-plum">
          {repo.repo}@{repo.branch}
        </span>
        <span className="text-[12px] text-slate">
          {repo.last_commit ? `at ${repo.last_commit.slice(0, 12)}` : "not fetched yet"}
        </span>
        <span className="ml-auto flex items-center gap-2">
          <span title={pullReason}>
            <button type="button" className="btn-secondary" disabled={!canPull || busy !== null} onClick={pull}>
              {busy === "pull" ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <RefreshCw size={15} aria-hidden />}
              <RoleLock reason={pullReason} />
              Pull
            </button>
          </span>
          {repo.purpose === "build" && buildId ? (
            <span title={pushReason}>
              <button type="button" className="btn-secondary" disabled={!!pushReason || busy !== null} onClick={push}>
                {busy === "push" ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <Upload size={15} aria-hidden />}
                <RoleLock reason={pushReason} />
                Push build
              </button>
            </span>
          ) : null}
        </span>
      </div>
      {status ? (
        <p role="status" className="mt-1 text-[12.5px] text-slate">
          {status}
          {pr ? (
            <>
              {" "}
              <a className="inline-flex items-center gap-1 text-purple underline" href={pr.url} target="_blank" rel="noreferrer">
                <GitPullRequest size={12} aria-hidden /> pull request #{pr.number}
              </a>
            </>
          ) : null}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-1 text-[12.5px] text-error">
          {error}
        </p>
      ) : null}
    </li>
  );
}

export function RepoPanel({
  purpose,
  processId = null,
  buildId = null,
  onEditSet,
}: {
  purpose: "estate" | "build";
  processId?: string | null;
  buildId?: string | null;
  onEditSet?: (editSetId: string) => void;
}) {
  const qc = useQueryClient();
  const { projectId } = useAppState();
  const flags = useConnectors();
  const repos = useRepos(projectId);
  const access = useIterationAccess("repo", purpose === "build" ? BUILDERS : undefined);
  const pushAccess = useIterationAccess("repo", BUILDERS);
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const watch = useFetchJob((result) => {
    if (result.kind === "edit_set" && typeof result.id === "string") {
      setNote("The branch differs from its build: the edit set is below.");
      qc.invalidateQueries({ queryKey: ["iterations", projectId] });
      onEditSet?.(result.id);
    } else if (result.kind === "upload") {
      setNote(`Fetched ${String(result.commit ?? "").slice(0, 12)} as a new upload; the scan is queued.`);
      for (const q of ["files", "report", "processes"]) qc.invalidateQueries({ queryKey: [q, projectId] });
    }
  });

  if (!projectId || !flags.data?.github.enabled || !repos.data) return null;
  const rows = repos.data.filter((r) => r.purpose === purpose && (purpose === "estate" || r.process_id === processId));
  const repoOk = REPO.test(repo.trim());
  const canConnect = access.allowed && !busy && repoOk && branch.trim().length > 0 && (purpose === "estate" || !!processId);
  const id = `repo-${purpose}`;

  const connect = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canConnect) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const row = await connectRepo(projectId, {
        repo: repo.trim(),
        branch: branch.trim(),
        purpose,
        ...(purpose === "build" && processId ? { process_id: processId } : {}),
      });
      qc.invalidateQueries({ queryKey: ["repos", projectId] });
      setRepo("");
      setBranch("");
      if (row.job_id) {
        setNote(`Fetching ${row.repo}@${row.branch}`);
        const failed = await watch(row.job_id);
        if (failed) setError(failed);
        qc.invalidateQueries({ queryKey: ["repos", projectId] });
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card space-y-3 p-4" data-testid="repo-panel" aria-labelledby={`${id}-title`}>
      <div>
        <h3 id={`${id}-title`} className="flex items-center gap-2 text-[16px] font-semibold text-plum">
          <GitBranch size={16} aria-hidden /> GitHub repository
        </h3>
        <p className="text-[12.5px] text-slate">
          {purpose === "estate"
            ? "Take the estate from a branch instead of a zip: each pull of a new commit is a new upload and scan."
            : "A branch holding this build's export: a reviewer's commits come back as an edit set, and an iteration's build can be pushed to it."}
        </p>
      </div>
      {access.reason ? <p className="text-[12.5px] text-slate">{access.reason}</p> : null}
      {rows.length > 0 ? (
        <ul className="space-y-1.5" aria-label="Connected repositories">
          {rows.map((r) => (
            <Row
              key={r.id}
              repo={r}
              canPull={access.allowed}
              pullReason={access.reason}
              buildId={buildId}
              pushReason={pushAccess.reason}
              watch={watch}
            />
          ))}
        </ul>
      ) : null}
      <form className="flex flex-wrap items-end gap-2" onSubmit={connect}>
        <label className="block text-[13px]" htmlFor={`${id}-repo`}>
          <span className="label">Repository (owner/name)</span>
          <input
            id={`${id}-repo`}
            className="input mt-1 w-[240px] font-mono"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            placeholder="gmf/credit-estate"
            aria-invalid={repo.trim() !== "" && !repoOk}
            disabled={!access.allowed}
          />
        </label>
        <label className="block text-[13px]" htmlFor={`${id}-branch`}>
          <span className="label">Branch</span>
          <input
            id={`${id}-branch`}
            className="input mt-1 w-[180px] font-mono"
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            placeholder="main"
            disabled={!access.allowed}
          />
        </label>
        <span title={access.reason}>
          <button type="submit" className="btn-primary" disabled={!canConnect}>
            {busy ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <GitBranch size={15} aria-hidden />}
            <RoleLock reason={access.reason} />
            Connect
          </button>
        </span>
      </form>
      {note ? (
        <p role="status" className="text-[12.5px] text-slate">
          {note}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="rounded-btn border border-error/30 bg-error-bg px-3 py-2 text-[13px] text-error">
          {error}
        </p>
      ) : null}
    </section>
  );
}
