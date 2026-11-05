"use client";

// Project expert rules: TIBCO modernisation guidance imported from CSV (or the bundled GMF set),
// injected into each phase's system prompt. Reads are GETs; writes are thin calls the Upload card
// and the /rules page make and then refresh from.
import { useQuery } from "@tanstack/react-query";
import { ApiError, json, requireApiUrl } from "./api";

export type Severity = "Critical" | "High" | "Medium" | "Low";
export const SEVERITIES: readonly Severity[] = ["Critical", "High", "Medium", "Low"];
export const MAX_RULES_BYTES = 2 * 1024 * 1024;

export interface ExpertRule {
  id: string;
  rule_id: string;
  domain: string;
  name: string;
  severity: Severity;
  guidance: string;
  evidence: string;
  pass_criteria: string;
  primary_agent: string;
  enforcement: string;
  applicability: string;
  version: string;
  rule_status: string;
  owner: string;
  last_reviewed: string;
  notes: string;
  enabled: boolean;
  source: "upload" | "default";
  uploaded_by: string | null;
  /** The phases whose prompts receive this rule. */
  phases: string[];
  /** The phases whose prompts carried it so far. */
  applied_in: string[];
  last_applied: { phase: string; at: string; ref: string } | null;
}

export interface ExpertRuleList {
  rules: ExpertRule[];
  total: number;
  enabled: number;
  by_severity: Record<Severity, number>;
  phases: string[];
}

export interface ImportIssue {
  row: number;
  rule_id: string | null;
  field: string | null;
  message: string;
}

export interface ImportReport {
  dry_run: boolean;
  mode: ImportMode;
  source: string;
  total: number;
  imported: number;
  updated: number;
  skipped: number;
  removed: number;
  errors: ImportIssue[];
  warnings: ImportIssue[];
  rule_ids: string[];
}

export type ImportMode = "merge" | "replace";

/** A refused import (422): the message and, when the file was read, its row errors. */
export function importFailure(e: unknown): { message: string; errors: ImportIssue[] } | null {
  if (!(e instanceof ApiError) || e.status !== 422) return null;
  try {
    const d = JSON.parse(e.detail) as { message?: string; errors?: ImportIssue[] };
    return { message: d.message ?? e.detail, errors: d.errors ?? [] };
  } catch {
    return { message: e.detail, errors: [] };
  }
}

export const expertRulesApi = {
  list: (projectId: string) => json<ExpertRuleList>(`/projects/${projectId}/expert-rules`),
  importCsv: (projectId: string, file: File, mode: ImportMode, dryRun: boolean) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return json<ImportReport>(`/projects/${projectId}/expert-rules/import?mode=${mode}&dry_run=${dryRun}`, {
      method: "POST",
      body: form,
    });
  },
  loadDefaults: (projectId: string, mode: ImportMode = "merge") =>
    json<ImportReport>(`/projects/${projectId}/expert-rules/defaults?mode=${mode}`, { method: "POST" }),
  setEnabled: (projectId: string, ruleId: string, enabled: boolean) =>
    json<ExpertRule>(`/projects/${projectId}/expert-rules/${encodeURIComponent(ruleId)}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    }),
  clear: (projectId: string) => json<{ removed: number }>(`/projects/${projectId}/expert-rules`, { method: "DELETE" }),
  templateUrl: () => `${requireApiUrl()}/expert-rules/template.csv`,
};

export const useExpertRules = (projectId: string | null) =>
  useQuery({
    queryKey: ["expert-rules", projectId],
    queryFn: () => expertRulesApi.list(projectId!),
    enabled: !!projectId,
    retry: false,
  });

export const PHASE_LABEL: Record<string, string> = {
  discover: "Discover",
  analysis: "Validate",
  design: "Design",
  build: "Build",
  verify: "Verify",
  deploy: "Deploy",
};
