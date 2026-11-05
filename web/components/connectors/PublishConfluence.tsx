"use client";

// Design tab: publish (or republish) the process's architecture page to Confluence
// (POST /processes/{id}/confluence/publish, a `connector.confluence.publish` job). Hidden when
// Confluence is off; disabled with the reason for anyone but an architect or admin. A page deleted in
// Confluence leaves its link stale: a new page is made only with the "Create again" opt-in (X20).
import { useState } from "react";
import { BookOpen, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useConnectors } from "@/lib/connectors";
import { useJob } from "@/lib/jobs";
import { actionError, useActionAccess, useLinks } from "@/lib/publish";
import { RoleLock } from "@/components/shell/RoleNotice";
import { FigmaReferences } from "./FigmaReferences";
import { ActionStatus, LinkChips } from "./LinkChips";

function summary(result: Record<string, unknown>): string | null {
  return result.version ? `published "${String(result.title ?? "")}", version ${String(result.version)}` : null;
}

export function PublishConfluence({ processId, hasDesign }: { processId: string; hasDesign: boolean }) {
  const flags = useConnectors();
  const access = useActionAccess("confluence");
  const links = useLinks(processId);
  const { stream, watch } = useJob("confluence", processId);
  const [createAgain, setCreateAgain] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!flags.data?.atlassian.confluence) return null;
  const page = (links.data ?? []).filter((l) => l.object_type === "confluence_page");
  const stale = page.some((l) => l.stale);
  const running = busy || stream?.status === "running";
  const reason = access.reason ?? (!hasDesign ? "Generate the design record first" : undefined);

  const publish = async () => {
    setBusy(true);
    setError(null);
    try {
      const { job_id } = await api.publishConfluence(processId, createAgain && stale);
      watch(job_id);
      setCreateAgain(false);
    } catch (e) {
      setError(actionError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card card-compact space-y-2 print:hidden" data-testid="confluence-publish" aria-labelledby="confluence-publish-title">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="confluence-publish-title" className="text-lg font-semibold text-heading">
          Confluence
        </h3>
        <LinkChips links={page} />
        <span className="ml-auto" title={reason}>
          <button type="button" className="btn-secondary" data-testid="publish-confluence" disabled={!!reason || running} onClick={publish}>
            {running ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <BookOpen size={16} aria-hidden />}
            <RoleLock reason={access.reason} />
            {page.length > 0 ? "Republish to Confluence" : "Publish to Confluence"}
          </button>
        </span>
      </div>
      <p className="text-sm text-slate">
        {access.reason ??
          "The architecture page is built from this design record by code: the mapping, views, decisions and open questions."}
      </p>
      {stale ? (
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={createAgain}
            disabled={!access.allowed || running}
            onChange={(e) => setCreateAgain(e.target.checked)}
            data-testid="confluence-create-again"
          />
          Create again the page that was deleted in Confluence
        </label>
      ) : null}
      <ActionStatus stream={stream} error={error} testId="publish-confluence" summary={summary} />
    </section>
  );
}

/** The Design tab's connector row: Confluence publishing beside the Figma references; nothing when
 * both connectors are off. */
export function DesignConnectorPanels({ processId, hasDesign }: { processId: string; hasDesign: boolean }) {
  const flags = useConnectors();
  if (!flags.data?.atlassian.confluence && !flags.data?.figma.enabled) return null;
  return (
    <div className="grid items-start gap-4 xl:grid-cols-2 print:hidden" data-testid="design-connectors">
      <PublishConfluence processId={processId} hasDesign={hasDesign} />
      <FigmaReferences processId={processId} />
    </div>
  );
}
