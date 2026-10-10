"use client";

import { useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";
import { ErrorNote } from "@/components/ui/Empty";
import { Pill } from "@/components/ui/Pill";
import { useBuildFiles, useInfra } from "@/lib/buildQueries";
import type { InfraValidation } from "@/lib/buildTypes";
import { CodeViewer } from "./CodeViewer";
import { CheckPill, Panel } from "./Panel";

const KINDS: { kind: string; label: string; open: boolean }[] = [
  { kind: "terragrunt", label: "Terragrunt live configuration", open: true },
  { kind: "terraform", label: "Terraform modules", open: false },
  { kind: "dockerfile", label: "Container image", open: true },
  { kind: "pipeline", label: "Pipelines", open: true },
  { kind: "other", label: "Other", open: false },
];

function moduleReason(m: { init_ok: boolean | null; validate_ok: boolean | null; registry_unreachable?: boolean; log?: string }): string {
  const same = (m.log ?? "").match(/same failure as module (\w+)/);
  if (m.registry_unreachable && same) return `provider registry unreachable (same as ${same[1]})`;
  if (m.registry_unreachable) return "provider registry unreachable: init could not download providers, validate did not run";
  if (m.init_ok === false) return (m.log ?? "").split("\n").find((l) => l.startsWith("Error")) ?? "init failed";
  if (m.validate_ok === false) return (m.log ?? "").split("\n").find((l) => l.startsWith("Error")) ?? "validate failed";
  return "";
}

/** fmt, per-module init and validate, and terragrunt hclfmt, each with its reason when red. */
export function ValidationRows({ validation, testId }: { validation: InfraValidation | null; testId?: string }) {
  if (!validation) return <p className="text-[14px] text-slate">No validation recorded.</p>;
  const modules = validation.modules ?? [];
  return (
    <div data-testid={testId} className="space-y-2">
      <div className="overflow-x-auto rounded-btn border border-line">
        <table className="w-full min-w-[620px] text-[13px]">
          <thead>
            <tr className="table-head">
              <th className="px-3 py-2">Check</th>
              <th className="px-3 py-2">Result</th>
              <th className="px-3 py-2">Reason</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            <tr data-check="fmt" data-ok={String(validation.fmt_ok)} className={validation.fmt_ok === false ? "bg-error-bg/40" : ""}>
              <td className="px-3 py-2 font-mono text-[12px]">terraform fmt -check</td>
              <td className="px-3 py-2">
                <CheckPill ok={validation.fmt_ok} yes="passed" />
              </td>
              <td className="px-3 py-2 text-slate">{validation.fmt_ok === false ? "files need formatting" : ""}</td>
            </tr>
            <tr data-check="hclfmt" data-ok={String(validation.hclfmt_ok)} className={validation.hclfmt_ok === false ? "bg-error-bg/40" : ""}>
              <td className="px-3 py-2 font-mono text-[12px]">terragrunt hcl fmt --check</td>
              <td className="px-3 py-2">
                <CheckPill ok={validation.hclfmt_ok} yes="passed" />
              </td>
              <td className="px-3 py-2 text-slate">{validation.hclfmt_ok === false ? "live configuration needs formatting" : ""}</td>
            </tr>
            {modules.map((m) => {
              const reason = moduleReason(m);
              return (
                <tr key={m.name} className={m.init_ok === false || m.validate_ok === false ? "bg-error-bg/40" : ""}>
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-[12px]">module {m.name}</td>
                  <td className="whitespace-nowrap px-3 py-2">
                    <span data-check="init" data-ok={String(m.init_ok)} className="mr-1">
                      <CheckPill ok={m.init_ok} yes="init ok" no="init failed" unknown="init not run" />
                      <span className="sr-only">{reason}</span>
                    </span>
                    <span data-check="validate" data-ok={String(m.validate_ok)}>
                      <CheckPill ok={m.validate_ok} yes="valid" no="invalid" unknown="validate not run" />
                    </span>
                  </td>
                  <td className={`px-3 py-2 ${reason ? "text-error" : "text-slate"}`}>
                    {reason}
                    {m.log && !/^init skipped/.test(m.log) ? (
                      <details className="mt-1 text-slate">
                        <summary className="cursor-pointer text-[12px]">log</summary>
                        <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-page p-2 font-mono text-[11.5px]">{m.log}</pre>
                      </details>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {(validation.notes ?? []).map((n) => (
        <p key={n} className="text-[13px] text-decision">
          {n}
        </p>
      ))}
    </div>
  );
}

/** The generated infrastructure: Terraform, Terragrunt, Dockerfile and pipelines, with validation. */
export function InfraPanel({ buildId }: { buildId: string }) {
  const infra = useInfra(buildId);
  const files = useBuildFiles(buildId);
  const [selected, setSelected] = useState<string | null>(null);
  const groups = useMemo(() => {
    const list = infra.data?.files ?? [];
    return KINDS.map((k) => ({ ...k, files: list.filter((f) => (KINDS.some((x) => x.kind === f.kind) ? f.kind : "other") === k.kind) })).filter(
      (g) => g.files.length > 0,
    );
  }, [infra.data]);
  const entry = files.data?.files.find((f) => f.path === selected) ?? null;
  const v = infra.data?.validation ?? null;

  return (
    <Panel
      title="Infrastructure"
      eyebrow="Terraform, Terragrunt, container, pipelines"
      testId="infra-panel"
      id="infra"
      actions={
        infra.data ? (
          <>
            <Pill tone="det">{infra.data.files.length} files</Pill>
            <Pill tone="purple">{infra.data.runtime === "aks" ? "AKS" : "Container Apps"}</Pill>
            <CheckPill ok={v?.ok} yes="validation passed" no="validation failed" />
          </>
        ) : null
      }
    >
      {infra.error ? <ErrorNote error={infra.error} /> : null}
      <ValidationRows validation={v} testId="infra-validation" />
      <div className="mt-4 grid items-start gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="max-h-[560px] overflow-y-auto rounded-btn border border-line p-2" data-testid="infra-tree">
          {groups.map((g) => (
            <details key={g.kind} open={g.open} className="group/k">
              <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded px-1 py-1 text-[13px] font-semibold text-plum hover:bg-warm">
                <ChevronRight size={13} className="text-slate transition-transform group-open/k:rotate-90" aria-hidden />
                {g.label}
                <span className="font-normal text-slate">{g.files.length}</span>
              </summary>
              <ul className="ml-4 border-l border-line pl-1.5">
                {g.files.map((f) => (
                  <li key={f.path}>
                    <button
                      type="button"
                      onClick={() => setSelected(f.path)}
                      className={`w-full truncate rounded px-1 py-0.5 text-left font-mono text-[11.5px] ${
                        selected === f.path ? "bg-purple-lt text-purple-dk" : "text-ink hover:bg-warm"
                      }`}
                      title={f.path}
                    >
                      {f.path}
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </div>
        <CodeViewer buildId={buildId} entry={entry} empty="Select an infrastructure file to see it here." testId="infra-viewer" />
      </div>
    </Panel>
  );
}
