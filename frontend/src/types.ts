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
  enabled: boolean;
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
