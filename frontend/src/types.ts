export type Health = "healthy" | "warning" | "critical" | "unknown";

export interface Option {
  key: string;
  label: string;
}

export interface Product extends Option {
  stacks: string[];
  databases?: string[];
  license_model: string;
}

export interface Catalogue {
  products: Product[];
  stacks: Option[];
  databases: Option[];
  environments: Option[];
  license_models: (Option & { tiers: Record<string, string> })[];
}

export interface SapSystem {
  id: number;
  sid: string;
  name: string;
  product: string;
  product_label: string;
  product_version: string | null;
  basis_release: string | null;
  stack: string;
  database: string;
  environment: string;
  landscape: string;
  license_model: string | null;
  effective_license_model: string;
  host: string | null;
  default_client: string | null;
  description: string | null;
  timezone: string | null;
  enabled: boolean;
  /** null = default (allowed except for PRD). */
  control_enabled: boolean | null;
  created_at: string;
}

export interface Capability {
  supported: boolean;
  how_to_export: string;
  source?: string;
  license_recommendations?: boolean;
}

export interface SystemDetail extends SapSystem {
  capabilities: {
    certificates: Capability;
    growth: Capability;
    users: Capability;
    transports: Capability;
    jobs: Capability;
    locks: Capability;
    license_model: { key: string; label: string; tiers: Record<string, string>; unit: string; note: string };
  };
}

export type SystemInput = Omit<
  SapSystem,
  "id" | "product_label" | "effective_license_model" | "created_at"
>;

export interface Run {
  id: number;
  system_id: number;
  module: string;
  status: string;
  summary: Record<string, number | string | null>;
  created_at: string;
}

export interface OverviewSystem {
  availability?: { instances: number; running: number; degraded: number; down: number; system_up: boolean } | null;
  incidents?: IncidentCounts;
  id: number;
  sid: string;
  name: string;
  product: string;
  product_label: string;
  stack: string;
  database: string;
  environment: string;
  landscape: string;
  effective_license_model: string;
  health: Health;
  certificates: { count: number; health: Health; soonest_expiry_days: number | null };
  growth: { health: Health; disk_used_pct: number | null; days_until_threshold: number | null };
  users: Run | null;
  transports: { precheck: Run | null; postcheck: Run | null };
}

export interface Overview {
  totals: Partial<Record<Health, number>>;
  systems: OverviewSystem[];
}

export interface EndpointStatus {
  target_id: number;
  name: string;
  hostname: string;
  port: number;
  system_id: number | null;
  sid: string | null;
  landscape: string | null;
  status: string;
  days_remaining: number | null;
  not_after: string | null;
  checked_at: string | null;
  subject: string | null;
  error: string | null;
}

export interface ImportedCertificate {
  id: number;
  system_id: number;
  sid: string;
  landscape: string;
  pse_name: string;
  subject: string;
  issuer: string;
  not_after: string;
  days_remaining: number;
  status: string;
}

export interface CertificateSummary {
  counts: Record<string, number>;
  endpoints: EndpointStatus[];
  strust_certificates: ImportedCertificate[];
}

export interface GrowthTable {
  table_name: string;
  size_mb: number;
  row_count: number | null;
  snapshots: number;
  growth_mb_per_day: number | null;
  growth_mb_window: number;
  growth_pct_window: number | null;
  recommendation: string | null;
}

export interface GrowthReport {
  system_id: number;
  window_days: number;
  tracked_tables: number;
  tracked_growth_mb_per_day: number;
  size_history: { captured_at: string; tracked_size_mb: number }[];
  capacity_history: {
    captured_at: string;
    disk_used_mb: number;
    disk_total_mb: number;
    memory_used_mb: number | null;
    memory_total_mb: number | null;
  }[];
  capacity_forecast: null | {
    threshold_pct: number;
    disk_used_pct: number;
    disk_growth_mb_per_day: number | null;
    days_until_disk_threshold: number | null;
    days_until_disk_full: number | null;
    memory_used_pct?: number;
    days_until_memory_threshold?: number | null;
  };
  top_growing_tables: GrowthTable[];
  housekeeping_candidates: GrowthTable[];
}

export interface UserRecord {
  user: string;
  user_type: string;
  user_group: string;
  last_logon: string | null;
  days_inactive: number | null;
  current_license: string | null;
  reason: string;
}

export interface UserReport {
  summary: {
    users_analysed: number;
    active_dialog_users: number;
    lock_candidates: number;
    never_logged_on: number;
    license_downgrades: number;
    excluded: Record<string, number>;
    license_model: string;
    estimated_saving: number;
    saving_unit: string;
    saving_note: string;
  };
  lock_candidates: UserRecord[];
  license_changes: {
    user: string;
    current_license: string;
    recommended_license: string;
    transactions_used: string[];
    saving: number;
  }[];
  su10_user_list: string;
}

export interface TransportFinding {
  severity: "error" | "warning" | "info";
  code: string;
  request: string;
  message: string;
  object?: string;
}

export interface PrecheckResult {
  verdict: "pass" | "review" | "block";
  counts: Record<string, number>;
  queue: { request: string; objects: number; owner: string | null }[];
  findings: TransportFinding[];
  notes: string[];
}

export interface PostcheckResult {
  target_system: string;
  requests: number;
  failed: number;
  warnings: number;
  results: { request: string; rc: number; result: string }[];
  alerts: { channel: string; result: string }[];
  history?: { stored: number; duplicates: number };
}

export interface AlertEvent {
  id: string;
  system_id: number | null;
  sid: string | null;
  module: string;
  channel: string;
  severity: string;
  summary: string;
  sent_at: string;
  result: string;
  error: string | null;
}

export interface TransportRoute {
  id: number;
  source_system_id: number;
  target_system_id: number;
  source_sid: string;
  target_sid: string;
  source_environment: string;
  target_environment: string;
  route_type: "consolidation" | "delivery";
  transport_layer: string | null;
  description: string | null;
  mechanism: "TMS" | "CTS+";
  last_import?: null | { status: string; at: string; requests?: number; failed?: number; warnings?: number };
  warnings?: string[];
}

export interface TopologyAsset {
  id: number;
  landscape: string;
  system_id: number | null;
  kind: "host" | "hana" | "rfc_destination" | "middleware" | "web_dispatcher" | "external_service";
  name: string;
  hostname: string | null;
  port: number | null;
  attributes: Record<string, unknown>;
  source: "manual" | "sapcontrol" | "hana";
  created_at: string;
  updated_at: string;
}

export interface TopologyLink {
  id: number;
  source_asset_id: number;
  target_asset_id: number;
  relationship: "runs_on" | "uses_database" | "connects_to" | "routes_to" | "depends_on" | "hosts";
  source: "manual" | "sapcontrol" | "hana";
  verified_at: string | null;
  notes: string | null;
  created_at: string;
}

export interface DependencyTopology {
  landscape: string;
  assets: TopologyAsset[];
  links: TopologyLink[];
}

export interface TopologySystem {
  id: number;
  sid: string;
  name: string;
  environment: string;
  database: string;
  stack: string;
  product_version: string | null;
  enabled: boolean;
  health: Health;
  certificates?: { count: number; health: Health; soonest_expiry_days: number | null };
  growth?: { health: Health; disk_used_pct: number | null; days_until_threshold: number | null };
}

export interface Lane {
  key: string;
  product: string;
  product_label: string;
  stack: string;
  path: string;
  systems: TopologySystem[];
}

export interface Topology {
  landscape: string;
  tiers: { key: string; label: string }[];
  lanes: Lane[];
  routes: TransportRoute[];
  warnings: string[];
}

export interface RouteProposal {
  source_system_id: number;
  source_sid: string;
  target_system_id: number;
  target_sid: string;
  route_type: "consolidation" | "delivery";
  transport_layer: string | null;
  mechanism: string;
}

export interface SystemRoutes {
  inbound: TransportRoute[];
  outbound: TransportRoute[];
  hops: { system_id: number; sid: string }[][];
}

export interface SystemOverviewData {
  system: SystemDetail;
  health: Health;
  certificates: {
    count: number; health: Health; soonest_expiry_days: number | null; counts: Record<string, number>;
    endpoints: number; pse_certificates: number;
    expiring: { name?: string; hostname?: string; pse_name?: string; subject?: string; status: string; days_remaining: number | null }[];
  };
  growth: {
    health: Health; disk_used_pct: number | null; days_until_threshold: number | null; tracked_tables: number;
    tracked_growth_mb_per_day: number; memory_used_pct: number | null; snapshots: number;
    top_tables: { table_name: string; size_mb: number; growth_mb_per_day: number | null }[];
  };
  users: Run | null;
  transports: { precheck: Run | null; postcheck: Run | null };
  routes: SystemRoutes;
  transport_history: ImportHistory | null;
  jobs: { health: Health; cancelled_24h: number | null; long_running: number | null; report: JobReport | null };
  locks: { health: Health; long_running: number | null; oldest_minutes: number | null; report: LockReport | null };
  recent_alerts: AlertEvent[];
  recent_runs: Run[];
}


export interface TransportImport {
  id: number;
  trkorr: string;
  rc: number;
  result: "success" | "warning" | "error" | "fatal";
  imported_at: string;
  time_known: boolean;
  owner: string | null;
  description: string | null;
}

export interface ImportHistory {
  days: number;
  stats: { imports: number; requests: number; success: number; warning: number; error: number; fatal: number; failed: number };
  imports: TransportImport[];
}

export interface FailingJob {
  job_name: string;
  runs: number;
  cancelled: number;
  finished: number;
  failure_rate_pct: number | null;
  last_failure: string | null;
  last_run: string | null;
  last_status: string;
  still_failing: boolean;
  scheduled_by: string | null;
}

export interface JobReport {
  days: number;
  last_capture: string | null;
  stale: boolean;
  summary: {
    runs: number; cancelled: number; finished: number; active: number; scheduled: number;
    failure_rate_pct: number | null; cancelled_24h: number; failing_jobs: number; long_running: number;
    long_running_threshold_minutes: number;
  };
  failing_jobs: FailingJob[];
  recent_failures: { job_name: string; job_count: string | null; scheduled_by: string | null; start_at: string | null; duration_s: number | null }[];
  long_running: { job_name: string; job_count: string | null; scheduled_by: string | null; start_at: string; running_minutes: number }[];
}

export interface LockView {
  user: string;
  client: string | null;
  table: string;
  mode: string | null;
  argument: string | null;
  locked_at: string;
  age_minutes: number;
  severity: "critical" | "warning" | "ok";
  seen_in_snapshots: number;
}

export interface LockReport {
  latest: string | null;
  stale: boolean;
  summary: null | { locks: number; long_running: number; critical: number; oldest_minutes: number | null; users: number; tables: number; snapshots: number };
  long_running: LockView[];
  by_user: { user: string; locks: number }[];
  by_table: { table: string; locks: number }[];
  persistent: LockView[];
  history: { captured_at: string; total: number; long_running: number }[];
  thresholds: { warning_minutes: number; critical_minutes: number };
}


export type Role = "viewer" | "operator" | "admin";

export interface Me {
  id?: number;
  username: string;
  display_name: string | null;
  email?: string | null;
  role: Role;
  kind?: "user" | "apikey";
}

export interface User extends Me {
  id: number;
  active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface AuditEntry {
  id: number;
  at: string;
  username: string;
  action: string;
  target: string | null;
  status: number | null;
  detail: string | null;
  ip: string | null;
}

export interface IncidentCounts {
  open: number;
  acknowledged: number;
  critical: number;
  warning: number;
  total: number;
}

export interface Incident {
  id: number;
  system_id: number | null;
  sid: string | null;
  landscape: string | null;
  fingerprint: string;
  source: "rule" | "module";
  metric: string | null;
  title: string;
  severity: "warning" | "critical";
  status: "open" | "acknowledged" | "resolved";
  details: Record<string, unknown>;
  value: number | null;
  opened_at: string;
  last_seen_at: string;
  occurrences: number;
  assignee: string | null;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  resolution: string | null;
  in_maintenance: boolean;
  timeline?: { id: number; at: string; actor: string; kind: string; message: string | null }[];
}

export interface Rule {
  id: number;
  name: string;
  metric: string;
  operator: ">" | ">=" | "<" | "<=";
  warning: number | null;
  critical: number | null;
  scope_type: "global" | "landscape" | "system";
  scope_value: string | null;
  enabled: boolean;
  description: string | null;
  builtin: boolean;
  updated_at: string;
}

export interface RuleMetric {
  metric: string;
  name: string;
  operator: string;
  warning: number | null;
  critical: number | null;
  description: string;
}

export interface MaintenanceWindow {
  id: number;
  scope_type: "global" | "landscape" | "system";
  scope_value: string | null;
  starts_at: string;
  ends_at: string;
  reason: string;
  created_by: string;
  state: "active" | "planned" | "past";
}

export interface Collector {
  id: number;
  system_id: number;
  type: "sapcontrol" | "hana" | "hana_sapcontrol" | "node_exporter" | "os_ssh" | "rfc";
  host: string;
  port: number;
  use_tls: boolean;
  verify_tls: boolean;
  username: string | null;
  has_password: boolean;
  database_name: string | null;
  abap_schema: string | null;
  interval_seconds: number;
  enabled: boolean;
  last_run_at: string | null;
  last_status: "ok" | "partial" | "error" | null;
  last_error: string | null;
  last_duration_ms: number | null;
  diagnostics: { call: string; ok: boolean; error?: string; instance?: string; rows?: number; experimental?: boolean }[];
  simulated: boolean;
  has_private_key?: boolean;
  host_key?: string | null;
  sap_client?: string | null;
}

export interface AgentToken {
  id: number;
  system_id: number;
  hint: string;
  created_at: string;
  created_by: string | null;
  revoked_at: string | null;
  last_seen_at: string | null;
  last_ip: string | null;
  last_agent_version: string | null;
  last_payload_bytes: number | null;
  token?: string;
}

export interface AgentStatus {
  active: AgentToken | null;
  tokens: AgentToken[];
  endpoint: string;
  abap_objects: string[];
  schema: number;
}

export interface MonitorSnapshot<T = Record<string, any>> {
  source: string;
  captured_at: string;
  health: string;
  stale: boolean;
  data: T;
}

export interface MonitorsResponse {
  monitors: Record<string, MonitorSnapshot>;
  collectors: Collector[];
  incidents: IncidentCounts;
}

export interface MonitorDetail {
  monitor: string;
  latest: MonitorSnapshot | null;
  series: Record<string, { ts: string; value: number }[]>;
  uploadable: boolean;
}

// ------------------------------------------------------------------ discovery & lifecycle control
export type InstanceRole = "ASCS" | "SCS" | "ERS" | "PAS" | "AAS" | "JAVA" | "HANA" | "WEBDISP" | "SMDA" | "GATEWAY" | "OTHER";

export interface DiscoveredInstance {
  hostname: string;
  instance_nr: string;
  instance_name: string | null;
  role: InstanceRole;
  role_source: "instance_name" | "features" | "inferred" | "manual";
  features: string;
  http_port: number | null;
  https_port: number | null;
  start_priority: string | null;
  status: string;
  reachable?: boolean;
}

export interface SapInstance extends DiscoveredInstance {
  id: number;
  system_id: number;
  role_label: string;
  last_seen_at: string | null;
}

export interface ScanResult extends OsScanExtras {
  host: string;
  sid: string;
  instances: DiscoveredInstance[];
  stack: string;
  database: string | null;
  db_host: string | null;
  product: string;
  product_version: string | null;
  basis_release: string | null;
  product_evidence: string | null;
  components: { component: string; release: string; patchlevel: string }[];
  suggested_environment: string;
  control_access: boolean | null;
  collector: { host: string; port: number; use_tls: boolean; verify_tls: boolean; username: string | null };
  hana_instance: {
    host: string; port: number; instance_nr: string; sid: string; suggested_user: string | null; instances: DiscoveredInstance[];
  } | null;
  existing_systems: { id: number; sid: string; landscape: string; name: string }[];
  warnings: string[];
  scanned_at: string;
}

export interface OsInfo {
  hostname: string | null; fqdn: string | null; ip_addresses: string[];
  os_name: string | null; os_version: string | null; os_pretty: string | null; kernel: string | null; arch: string | null;
  cpu_count: number | null; cpu_model: string | null; memory_gb: number | null; memory_used_pct: number | null; swap_gb: number | null;
  load1: number | null; uptime_days: number | null; virtualization: string | null; timezone: string | null;
  filesystems: { mount: string; size_gb: number; used_pct: number }[];
  role?: string; ssh_host_key?: string | null; inventoried_at?: string;
}

export interface DatabaseLayer {
  type: string | null; host: string; on_application_host: boolean; tenant: string | null; schema: string | null;
  db_sid: string | null; instance_nr: string | null; version: string | null; status: string | null;
}

export interface OsScanExtras {
  method?: "os" | "sapcontrol";
  sids_on_host?: { sid: string; kind: "application" | "database" | "agent" | "webdispatcher"; instances: string[] }[];
  host_key?: string;
  os?: OsInfo;
  os_user?: string;
  database_layer?: DatabaseLayer;
  db_os?: OsInfo | null;
  db_host_key?: string | null;
  sap_credentials?: boolean;
}

export interface TopologyAssetView {
  id: number; kind: string; name: string; hostname: string | null; source: string; updated_at: string;
  attributes: Record<string, unknown>;
}

export interface Infrastructure {
  system: { id: number; sid: string; product_label: string | null; stack: string; database: string; host: string | null };
  application: TopologyAssetView | null;
  database: TopologyAssetView | null;
  hosts: (TopologyAssetView & { attributes: OsInfo })[];
  instances: SapInstance[];
  links: { id: number; source_asset_id: number; target_asset_id: number; relationship: string; verified_at: string | null }[];
  collectors: Collector[];
  refresh?: { collector_id: number; status: string; error: string | null }[];
}

export interface RegisterResult {
  system: SapSystem;
  created: boolean;
  instances: SapInstance[];
  collectors: Collector[];
  first_runs: { collector_id: number; status: string; error: string | null }[];
  warnings: string[];
}

export type StepStatus = "pending" | "running" | "ok" | "warning" | "failed" | "timeout" | "aborted" | "skipped"
  | "overridden" | "confirmed" | "not_confirmed" | "todo";

export interface ProcedureStep {
  key: string;
  kind: "check" | "manual" | "action";
  title: string;
  status: StepStatus;
  detail: string | null;
  blocking?: boolean;
  verb?: "start" | "stop";
  targets?: string[];
  roles?: string[];
  instances?: Record<string, string>;
  started_at?: string;
  finished_at?: string;
}

export type ControlScope = "system" | "app" | "db" | "instance";

export interface Operation {
  id: number;
  system_id: number;
  action: "start" | "stop" | "restart";
  scope: ControlScope;
  scope_label: string;
  hostname: string | null;
  instance_nr: string | null;
  soft_timeout: number | null;
  reason: string;
  requested_by: string;
  status: "running" | "succeeded" | "failed" | "timeout" | "interrupted" | "aborted";
  message: string | null;
  steps: ProcedureStep[];
  checklist: Record<string, boolean>;
  force: boolean;
  abort_requested: boolean;
  maintenance_window_id: number | null;
  started_at: string;
  finished_at: string | null;
}

export interface ControlPlan {
  action: string;
  scope: ControlScope;
  scope_label: string;
  steps: ProcedureStep[];
  blocked: boolean;
  blockers: string[];
  manual: { key: string; title: string }[];
}

export interface ControlStatus {
  allowed: boolean;
  reason: string | null;
  scopes: Record<ControlScope, boolean>;
  notes: string[];
  running: Operation | null;
  timeout_seconds: number;
  hana_start_timeout_seconds: number;
  start_sequence: string[];
  stop_sequence: string[];
}

export type CheckStatus = "critical" | "warning" | "stale" | "ok" | "no_data";
export interface DailyCheckCell {
  status: CheckStatus;
  value: number;
  at: string;
  threshold: { operator: string; warning: number | null; critical: number | null; source: "rule" | "default" };
}
export interface DailyCheckSystem {
  id: number; sid: string; name: string; landscape: string; environment: string;
  overall: CheckStatus; counts: Record<CheckStatus, number>; cells: Record<string, DailyCheckCell>;
}
export interface DailyChecks {
  generated_at: string;
  checks: { key: string; area: string; label: string; tcode: string; fresh_minutes: number; format: "flag" | "pct" | "ms" | "hours" | "minutes" | "days" | "count" }[];
  systems: DailyCheckSystem[];
  totals: Record<CheckStatus, number>;
  summary: { systems: number } & Record<CheckStatus, number>;
}
export interface DailyCheckReview {
  id: number; check_date: string; landscape: string | null; reviewed_by: string; reviewed_at: string;
  comment: string | null; summary: { systems: number } & Record<CheckStatus, number>;
}
