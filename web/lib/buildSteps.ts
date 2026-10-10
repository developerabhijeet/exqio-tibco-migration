// The build's step list, from the job's stage markers (live) and the finished build record.
import type { BuildView } from "./buildTypes";
import type { JobEvent } from "./types";

export const BUILD_STEPS = [
  "generate",
  "tests",
  "builder",
  "review",
  "restore",
  "build",
  "format",
  "test",
  "repair",
  "infra",
  "validate",
  "metrics",
] as const;
export type BuildStepName = (typeof BUILD_STEPS)[number];

/** The job's stage names; "infra validate" is the validate step. */
export const JOB_STAGES = [
  "design check",
  "generate",
  "tests",
  "test engineer",
  "builder",
  "review",
  "restore",
  "build",
  "format",
  "test",
  "repair",
  "infra",
  "infra validate",
  "metrics",
  "persist",
];

const LABELS: Record<BuildStepName, string> = {
  generate: "Generate solution",
  tests: "Write tests",
  builder: "Code Builder",
  review: "Review panel",
  restore: "dotnet restore",
  build: "dotnet build",
  format: "dotnet format",
  test: "dotnet test",
  repair: "Repair loop",
  infra: "Generate infrastructure",
  validate: "Validate infrastructure",
  metrics: "Metrics",
};

const AI_STEPS = new Set<BuildStepName>(["builder", "review", "repair"]);

export type StepRunStatus = "pending" | "running" | "passed" | "failed" | "skipped" | "warning";

export interface BuildStepRow {
  name: BuildStepName;
  label: string;
  tag: "code" | "ai";
  status: StepRunStatus;
  /** Replaces the status pill's text (dotnet format after a failed restore is a reduced check). */
  statusText?: string;
  summary: string;
  log: string;
}

/** The step a job stage marker belongs to ("infra validate" is the validate step), or null. */
export const stepOfStage = (stage: string): BuildStepName | null =>
  stage === "infra validate" ? "validate" : (BUILD_STEPS as readonly string[]).includes(stage) ? (stage as BuildStepName) : null;

/** Log lines grouped under the stage marker that precedes them. */
export function linesByStage(events: JobEvent[]): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  let stage = "";
  for (const e of events) {
    if (e.event === "stage") {
      stage = e.text;
      out[stage] = out[stage] ?? [];
    } else if (e.event === "log" || e.event === "error") {
      (out[stage] = out[stage] ?? []).push(e.event === "error" ? `error: ${e.text}` : e.text);
    }
  }
  return out;
}

function fromLines(lines: string[]): StepRunStatus {
  const text = lines.join("\n").toLowerCase();
  if (/\bnot run\b|\bskipped\b/.test(text)) return "skipped";
  if (/\bfailed\b|^error:/m.test(text)) return "failed";
  return "passed";
}

export function buildStepRows(
  events: JobEvent[],
  jobStatus: "running" | "done" | "failed" | null,
  build: BuildView | null,
  infraFiles: number,
): BuildStepRow[] {
  const groups = linesByStage(events);
  const stageEvents = events.filter((e) => e.event === "stage").map((e) => e.text);
  const current = jobStatus === "running" ? stepOfStage(stageEvents[stageEvents.length - 1] ?? "") : null;
  const reached = new Set(stageEvents.map(stepOfStage).filter(Boolean) as BuildStepName[]);
  const finished = !!build && (build.status === "passed" || build.status === "failed") && !!build.finished_at;
  const failedAt = jobStatus === "failed" ? stepOfStage(stageEvents[stageEvents.length - 1] ?? "") : null;

  return BUILD_STEPS.map((name): BuildStepRow => {
    const stageName = name === "validate" ? "infra validate" : name;
    const lines = groups[stageName] ?? [];
    const record = build?.steps.find((s) => s.name === name);
    let status: StepRunStatus = "pending";
    let summary = lines[lines.length - 1] ?? "";
    let log = lines.join("\n");

    if (current === name) status = "running";
    else if (failedAt === name) status = "failed";
    else if (reached.has(name)) status = fromLines(lines);

    if (finished && build) {
      if (record) {
        status = record.ok ? "passed" : record.skipped ? "skipped" : "failed";
        log = record.log || log;
        summary = record.log.split("\n").find((l) => l.trim()) ?? summary;
      } else if (name === "builder") {
        const b = build.builder;
        if (!b) status = "skipped";
        else {
          status = b.rejections.length > 0 && b.filled.length === 0 ? "warning" : "passed";
          summary = `${b.filled.length} file(s) filled, ${b.rejections.length} reply(ies) rejected by the code checks`;
          if (b.rejections.length > 0)
            log = [log, ...b.rejections.map((r) => `rejected ${r.path} (attempt ${r.attempt}): ${r.reason}`)]
              .filter(Boolean)
              .join("\n");
        }
      } else if (name === "repair") {
        const r = build.repair;
        if (!r) status = "skipped";
        else status = r.final?.ok ? "passed" : "failed";
        if (r) summary = `${r.attempts.length} attempt(s)`;
      } else if (name === "infra") {
        status = infraFiles > 0 ? "passed" : reached.has("infra") ? fromLines(lines) : "skipped";
      } else if (name === "validate") {
        const v = build.infra_validation;
        status = v ? (v.ok ? "passed" : "failed") : "skipped";
        if (v?.notes?.length) summary = v.notes[0];
      } else if (name === "metrics") {
        status = build.metrics ? "passed" : "failed";
      } else if (status === "pending") {
        // A finished build whose job log is not loaded: the generation stages ran.
        status = "passed";
      }
    }
    let statusText: string | undefined;
    if (finished && build && name === "format" && status === "passed") {
      const restore = build.steps.find((s) => s.name === "restore");
      if (restore && !restore.ok) {
        statusText = "passed (reduced check)";
        const detail = build.metrics?.details?.standards_adherence ?? "";
        const reduced = /\(reduced check:[^)]*\)/.exec(detail)?.[0];
        if (reduced) summary = reduced.slice(1, -1);
      }
    }
    return { name, label: LABELS[name], tag: AI_STEPS.has(name) ? "ai" : "code", status, statusText, summary, log };
  });
}
