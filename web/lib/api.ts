// Typed client over the ExiQO Migrate API. Every request carries the chosen user as X-User.
import type {
  AnalysisView,
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
  ScenarioView,
  UploadResult,
} from "./types";

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

  // Task 19
  scenarios: (processId: string) => optional<ScenarioView[]>(`/processes/${processId}/scenarios`),
  settings: () => optional<Record<string, unknown>>("/settings"),
};
