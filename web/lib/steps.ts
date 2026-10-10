"use client";

// Step status for the stepper. Upload and Discover come from the scan report and the domain list;
// Validate to Deploy come from the open process's gates (G1 to G6) once the API exposes them,
// and read "not started" otherwise.
import { useMemo } from "react";
import { useDomainList } from "./domains";
import { useAnalysis, useGates, useGraph, useProcesses, useReport } from "./queries";
import { useAppState } from "./state";
import type { Gate, StepState, StepStatus } from "./types";

export const STEP_LABELS: Record<string, string> = {
  upload: "Upload",
  discover: "Discover",
  validate: "Validate",
  design: "Design",
  build: "Build",
  verify: "Verify",
  deploy: "Deploy",
};

const GATES_BY_STEP: Record<string, string[]> = {
  validate: ["G1", "G2"],
  design: ["G3"],
  build: ["G4"],
  verify: ["G5"],
  deploy: ["G6"],
};

const GATE_NAMES: Record<string, string> = {
  G1: "G1 rules",
  G2: "G2 findings",
  G3: "G3 design",
  G4: "G4 build",
  G5: "G5 parity",
  G6: "G6 deploy",
};

function gateStatus(gates: Gate[], analysed = false): StepStatus {
  if (gates.length === 0) return "not_started";
  if (gates.some((g) => g.state === "rejected")) return "failed";
  if (gates.every((g) => g.state === "approved")) return "approved";
  if (gates.some((g) => g.state === "needs_review" || g.state === "approved" || g.enabled || g.reset_reason))
    return "needs_review";
  return analysed ? "needs_review" : "not_started";
}

function gateEvidence(g: Gate): string {
  if (g.state === "approved") return `approved by ${g.decided_by ?? "unknown"}${g.note ? `: ${g.note}` : ""}`;
  if (g.state === "rejected") return `rejected by ${g.decided_by ?? "unknown"}${g.note ? `: ${g.note}` : ""}`;
  if (g.reset_reason) return `pending (reset: ${g.reset_reason})`;
  if (g.enabled === false && g.reason) return `pending: ${g.reason}`;
  return "pending";
}

export function useStepStates(): StepState[] {
  const { projectId, processId, scan } = useAppState();
  const report = useReport(projectId);
  const processes = useProcesses(projectId);
  const { domains } = useDomainList(projectId);
  const graph = useGraph(projectId);
  const gates = useGates(processId);
  const analysis = useAnalysis(processId);

  return useMemo(() => {
    const r = report.data ?? null;
    const procName = processes.data?.find((p) => p.id === processId)?.name;
    const scanning = scan && scan.projectId === projectId && scan.status === "running";
    const scanFailed = scan && scan.projectId === projectId && scan.status === "failed";

    let upload: StepStatus = "not_started";
    if (scanning) upload = "running";
    else if (scanFailed) upload = "failed";
    else if (r) upload = r.ok && r.unknown_activity_types.length === 0 ? "complete" : "needs_review";
    const uploadEvidence = r
      ? [
          {
            label: "Parse report",
            detail: `${r.files_parsed} of ${r.files_found} files parsed; ${r.unknown_activity_types.length} unknown activity types; ${r.unknown_elements.length} unknown elements; ${r.explained.length} elements explained`,
          },
          { label: "Scan job", detail: r.job_id ?? "not recorded" },
          ...(r.synthetic ? [{ label: "Estate", detail: "synthetic (SYNTHETIC.txt at the scan root)" }] : []),
        ]
      : [];

    const g = graph.data;
    const discover: StepStatus = domains.length > 0 ? "complete" : scanning ? "running" : "not_started";
    const discoverEvidence =
      domains.length > 0
        ? [
            { label: "Domain list", detail: `${domains.length} domains built from the parsed model only` },
            ...(g ? [{ label: "Domain graph", detail: `${g.nodes.length} nodes, ${g.edges.length} edges` }] : []),
            ...(procName ? [{ label: "I/O card", detail: procName }] : []),
          ]
        : [];

    const gateList = gates.data ?? [];
    const later = (key: string): StepState => {
      const mine = gateList.filter((x) => GATES_BY_STEP[key].includes(x.gate));
      return {
        key,
        label: STEP_LABELS[key],
        status: gateStatus(mine, key === "validate" && !!analysis.data?.analysed),
        evidence: mine.map((x) => ({
          label: `${GATE_NAMES[x.gate] ?? x.gate}${procName ? ` for ${procName}` : ""}`,
          detail: gateEvidence(x),
        })),
      };
    };

    return [
      { key: "upload", label: "Upload", status: upload, evidence: uploadEvidence },
      { key: "discover", label: "Discover", status: discover, evidence: discoverEvidence },
      later("validate"),
      later("design"),
      later("build"),
      later("verify"),
      later("deploy"),
    ];
  }, [report.data, processes.data, processId, scan, projectId, domains, graph.data, gates.data, analysis.data]);
}
