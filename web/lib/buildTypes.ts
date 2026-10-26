// Shapes of the Build, Verify, Deploy, Settings, History and Readiness routes (Task 19 API).
import type { CitedText } from "./citations-model";
import type { Gate } from "./types";

export interface BuildSummary {
  id: string;
  status: "queued" | "running" | "passed" | "failed" | string;
  runtime: string;
  build_hash: string | null;
  job_id: string | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface BuildStepRecord {
  name: string;
  ok: boolean;
  skipped?: boolean;
  log: string;
  duration_ms?: number | null;
}

export interface Metrics {
  rule_carry_forward: number | null;
  test_pass_rate: number | null;
  branch_coverage: number | null;
  code_coverage: number | null;
  standards_adherence: number | null;
  compile_ok: boolean | null;
  warnings: number | null;
  infra_ok: boolean | null;
  /** The five quality metrics as one weighted number; absent on builds made before it existed. */
  conversion_accuracy?: number | null;
  formulas: Record<string, string>;
  details: Record<string, string>;
}

export interface InfraModuleCheck {
  name: string;
  init_ok: boolean | null;
  validate_ok: boolean | null;
  log?: string;
  registry_unreachable?: boolean;
}

export interface InfraValidation {
  ok?: boolean;
  fmt_ok?: boolean | null;
  hclfmt_ok?: boolean | null;
  modules?: InfraModuleCheck[];
  tools_present?: Record<string, boolean>;
  registry_unreachable?: boolean;
  notes?: string[];
}

/** A soft assertion the Test Engineer could not make exact (a newer API reports these). */
export interface TestWarning {
  path: string;
  method: string;
  reason: string;
}

/** A reply the code checks refused: why, and the body it refused, so a person can judge the call. */
export interface BuildRejection {
  path: string;
  attempt?: number;
  reason: string;
  body?: string;
}

export interface BuilderResult {
  filled: string[];
  rejections: BuildRejection[];
  provider?: string | null;
  ai_available?: boolean;
  test_engineer?: {
    filled?: string[];
    rejections?: BuildRejection[];
    warnings?: TestWarning[];
  } | null;
}

export interface RepairAttempt {
  n: number;
  failing: string[];
  diff: string;
  applied: boolean;
  reason?: string | null;
}

export interface BuildView {
  id: string;
  process_id: string;
  job_id: string | null;
  status: string;
  runtime: string;
  build_hash: string | null;
  spec_hash: string | null;
  design_hash: string | null;
  created_at: string | null;
  finished_at: string | null;
  error: string | null;
  metrics: Metrics | null;
  steps: BuildStepRecord[];
  repair: { attempts: RepairAttempt[]; final?: { ok: boolean; cases?: unknown[] } } | null;
  builder: BuilderResult | null;
  infra_validation: InfraValidation | null;
  g4?: Gate;
}

export interface TibcoRefRow {
  activity_id: string;
  activity_name: string;
  pattern: string;
  class_name: string;
  method: string;
  rule_ids: string[];
  finding_ids: string[];
  origin: string;
}

export interface TibcoRef {
  process_id: string;
  source_path: string;
  activity_ids: string[];
  source_lines: Record<string, number>;
  rows: TibcoRefRow[];
}

export interface BuildFileEntry {
  path: string;
  origin: "code" | "agent" | "ai" | "infra" | string;
  process_id: string | null;
  size: number;
  sha256: string;
  tibco_ref: TibcoRef | null;
}

export interface BuildFiles {
  build_id: string;
  files: BuildFileEntry[];
  counts: Record<string, number>;
}

export interface BuildFileContent extends BuildFileEntry {
  content: string;
  /** `content` with each line masked by the API (configured sensitive fields), line for line: the
   * text to quote from the file anywhere but the code viewer. */
  content_masked?: string;
  language: string;
}

export interface TestCase {
  name: string;
  display_name: string | null;
  kind: "rule" | "branch" | "scenario" | "other" | string;
  rule_key: string | null;
  ref: string | null;
  file: string | null;
  outcome: "passed" | "failed" | "skipped" | "not_run" | string;
  has_body: boolean;
  message: string | null;
  duration_ms: number | null;
}

export interface TestReport {
  build_id: string;
  cases: TestCase[];
  summary: { total: number; passed: number; failed: number; not_run: number; no_body: number };
}

export interface ReviewFinding {
  reviewer: "equivalence" | "security" | "standards" | string;
  file: string | null;
  line: number | null;
  text: string;
  triage: "patch" | "defer" | "decision" | string;
  /** The engineer's verdict. "keep" is applied by the next build; "reject" never is. */
  decision?: "keep" | "reject" | null;
  decided_by?: string | null;
  decided_at?: string | null;
  note?: string | null;
  /** The build that acted on a kept finding, once one has. */
  applied_in?: string | null;
  /** The text with a chip for its file and line (and named activities). `cited.text` is the
   * MASKED text and is what is rendered; null on a finding with no text. */
  cited?: CitedText | null;
}

export interface InfraFile {
  path: string;
  kind: "terraform" | "terragrunt" | "dockerfile" | "pipeline" | "other" | string;
  sha256: string;
  validation: Record<string, unknown>;
}

export interface InfraView {
  build_id: string;
  runtime: string;
  files: InfraFile[];
  validation: InfraValidation | null;
}

export interface RunState {
  running: boolean;
  url: string | null;
  port: number | null;
  pid: number | null;
  swagger_url: string | null;
  started_at: string | null;
  exit_code: number | null;
  log_tail?: string;
  /** Why the service is not running when the API knows, e.g. "the worker hosting the service is gone". */
  reason?: string | null;
}

export interface Scenario {
  id: string;
  key: string;
  name: string;
  kind: "happy" | "branch" | "fault" | "external_failure" | "sme" | string;
  origin: string;
  created_by: string | null;
  input: Record<string, unknown>;
  expected?: Record<string, unknown>;
  created_at?: string | null;
}

export interface ScenarioList {
  process_id: string;
  label?: string;
  notes: string[];
  database?: string;
  scenarios: Scenario[];
}

export interface FieldDiff {
  path: string;
  /** RAW values: render `expected_display` / `actual_display` (masked by the API) instead. */
  expected: unknown;
  actual: unknown;
  /** The value as shown, MASKED by the API: "absent", "null", "***" for a sensitive field, else JSON. */
  expected_display?: string;
  actual_display?: string;
  status: "match" | "defect" | "intended" | "noise" | string;
  finding_id: string | null;
  /** false on an intended difference no TIBCO sandbox run has confirmed (newer API). */
  confirmed?: boolean;
  /** "control" on a defect a Fix finding touches only through a branch decision; it stays a defect (newer API). */
  via?: "control" | string;
  /** "<path>: expected <e>, got <a>." plus its note, with chips for the scenario run and the
   * finding. MASKED (values of sensitive fields never show); absent on a match no finding explains. */
  cited?: CitedText | null;
}

export interface ScenarioReport {
  scenario_id: string;
  scenario_key?: string;
  scenario_name?: string;
  run_id?: string;
  status: "match" | "differs" | "error" | string;
  error?: string | null;
  fields?: FieldDiff[];
  side_effects?: FieldDiff[];
  parity?: number | null;
  unexplained?: number;
}

export interface ParityView {
  build_id: string;
  job_id: string | null;
  created_at: string | null;
  label: string;
  parity: number | null;
  unexplained: number;
  matched: number;
  compared: number;
  intended: number;
  errors: number;
  /** Intended differences derived from the TIBCO model and not confirmed by a sandbox run. */
  unconfirmed_intended?: number;
  unconfirmed_note?: string;
  /** Defects a Fix finding touches only through a branch decision (newer API). */
  control_only_defects?: number;
  noise_rules: { path_glob: string; reason: string }[];
  reports: ScenarioReport[];
}

export interface TestData {
  process_id: string;
  note: string;
  label?: string;
  masked_fields: string[];
  masking: string;
  inputs: { scenario: string; name: string; kind: string; input: Record<string, unknown>; seed_state: unknown[] }[];
  tables: { table: string; operations: string[]; rows: Record<string, unknown>[] }[];
  notes: string[];
}

export interface AzureConnection {
  connected: boolean;
  project_id?: string;
  tenant?: string;
  subscription?: string;
  settings?: Record<string, string | null>;
  /** The deploy environment: credentials and infra/live/<environment> (dev when null). */
  environment?: string | null;
  auth_mode?: "local" | DeployAuth;
  client_id?: string | null;
  key_vault_uri?: string | null;
  secret_name?: string | null;
  secret_stored?: boolean;
  warning?: string | null;
  updated_at?: string | null;
}

export type DeployAuth = "workload_identity" | "keyvault_sp";

/** One declared Azure target (GET /deploy/environments): ids and where a secret lives, no value. */
export interface DeployEnvironment {
  name: string;
  tenant: string;
  subscription: string;
  client_id: string;
  auth: DeployAuth;
  key_vault_uri: string;
  secret_name: string;
}

export interface DeployEnvironments {
  mode: "workload_identity" | "local";
  environments: DeployEnvironment[];
}

export interface AzureConnectBody {
  tenant: string;
  subscription: string;
  resource_group?: string;
  container_registry?: string;
  runtime?: "container_app" | "aks";
  service_bus_namespace?: string;
  key_vault?: string;
  log_analytics?: string;
  location?: string;
  client_id?: string;
  client_secret?: string;
}

export interface PlanUnit {
  unit: string;
  add: number;
  change: number;
  destroy: number;
}

export interface PlanOut {
  id: string;
  build_id: string;
  job_id: string | null;
  connected: boolean;
  status: "connect_later" | "acknowledged" | "running" | "planned" | "failed" | string;
  plan_hash: string | null;
  summary: { units: PlanUnit[]; add: number; change: number; destroy: number } | null;
  output: string | null;
  last_validation: InfraValidation | null;
  note: string | null;
  decided_by: string | null;
  apply?: { enabled: boolean; reason: string };
  created_at: string | null;
}

export type PlanStart =
  | { connected: false; reason: string; last_validation: InfraValidation | null; plan: PlanOut }
  | { connected: true; job_id: string; plan_id: string };

export interface ToolInfo {
  present: boolean;
  version: string | null;
  path: string | null;
}

export interface ProviderInfo {
  name: string;
  role: string;
  configured: boolean;
  env: string[];
  endpoint_set?: boolean;
  key_set?: boolean;
  deployments_set?: Record<string, boolean>;
}

export interface SettingsView {
  provider: { choice: string; active: string };
  providers: ProviderInfo[];
  models: {
    tiers: Record<string, { claude: string; azure_deployment_set: boolean }>;
    stages: { stage: string; task_kind: string; weight: number; tier: string }[];
  };
  routing: { bands: Record<string, [number, number]>; fallback: string; complexity: string };
  toolchains: Record<string, ToolInfo>;
  langfuse: { enabled: boolean; host: string | null; keys_configured: boolean };
  /** Bring your own Azure OpenAI key. The key itself is never sent to the browser. */
  azure_openai: {
    endpoint: string;
    key_set: boolean;
    deployments: Record<string, string>;
    source: "stored" | "environment" | "not configured" | string;
    app_secret_configured: boolean;
  };
  app_secret_configured: boolean;
  scenario_database: string;
}

export interface SettingsPatch {
  models?: Record<string, string>;
  routing_bands?: Record<string, [number, number]>;
  langfuse_enabled?: boolean;
  azure?: {
    endpoint?: string;
    api_key?: string;
    deployments?: Record<string, string>;
    clear?: boolean;
  };
}

export interface EvalRow {
  provider: string;
  model: string;
  process_id: string;
  process_name: string;
  citations_verified: number;
  citations_total: number;
  coverage: number;
  coverage_total: number;
  challenger_agree: number | null;
  rules: number;
  latency_ms: number;
  cost_usd: number;
  error: string | null;
}

export interface EvalView {
  id: string;
  job_id: string | null;
  status: string;
  process_ids: string[];
  rows: EvalRow[];
  created_at: string | null;
}

export interface Totals {
  jobs: number;
  model_calls: number;
  failed_calls: number;
  tokens_in: number;
  tokens_out: number;
  tokens_cached: number;
  cost_usd: number;
  latency_ms: number;
  decisions: number;
  workflows?: number;
}

export type TimelineEntry =
  | {
      ts: string;
      kind: "job";
      id: string;
      job_kind: string;
      status: string;
      stage: string | null;
      phase: string;
      process_id: string | null;
      process_name: string | null;
      duration_ms: number | null;
      error: string | null;
    }
  | {
      ts: string;
      kind: "model_call";
      id: string;
      job_id: string | null;
      phase: string;
      stage: string;
      agent: string;
      provider: string;
      model: string;
      tier: string;
      tokens_in: number;
      tokens_out: number;
      tokens_cached: number;
      cost_usd: number;
      latency_ms: number;
      ok: boolean;
      error: string | null;
      fallback_from: string | null;
      process_id: string | null;
      process_name: string | null;
    }
  | {
      ts: string;
      kind: "decision";
      id: number;
      actor: string;
      action: string;
      gate: string | null;
      process_id: string | null;
      process_name: string | null;
      detail: Record<string, unknown>;
    };

/** One agent identity's share of the spend: the `stage` recorded on every model call. */
export interface AgentTotals {
  agent: string;
  model_calls: number;
  failed_calls: number;
  fallbacks: number;
  tokens_in: number;
  tokens_out: number;
  tokens_cached: number;
  cost_usd: number;
  latency_ms: number;
  latency_ms_avg: number;
  models: string[];
  providers: string[];
  tiers: string[];
}

/** One run (a job, or the Ask calls made outside any job) with its own per-agent split. */
export interface AgentSession {
  job_id: string | null;
  job_kind: string;
  phase: string;
  status: string | null;
  process_id: string | null;
  process_name: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  totals: Totals;
  by_agent: AgentTotals[];
}

export interface HistoryView {
  project_id: string;
  timeline: TimelineEntry[];
  totals: { all: Totals; by_phase: Record<string, Totals>; by_band: Record<string, Totals>; cache_hit_rate: number };
  by_agent: AgentTotals[];
  sessions: AgentSession[];
  workflows: (Totals & {
    process_id: string;
    name: string;
    complexity_band: string | null;
    complexity_score: number | null;
    gates: Record<string, string>;
  })[];
  phases: string[];
}

export interface ReadinessRow {
  key: string;
  expectation: string;
  status: "met" | "partial" | "missing" | string;
  evidence: { kind: string; ref: string; label: string }[];
  gap: string | null;
  /** "<expectation>: <status>; <gap>." with one chip per evidence item on it. MASKED; rendered
   * instead of the evidence list. */
  cited?: CitedText | null;
}

export interface ReadinessView {
  project_id: string;
  rows: ReadinessRow[];
  summary: { met: number; partial: number; missing: number };
}

/** The connectors of Settings → Connectors (api/app/connectors/base.py KINDS). */
export type ConnectorKind = "github" | "atlassian" | "figma";

/** POST /connectors/{kind}/test; the message is redacted by the API. */
export interface ConnectionTest {
  ok: boolean;
  account: string | null;
  site: string | null;
  scopes: string[];
  latency_ms: number;
  message: string;
}

/** The last connection test of a connector, kept by the API. */
export interface ConnectorLastTest {
  result: ConnectionTest;
  at: string;
  actor: string;
}

/**
 * One connector of GET /settings/connectors. Everyone gets the two flags; an admin also gets the
 * effective URLs (the admin's override, else the install's), the overrides themselves, the
 * install layer, the allowlist, the defaults, the secret names, whether each secret is mounted
 * (never its value) and the last test.
 */
export interface ConnectorSettings {
  install_enabled: boolean;
  enabled: boolean;
  app_enabled?: boolean;
  overrides?: Record<string, string>;
  install?: Record<string, unknown>;
  allowed_hosts?: string[];
  secret_names?: Record<string, string>;
  secrets_present?: Record<string, boolean>;
  last_test?: ConnectorLastTest | null;
  // effective URLs
  base_url?: string;
  api_url?: string;
  web_url?: string;
  confluence_url?: string;
  jira_url?: string;
  // defaults
  default_owner?: string;
  space_key?: string;
  parent_page_id?: string;
  project_key?: string;
  issue_type?: string;
  labels?: string[];
  file_keys?: string[];
}

export type ConnectorSettingsView = Record<ConnectorKind, ConnectorSettings>;

/** GET /connectors: which connectors are on, for any signed-in user. */
export interface ConnectorFlags {
  github: { enabled: boolean };
  atlassian: { enabled: boolean; confluence: boolean; jira: boolean };
  figma: { enabled: boolean };
}

/** PUT /settings/connectors: a partial update of the admin's layer (there are no secret fields). */
export type ConnectorPatch = Partial<Record<string, string | string[] | boolean>>;
export type ConnectorSettingsPatch = Partial<Record<ConnectorKind, ConnectorPatch>>;
