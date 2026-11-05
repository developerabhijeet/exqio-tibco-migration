"use client";

// File Jira issues for the items of a tab: findings (Validate), open questions (Design), unexplained
// parity differences (Verify). POST /processes/{id}/jira/issues queues a `connector.jira.issues`
// job; each item is named by its stable identity (ruling X17: a finding key, a question id, a
// `<scenario key>:<field>`), never by its position. An item already filed shows its issue; filing
// it again updates that issue. Hidden when Jira is off; disabled with the reason for a role the
// API refuses (architect, SME, QA or admin may file).
import { useMemo, useState } from "react";
import { Loader2, TicketPlus } from "lucide-react";
import { api } from "@/lib/api";
import { useConnectors } from "@/lib/connectors";
import { useJob } from "@/lib/jobs";
import { actionError, jiraLinks, jiraRef, useActionAccess, useLinks, type JiraCandidate } from "@/lib/publish";
import { RoleLock } from "@/components/shell/RoleNotice";
import { ActionStatus, LinkChip } from "./LinkChips";

export type { JiraCandidate } from "@/lib/publish";
export { findingCandidates, parityCandidates, questionCandidates } from "@/lib/publish";

function summary(result: Record<string, unknown>): string | null {
  const n = (k: string) => (Array.isArray(result[k]) ? (result[k] as unknown[]).length : 0);
  const parts = [
    n("created") ? `${n("created")} created` : "",
    n("updated") ? `${n("updated")} updated` : "",
    n("stale") ? `${n("stale")} skipped: deleted in Jira (tick "create again")` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : null;
}

export function JiraIssues({
  processId,
  scope,
  title,
  candidates,
  empty,
}: {
  processId: string;
  scope: "validate" | "design" | "verify";
  title: string;
  candidates: JiraCandidate[];
  /** Shown when there is nothing to file. */
  empty: string;
}) {
  const flags = useConnectors();
  const access = useActionAccess("jira");
  const links = useLinks(processId);
  const { stream, watch } = useJob(`jira-${scope}`, processId);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [createAgain, setCreateAgain] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const byRef = useMemo(() => jiraLinks(links.data), [links.data]);

  if (!flags.data?.atlassian.jira) return null;
  const shown = candidates.filter((c) => c.trackable || byRef.has(jiraRef(c.item)));
  const selected = shown.filter((c) => c.trackable && picked.has(jiraRef(c.item)));
  const running = busy || stream?.status === "running";
  const anyStale = selected.some((c) => byRef.get(jiraRef(c.item))?.stale);
  const reason = access.reason ?? (selected.length === 0 ? "Select the items to file" : undefined);

  const toggle = (ref: string, on: boolean) =>
    setPicked((p) => {
      const next = new Set(p);
      if (on) next.add(ref);
      else next.delete(ref);
      return next;
    });

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const { job_id } = await api.jiraIssues(
        processId,
        selected.map((c) => c.item),
        createAgain && anyStale,
      );
      watch(job_id);
      setPicked(new Set());
    } catch (e) {
      setError(actionError(e));
    } finally {
      setBusy(false);
    }
  };

  const headingId = `jira-${scope}-title`;
  return (
    <section className="card card-compact space-y-2 print:hidden" data-testid={`jira-${scope}`} aria-labelledby={headingId}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 id={headingId} className="text-lg font-semibold text-heading">
          {title}
        </h3>
        <span className="ml-auto" title={reason}>
          <button type="button" className="btn-secondary" data-testid="jira-create" disabled={!!reason || running} onClick={create}>
            {running ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <TicketPlus size={16} aria-hidden />}
            <RoleLock reason={access.reason} />
            {selected.length > 0 ? `File ${selected.length} in Jira` : "File in Jira"}
          </button>
        </span>
      </div>
      {access.reason ? <p className="text-sm text-slate">{access.reason}</p> : null}
      {shown.length === 0 ? (
        <p className="text-sm text-slate">{empty}</p>
      ) : (
        <ul className="max-h-[320px] divide-y divide-line overflow-y-auto rounded-btn border border-line text-sm" data-testid={`jira-${scope}-items`}>
          {shown.map((c) => {
            const ref = jiraRef(c.item);
            const link = byRef.get(ref);
            const id = `jira-select-${c.item.type}-${c.item.ref}`;
            return (
              <li key={ref} className="flex items-start gap-2 px-3 py-2">
                <input
                  id={`${scope}-${id}`}
                  type="checkbox"
                  className="mt-0.5"
                  data-testid={id}
                  checked={picked.has(ref)}
                  disabled={!c.trackable || !access.allowed || running}
                  title={!c.trackable ? c.note : access.reason}
                  onChange={(e) => toggle(ref, e.target.checked)}
                />
                <label htmlFor={`${scope}-${id}`} className="min-w-0 flex-1 break-words">
                  {c.label}
                  {!c.trackable && c.note ? <span className="ml-1 text-slate">({c.note})</span> : null}
                </label>
                {link ? <LinkChip link={link} /> : null}
              </li>
            );
          })}
        </ul>
      )}
      {anyStale ? (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={createAgain} onChange={(e) => setCreateAgain(e.target.checked)} data-testid="jira-create-again" />
          Create again the issues that were deleted in Jira
        </label>
      ) : null}
      <ActionStatus stream={stream} error={error} testId="jira-create" summary={summary} />
    </section>
  );
}
