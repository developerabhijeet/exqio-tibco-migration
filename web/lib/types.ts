// Shapes returned by the ExiQO Migrate API (api/app/schemas, Tasks 17 to 19).

export const INSPECTOR_TABS = ["Config", "SQL", "Mappings", "Rules", "Findings", "XML"] as const;
export type InspectorTab = (typeof INSPECTOR_TABS)[number];

export type StepStatus = "not_started" | "running" | "needs_review" | "approved" | "failed";

export interface EvidenceRef {
  label: string;
  detail?: string;
  href?: string;
}

export interface StepState {
  key: string;
  label: string;
  status: StepStatus;
  evidence: EvidenceRef[];
}

export interface Project {
  id: string;
  name: string;
  created_at: string;
}

export interface FileEntry {
  path: string;
  kind: string;
  dialect: string | null;
  sha256: string;
  size: number;
  duplicate_of: string | null;
}

export interface UploadResult {
  upload_id: string;
  scan_root: string;
  files: FileEntry[];
}

export type ReportItem = Record<string, unknown>;

export interface ScanReport {
  upload_id: string;
  job_id: string | null;
  synthetic: boolean;
  sql_files: string[];
  files_found: number;
  files_parsed: number;
  failures: ReportItem[];
  unknown_activity_types: ReportItem[];
  unknown_elements: ReportItem[];
  explained: ReportItem[];
  ok: boolean;
  created_at: string;
}

export interface ProcessSummary {
  id: string;
  ir_id: string;
  name: string;
  application: string;
  module: string;
  package: string;
  dialect: string;
  source_path: string;
  starter_kind: string | null;
  spec_hash: string;
  complexity_score: number | null;
  complexity_band: string | null;
  domain_id: string | null;
  domain_name: string | null;
  migration_status: string;
  activity_count: number;
}

export interface Job {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "failed" | string;
  stage: string | null;
  project_id: string | null;
  process_id: string | null;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type JobEventKind = "log" | "stage" | "done" | "error";

export interface JobEvent {
  event: JobEventKind;
  seq: number;
  tag: "code" | "ai" | null;
  text: string;
  data: Record<string, unknown> | null;
  ts: string;
}

export interface DomainEvidence {
  kind: string;
  text: string;
}

export interface Domain {
  id: string;
  name: string;
  suggested_name: string;
  process_ids: string[];
  ir_process_ids?: string[];
  evidence: DomainEvidence[];
  renamed_by: string | null;
}

export interface GraphNode {
  id: string;
  kind: string;
  name: string;
  attrs: Record<string, unknown>;
}

export interface GraphEdge {
  src: string;
  dst: string;
  kind: string;
  attrs: Record<string, unknown>;
}

export interface EstateGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export type CanvasNodeData = {
  name: string;
  canonical_type: string;
  native_type: string;
  source_line: number | null;
  config: Record<string, unknown>;
  rule_ids: string[];
  finding_ids: string[];
  group_id: string | null;
  starter: boolean;
  unknown: boolean;
};

export interface CanvasNode {
  id: string;
  type: string;
  data: CanvasNodeData;
}

export interface CanvasEdge {
  id: string;
  source: string;
  target: string;
  kind: "success" | "conditional" | "otherwise" | "error" | string;
  expression: string | null;
  label: string | null;
  source_line: number | null;
}

export interface CanvasGroup {
  id: string;
  kind: string;
  member_ids: string[];
  config: Record<string, unknown>;
  synthesised: boolean;
  entry_ids: string[];
}

export interface FaultHandler {
  id: string;
  fault_name: string | null;
  activity_ids: string[];
}

export interface ProcessCanvasData {
  process_id: string;
  starter_id: string | null;
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  groups: CanvasGroup[];
  fault_handlers: FaultHandler[];
}

export interface FieldNode {
  name?: string;
  type?: string;
  required?: boolean;
  repeated?: boolean;
  children?: FieldNode[];
  [key: string]: unknown;
}

export interface IOCardData {
  process_id: string;
  trigger: Record<string, unknown>;
  request_contract: FieldNode;
  response_contract: FieldNode;
  side_effects: Record<string, unknown>[];
  faults: string[];
  subprocesses: string[];
}

export interface Gate {
  gate: string;
  state: "pending" | "approved" | "rejected" | "needs_review" | string;
  enabled?: boolean;
  reason?: string | null;
  decided_by?: string | null;
  decided_at?: string | null;
  note?: string | null;
  reset_reason?: string | null;
  spec_hash?: string | null;
}

export interface Citation {
  activity_id: string;
  kind: string;
  text: string;
  verified?: boolean;
  reason?: string | null;
}

export interface Challenge {
  verdict: "agree" | "disagree" | string;
  reason: string;
  model?: string | null;
}

export type RuleStatus = "draft" | "approved" | "edited" | "rejected" | "unverified";

export interface RuleView {
  id: string;
  rule_key?: string;
  text: string;
  original_text?: string;
  status?: RuleStatus | string;
  confidence?: number | null;
  citations?: Citation[];
  owner?: string | null;
  memory_refs?: unknown[];
  challenge?: Challenge | null;
  updated_by?: string | null;
  updated_at?: string | null;
}

export type FindingDecision = "keep" | "fix" | "ask";

export interface FindingView {
  id: string;
  finding_key?: string;
  severity?: string;
  text: string;
  decision?: FindingDecision | string | null;
  citations?: Citation[];
  source?: "static" | "ai" | string;
  business_impact?: string | null;
  note?: string | null;
  decided_by?: string | null;
  decided_at?: string | null;
}

export interface VerificationFailure {
  rule_id: string;
  citation: Citation;
  reason: string;
}

export interface Verification {
  verified: number;
  total: number;
  passed?: boolean;
  failures: VerificationFailure[];
}

export interface CoverageItem {
  kind: string;
  ref: string;
  covered_by: string[];
  technical_reason: string | null;
}

export interface Coverage {
  covered: number;
  total: number;
  complete?: boolean;
  items: CoverageItem[];
}

export interface AnalysisView {
  process_id?: string;
  analysed?: boolean;
  analysis_id?: string | null;
  job_id?: string | null;
  created_at?: string | null;
  provider?: string | null;
  ai_available?: boolean;
  ai_reason?: string | null;
  notice?: string | null;
  static_only?: boolean;
  summary?: string | null;
  rules: RuleView[];
  findings: FindingView[];
  verification?: Verification;
  coverage?: Coverage;
  spec_hash?: string;
  gates?: Gate[];
  blockers?: string[];
}

export interface AuditEntry {
  id: number;
  ts: string;
  project_id: string | null;
  process_id: string | null;
  actor: string;
  actor_kind: string;
  action: string;
  gate: string | null;
  detail: Record<string, unknown>;
}

export interface AskCitation {
  id: string;
  kind: string;
  process_id: string | null;
  text: string;
  score?: number;
}

export interface AskAnswer {
  text: string;
  used_model: boolean;
  citations: AskCitation[];
}

// Design record (engine/dotnet_gen/models.py DesignRecord, served by GET /processes/{id}/design).
export interface DesignMappingRow {
  activity_id: string;
  activity_name: string;
  canonical_type: string;
  pattern: string;
  project: string;
  file: string;
  class_name: string;
  method: string;
  rule_ids: string[];
  finding_ids: string[];
  origin: "code" | "agent" | "ai" | string;
}

export interface DesignProject {
  name: string;
  kind: string;
  files: string[];
}

export interface DesignRecord {
  process_id?: string;
  spec_hash?: string;
  solution: { name?: string; projects: DesignProject[] };
  mapping: DesignMappingRow[];
  contracts: { contract_id: string; record_names?: string[]; namespace?: string; file?: string }[];
  config: { substvar: string; key: string; secret: boolean }[];
  config_ignored: { substvar: string; reason: string }[];
  messaging: { kind: string; name: string; direction: string }[];
  resilience: { resource_id: string; timeout_ms?: number | null; retry?: unknown; breaker?: unknown }[];
  sequence: { from_?: string; from?: string; to: string; label: string }[];
  decisions: { title: string; choice: string; alternatives: string[]; reason: string }[];
  open_questions: { question: string; recommendation: string }[];
  expected_differences: { finding_id: string; description: string }[];
  narrative: string | null;
  design_hash: string;
}

export interface ScenarioView {
  id?: string;
  key?: string;
  name: string;
  origin?: string;
  data?: Record<string, unknown>;
  [key: string]: unknown;
}

export const USERS = ["sme.vicky", "architect.lee", "engineer.ravi", "qa.priya", "ops.sam"] as const;
export type UserName = (typeof USERS)[number];
