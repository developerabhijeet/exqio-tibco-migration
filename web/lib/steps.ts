"use client";

// Step status for the stepper. Upload and Discover come from the scan report and the domain list;
// Validate and Design come from the open process's gates (G1 to G3). Build, Verify and Deploy also
// read their runs: a running job, the latest build's steps, the parity report and the plan, so a
// failed or running build never reads "not started". Gate records decide "approved".
import { useMemo } from "react";
import { useDomainList } from "./domains";
import { useAnalysis, useGates, useGraph, useProcesses, useReport } from "./queries";
import { useBuild, useBuilds, useParity, usePlan } from "./buildQueries";
import { useJob } from "./jobs";
import type { BuildView, ParityView, PlanOut } from "./buildTypes";
import { useAppState } from "./state";
import type { EvidenceRef, Gate, StepState, StepStatus } from "./types";

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

const BUILD_STEPS = new Set(["restore", "build", "test"]);

function latestStatus(
  key: "build" | "verify" | "deploy",
  gate: Gate | undefined,
  jobRunning: boolean,
  build: BuildView | null | undefined,
  latestBuildStatus: string | undefined,
  parity: ParityView | null | undefined,
  plan: PlanOut | null | undefined,
): { status: StepStatus; evidence: EvidenceRef[] } {
  const evidence: EvidenceRef[] = [];
  let fromRun: StepStatus = "not_started";
  if (key === "build") {
    const failedStep = build?.steps.find((s) => BUILD_STEPS.has(s.name) && !s.ok && !s.skipped);
    if (latestBuildStatus === "queued" || latestBuildStatus === "running") fromRun = "running";
    else if (failedStep || latestBuildStatus === "failed") fromRun = "failed";
    else if (latestBuildStatus === "passed") fromRun = "needs_review";
    if (build) {
      evidence.push({
        label: "Latest build",
        detail: failedStep
          ? `${build.status}: ${failedStep.name} failed`
          : `${build.status}${build.build_hash ? `, build hash ${build.build_hash.slice(0, 12)}` : ""}`,
      });
    }
  } else if (key === "verify" && parity) {
    fromRun = parity.unexplained > 0 || parity.errors > 0 ? "failed" : "needs_review";
    evidence.push({
      label: "Parity report",
      detail: `${parity.parity === null ? "no parity" : `${Math.round(parity.parity * 1000) / 10}%`}; ${parity.unexplained} unexplained, ${parity.errors} errors (${parity.label})`,
    });
  } else if (key === "deploy" && plan) {
    fromRun = plan.status === "failed" ? "failed" : plan.status === "running" ? "running" : "needs_review";
    evidence.push({ label: "Plan", detail: `${plan.status.replace("_", " ")}${plan.decided_by ? ` by ${plan.decided_by}` : ""}` });
  }
  // A running job wins; then the gate record; then what the latest run says.
  let status: StepStatus = fromRun;
  if (jobRunning) status = "running";
  else if (gate?.state === "rejected") status = "failed";
  else if (gate?.state === "approved") status = "approved";
  return { status, evidence };
}

export function useStepStates(): StepState[] {
  const { projectId, processId, scan } = useAppState();
  const report = useReport(projectId);
  const processes = useProcesses(projectId);
  const { domains } = useDomainList(projectId);
  const graph = useGraph(projectId);
  const gates = useGates(processId);
  const builds = useBuilds(processId);
  const latest = builds.data?.[0] ?? null;
  const build = useBuild(latest?.id ?? null);
  const parity = useParity(latest?.id ?? null);
  const plan = usePlan(latest?.id ?? null);
  const buildJob = useJob("build", processId).stream;
  const verifyJob = useJob("verify", processId).stream;
  const planJob = useJob("plan", processId).stream;
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

    const withRuns = (key: "build" | "verify" | "deploy", running: boolean): StepState => {
      const base = later(key);
      const gate = gateList.find((x) => x.gate === GATES_BY_STEP[key][0]);
      const run = latestStatus(key, gate, running, build.data, latest?.status, parity.data, plan.data);
      return { ...base, status: run.status, evidence: [...run.evidence, ...base.evidence] };
    };

    return [
      { key: "upload", label: "Upload", status: upload, evidence: uploadEvidence },
      { key: "discover", label: "Discover", status: discover, evidence: discoverEvidence },
      later("validate"),
      later("design"),
      withRuns("build", buildJob?.status === "running"),
      withRuns("verify", verifyJob?.status === "running"),
      withRuns("deploy", planJob?.status === "running"),
    ];
  }, [
    report.data,
    processes.data,
    processId,
    scan,
    projectId,
    domains,
    graph.data,
    gates.data,
    analysis.data,
    latest,
    build.data,
    parity.data,
    plan.data,
    buildJob?.status,
    verifyJob?.status,
    planJob?.status,
  ]);
}
