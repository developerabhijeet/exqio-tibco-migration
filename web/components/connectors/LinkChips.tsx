"use client";

// What a process has published, as chips that open the remote object (a pull request, the
// Confluence page, a Jira issue), and the state of the publish job that is running or just ended.
// Everyone sees the chips, viewers included; only the actions are role-gated.
import { CheckCircle2, ExternalLink, GitBranch, GitPullRequest, Loader2, XCircle } from "lucide-react";
import type { JobStream } from "@/lib/jobs";
import type { ConnectorLinkView } from "@/lib/types";
import { Pill } from "@/components/ui/Pill";

const LABEL: Record<string, string> = {
  branch: "Branch",
  pull_request: "Pull request",
  confluence_page: "Confluence page",
  jira_issue: "Jira",
  figma_frame: "Figma frame",
};

/** The chip's test id: `link-<object type>`, and `link-jira_issue-<key>` for an issue. */
export function linkTestId(link: ConnectorLinkView): string {
  return link.object_type === "jira_issue" ? `link-jira_issue-${link.remote_id}` : `link-${link.object_type}`;
}

function text(link: ConnectorLinkView): string {
  switch (link.object_type) {
    case "pull_request":
      return `#${link.remote_id}`;
    case "confluence_page":
      return link.remote_version ? `version ${link.remote_version}` : link.remote_id;
    default:
      return link.remote_id;
  }
}

export function LinkChip({ link }: { link: ConnectorLinkView }) {
  const Icon = link.object_type === "branch" ? GitBranch : link.object_type === "pull_request" ? GitPullRequest : ExternalLink;
  const label = LABEL[link.object_type] ?? link.object_type;
  const title = link.stale
    ? `${label} ${link.remote_id} no longer exists there (deleted since it was published)`
    : `${label} ${link.remote_id}, published by ${link.created_by}, updated ${new Date(link.updated_at).toLocaleString()}`;
  return (
    <a
      href={link.remote_url}
      target="_blank"
      rel="noopener noreferrer"
      data-testid={linkTestId(link)}
      data-stale={link.stale ? "true" : undefined}
      title={title}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[12px] font-medium underline-offset-2 hover:underline ${
        link.stale ? "border-line bg-warm text-slate line-through" : "border-purple/30 bg-purple-lt text-purple-dk"
      }`}
    >
      <Icon size={12} aria-hidden />
      <span className="sr-only">{label} </span>
      {link.object_type === "jira_issue" ? null : <span aria-hidden>{label}</span>}
      <span className="font-mono">{text(link)}</span>
      {link.stale ? <span className="no-underline">(gone)</span> : null}
    </a>
  );
}

export function LinkChips({ links, testId }: { links: readonly ConnectorLinkView[]; testId?: string }) {
  if (links.length === 0) return null;
  return (
    <span className="flex flex-wrap items-center gap-1.5" data-testid={testId}>
      {links.map((l) => (
        <LinkChip key={l.id} link={l} />
      ))}
    </span>
  );
}

/**
 * A publish job's state under its action: queued or running, then what it did or why it failed.
 * `summary` turns a finished job's result into one line.
 */
export function ActionStatus({
  stream,
  error,
  testId,
  summary,
}: {
  stream: JobStream | null;
  /** The request's own refusal (a 409 or 422 before any job). */
  error: string | null;
  testId: string;
  summary?: (result: Record<string, unknown>) => string | null;
}) {
  if (error) {
    return (
      <p role="alert" data-testid={`${testId}-error`} className="rounded-btn border border-error/30 bg-error-bg px-3 py-2 text-[13px] text-error">
        {error}
      </p>
    );
  }
  if (!stream) return null;
  const line = stream.status === "done" && stream.result && summary ? summary(stream.result) : null;
  return (
    <div role="status" aria-live="polite" data-testid={`${testId}-status`} data-state={stream.status} className="space-y-1 text-[13px]">
      {stream.status === "running" ? (
        <Pill tone="purple">
          <Loader2 size={12} className="animate-spin" aria-hidden /> {stream.stage ? `running: ${stream.stage}` : "queued"}
        </Pill>
      ) : stream.status === "done" ? (
        <span className="flex flex-wrap items-center gap-2">
          <Pill tone="gate">
            <CheckCircle2 size={12} aria-hidden /> done
          </Pill>
          {line ? <span className="text-slate">{line}</span> : null}
        </span>
      ) : (
        <span className="flex flex-wrap items-start gap-2">
          <Pill tone="error">
            <XCircle size={12} aria-hidden /> failed
          </Pill>
          <span className="min-w-0 break-words text-error" data-testid={`${testId}-error`}>
            {stream.error ?? "the job failed"}
          </span>
        </span>
      )}
    </div>
  );
}
