// Typed client over the ExiQO Migrate API. Every request carries the chosen user as X-User.
import type {
  AnalysisView,
  AskAnswer,
  AuditEntry,
  DesignRecord,
  Domain,
  EstateGraph,
  FileEntry,
  Gate,
  IOCardData,
  Job,
  ProcessCanvasData,
  ProcessSummary,
  Project,
  ScanReport,
  ScenarioSet,
  UploadResult,
} from "./types";
import type {
  AzureConnectBody,
  AzureConnection,
  BuildFileContent,
  BuildFiles,
  BuildSummary,
  BuildView,
  EvalView,
  HistoryView,
  InfraView,
  ParityView,
  PlanOut,
  PlanStart,
  ReadinessView,
  ReviewFinding,
  RunState,
  Scenario,
  ScenarioList,
  SettingsPatch,
  SettingsView,
  TestData,
  TestReport,
} from "./buildTypes";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

let currentUser = "sme.vicky";

export function setApiUser(user: string): void {
  currentUser = user;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail || `HTTP ${status}`);
  }
}

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("X-User", currentUser);
  if (init.body && typeof init.body === "string" && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, `The API at ${API_URL} is not reachable`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  return (await request(path, init)).json() as Promise<T>;
}

/** null when the route answers 404 (not created yet, or not available in this API build). */
async function optional<T>(path: string): Promise<T | null> {
  try {
    return await json<T>(path);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 405)) return null;
    throw e;
  }
}

const post = (body?: unknown): RequestInit => ({ method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => json<{ status: string; provider: string }>("/health"),

  listProjects: () => json<Project[]>("/projects"),
  createProject: (name: string) => json<Project>("/projects", post({ name })),

  uploadZip: (projectId: string, file: Blob, filename: string) => {
    const form = new FormData();
    form.append("file", file, filename);
    return json<UploadResult>(`/projects/${projectId}/upload`, { method: "POST", body: form });
  },
  uploadPath: (projectId: string, path: string) => json<UploadResult>(`/projects/${projectId}/upload`, post({ path })),
  files: (projectId: string) => json<FileEntry[]>(`/projects/${projectId}/files`),
  scan: (projectId: string, uploadId?: string) =>
    json<{ job_id: string }>(`/projects/${projectId}/scan`, post(uploadId ? { upload_id: uploadId } : {})),
  report: (projectId: string) => optional<ScanReport>(`/projects/${projectId}/report`),
  processes: (projectId: string) => json<ProcessSummary[]>(`/projects/${projectId}/processes`),

  job: (jobId: string) => json<Job>(`/jobs/${jobId}`),

  canvas: (processId: string) => json<ProcessCanvasData>(`/processes/${processId}/canvas`),
  ioCard: (processId: string) => json<IOCardData>(`/processes/${processId}/io-card`),
  xml: async (processId: string) => (await request(`/processes/${processId}/xml`)).text(),

  // Task 18
  domains: (projectId: string) => optional<Domain[]>(`/projects/${projectId}/domains`),
  renameDomain: (domainId: string, name: string) =>
    json<Domain>(`/domains/${domainId}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  mergeDomain: (fromId: string, intoId: string) => json<Domain>(`/domains/${fromId}/merge/${intoId}`, post()),
  nameDomains: (projectId: string) => json<{ job_id: string }>(`/projects/${projectId}/domains/name`, post()),
  graph: (projectId: string) => optional<EstateGraph>(`/projects/${projectId}/graph`),
  gates: (processId: string) => optional<Gate[]>(`/processes/${processId}/gates`),
  analysis: (processId: string) => optional<AnalysisView>(`/processes/${processId}/analysis`),
  analyse: (processId: string) => json<{ job_id: string }>(`/processes/${processId}/analyse`, post()),
  patchRule: (ruleId: string, body: { status?: "approved" | "rejected" | "draft"; text?: string }) =>
    json<AnalysisView>(`/rules/${ruleId}`, { method: "PATCH", body: JSON.stringify(body) }),
  patchFinding: (findingId: string, body: { decision: string; note?: string | null }) =>
    json<AnalysisView>(`/findings/${findingId}`, { method: "PATCH", body: JSON.stringify(body) }),
  decideGate: (processId: string, gate: string, state: "approved" | "rejected", note?: string) =>
    json<Gate>(`/gates/${processId}/${gate}`, post({ state, note: note ?? null })),
  audit: (processId: string) => json<AuditEntry[]>(`/processes/${processId}/audit`),
  ask: (projectId: string, question: string) => json<AskAnswer>("/ask", post({ project_id: projectId, question })),

  // Task 19
  generateDesign: (processId: string) => json<{ job_id: string }>(`/processes/${processId}/design`, post()),
  design: async (processId: string): Promise<DesignView | null> => {
    const body = await optional<Record<string, unknown>>(`/processes/${processId}/design`);
    return body ? toDesignView(body) : null;
  },
  scenarios: (processId: string) => optional<ScenarioSet>(`/processes/${processId}/scenarios`),
  settings: () => optional<Record<string, unknown>>("/settings"),

  // Task 22: Build, Verify, Deploy, Settings, History, Readiness
  startBuild: (processId: string, runtime: "container_app" | "aks") =>
    json<{ job_id: string; build_id: string }>(`/processes/${processId}/build`, post({ runtime })),
  builds: (processId: string) => optional<BuildSummary[]>(`/processes/${processId}/builds`),
  build: (buildId: string) => json<BuildView>(`/builds/${buildId}`),
  buildFiles: (buildId: string) => json<BuildFiles>(`/builds/${buildId}/files`),
  buildFile: (buildId: string, path: string) =>
    json<BuildFileContent>(`/builds/${buildId}/files/${path.split("/").map(encodeURIComponent).join("/")}`),
  buildTests: (buildId: string) => json<TestReport>(`/builds/${buildId}/tests`),
  buildReviews: (buildId: string) => json<ReviewFinding[]>(`/builds/${buildId}/reviews`),
  infra: (buildId: string) => json<InfraView>(`/builds/${buildId}/infra`),

  runState: (buildId: string) => json<RunState>(`/builds/${buildId}/run`),
  startService: (buildId: string) => json<RunState>(`/builds/${buildId}/run`, post()),
  stopService: (buildId: string) =>
    json<{ stopped: boolean; running: false }>(`/builds/${buildId}/run`, { method: "DELETE" }),

  scenarioList: (processId: string) => optional<ScenarioList>(`/processes/${processId}/scenarios`),
  /** POST .../scenarios/generate; an API without that route regenerates with GET ?refresh=true. */
  generateScenarios: async (processId: string): Promise<ScenarioList | null> => {
    try {
      return await json<ScenarioList>(`/processes/${processId}/scenarios/generate`, post());
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 405))
        return json<ScenarioList>(`/processes/${processId}/scenarios?refresh=true`);
      throw e;
    }
  },
  addScenario: (processId: string, name: string, input: Record<string, unknown>) =>
    json<Scenario & { notes: string[] }>(`/processes/${processId}/scenarios`, post({ name, input })),
  verify: (buildId: string) => json<{ job_id: string }>(`/builds/${buildId}/verify`, post()),
  parity: (buildId: string) => optional<ParityView>(`/builds/${buildId}/parity`),
  testData: (processId: string) => optional<TestData>(`/processes/${processId}/test-data`),

  azure: (projectId: string) => json<AzureConnection>(`/projects/${projectId}/azure`),
  connectAzure: (projectId: string, body: AzureConnectBody) =>
    json<AzureConnection>(`/projects/${projectId}/azure`, post(body)),
  disconnectAzure: (projectId: string) =>
    json<AzureConnection>(`/projects/${projectId}/azure`, { method: "DELETE" }),
  startPlan: (buildId: string) => json<PlanStart>(`/builds/${buildId}/plan`, post()),
  plan: (buildId: string) => optional<PlanOut>(`/builds/${buildId}/plan`),
  acknowledgePlan: (buildId: string, note: string) => json<PlanOut>(`/builds/${buildId}/plan/acknowledge`, post({ note })),

  settingsView: () => json<SettingsView>("/settings"),
  putSettings: (body: SettingsPatch) => json<SettingsView>("/settings", { method: "PUT", body: JSON.stringify(body) }),
  startEval: (processIds: string[]) =>
    json<{ job_id: string; eval_id: string }>("/providers/eval", post({ process_ids: processIds })),
  evalView: (evalId: string) => json<EvalView>(`/providers/eval/${evalId}`),
  history: (projectId: string) => json<HistoryView>(`/history?project_id=${encodeURIComponent(projectId)}`),
  readiness: (projectId: string) => json<ReadinessView>(`/projects/${projectId}/readiness`),
};

export interface DesignView {
  record: DesignRecord;
  created_at?: string | null;
  job_id?: string | null;
  ai_available?: boolean;
  ai_reason?: string | null;
  version?: number | null;
  stale?: boolean;
  stale_reason?: string | null;
}

/** The record either bare or wrapped as {record, ...meta}; lists default to empty. */
function toDesignView(body: Record<string, unknown>): DesignView {
  const raw = (body.record && typeof body.record === "object" ? body.record : body) as Partial<DesignRecord>;
  const record: DesignRecord = {
    ...raw,
    solution: raw.solution ?? { projects: [] },
    mapping: raw.mapping ?? [],
    contracts: raw.contracts ?? [],
    config: raw.config ?? [],
    config_ignored: raw.config_ignored ?? [],
    messaging: raw.messaging ?? [],
    resilience: raw.resilience ?? [],
    sequence: raw.sequence ?? [],
    decisions: raw.decisions ?? [],
    open_questions: raw.open_questions ?? [],
    expected_differences: raw.expected_differences ?? [],
    narrative: raw.narrative ?? null,
    design_hash: raw.design_hash ?? "",
  };
  return {
    record,
    created_at: (body.created_at as string | undefined) ?? null,
    job_id: (body.job_id as string | undefined) ?? null,
    ai_available: body.ai_available as boolean | undefined,
    ai_reason: (body.ai_reason as string | undefined) ?? null,
    version: (body.version as number | undefined) ?? null,
    stale: body.stale === true,
    stale_reason: (body.stale_reason as string | undefined) ?? null,
  };
}
