import type { AnalyticsMetric, AnalyticsSeries, AnalyticsSummary } from "./types/analytics";
import type { AnomaliesResponse, AvailabilityResponse, CapacityForecastResponse, IncidentCorrelationResponse } from "./types/intelligence";
import type {
  AlertEvent, Catalogue, CertificateSummary, GrowthReport, Overview, PostcheckResult, PrecheckResult, Run,
  AuditEntry, Collector, ControlStatus, ImportHistory, Operation, RegisterResult, SapInstance, ScanResult, Incident, IncidentCounts, JobReport, LockReport, MaintenanceWindow, Me, MonitorDetail, MonitorsResponse, RouteProposal, Rule, RuleMetric, User, SapSystem, SystemDetail, SystemInput, SystemOverviewData, SystemRoutes, Topology, TransportRoute, UserReport,
} from "./types";

const KEY_STORAGE = "sapBasisToolkit.apiKey";
const TOKEN_STORAGE = "sapBasisToolkit.session";

function store(key: string, value: string | null) {
  try {
    if (value) sessionStorage.setItem(key, value);
    else sessionStorage.removeItem(key);
  } catch {
    /* storage unavailable: credentials live for this page only */
  }
}

function load(key: string): string {
  try {
    return sessionStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

export function getApiKey(): string {
  return load(KEY_STORAGE);
}

export function setApiKey(value: string): void {
  store(KEY_STORAGE, value || null);
  window.dispatchEvent(new Event("apikey-changed"));
}

export function getToken(): string {
  return load(TOKEN_STORAGE);
}

export function setToken(value: string | null): void {
  store(TOKEN_STORAGE, value);
  window.dispatchEvent(new Event("apikey-changed"));
}

export function signOut(): void {
  store(TOKEN_STORAGE, null);
  store(KEY_STORAGE, null);
  window.dispatchEvent(new Event("auth-required"));
}

export function hasCredentials(): boolean {
  return !!(getToken() || getApiKey());
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function detailMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? `${(d as { loc?: unknown[] }).loc?.slice(1).join(".")}: ${(d as { msg: string }).msg}` : String(d)))
      .join("; ");
  }
  return JSON.stringify(detail);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  else if (getApiKey()) headers.set("X-API-Key", getApiKey());
  const response = await fetch(path, { ...init, headers });
  if (response.status === 401 && !path.startsWith("/api/v1/auth/")) {
    window.dispatchEvent(new Event("auth-required"));
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  let data: unknown = text;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* plain text body */
  }
  if (!response.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? (data as { detail: unknown }).detail : text;
    throw new ApiError(response.status, detailMessage(detail) || `HTTP ${response.status}`);
  }
  return data as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  intelligenceAvailability: (hours = 24, landscape?: string) => {
    const params = new URLSearchParams({ hours: String(hours) });
    if (landscape) params.set("landscape", landscape);
    return request<AvailabilityResponse>(`/api/v1/intelligence/availability?${params.toString()}`);
  },
  intelligenceAnomalies: (hours = 24, landscape?: string) => {
    const params = new URLSearchParams({ hours: String(hours) });
    if (landscape) params.set("landscape", landscape);
    return request<AnomaliesResponse>(`/api/v1/intelligence/anomalies?${params.toString()}`);
  },
  intelligenceCapacity: (horizonDays = 30, thresholdPct = 85, landscape?: string) => {
    const params = new URLSearchParams({ horizon_days: String(horizonDays), threshold_pct: String(thresholdPct) });
    if (landscape) params.set("landscape", landscape);
    return request<CapacityForecastResponse>(`/api/v1/intelligence/capacity-forecasts?${params.toString()}`);
  },
  intelligenceCorrelations: (hours = 24, landscape?: string) => {
    const params = new URLSearchParams({ hours: String(hours) });
    if (landscape) params.set("landscape", landscape);
    return request<IncidentCorrelationResponse>(`/api/v1/intelligence/incident-correlations?${params.toString()}`);
  },
  analyticsMetrics: () => request<AnalyticsMetric[]>("/api/v1/analytics/metrics"),
  analyticsSummary: (hours = 24, landscape?: string) => {
    const params = new URLSearchParams({ hours: String(hours) });
    if (landscape) params.set("landscape", landscape);
    return request<AnalyticsSummary>(`/api/v1/analytics/summary?${params.toString()}`);
  },
  analyticsSeries: (metric: string, options: { hours?: number; bucketSeconds?: number; aggregation?: "avg" | "min" | "max" | "last"; systemId?: number; landscape?: string } = {}) => {
    const params = new URLSearchParams({ metric, hours: String(options.hours ?? 24), bucket_seconds: String(options.bucketSeconds ?? 300), aggregation: options.aggregation ?? "avg" });
    if (options.systemId) params.set("system_id", String(options.systemId));
    if (options.landscape) params.set("landscape", options.landscape);
    return request<AnalyticsSeries>(`/api/v1/analytics/series?${params.toString()}`);
  },
  authStatus: () => request<{ setup_required: boolean }>("/api/v1/auth/status"),
  login: (username: string, password: string) =>
    request<{ token: string; expires_at: string; user: Me }>("/api/v1/auth/login", json("POST", { username, password })),
  setup: (body: { api_key: string; username: string; password: string; display_name?: string }) =>
    request<Me>("/api/v1/auth/setup", json("POST", body)),
  me: () => request<Me>("/api/v1/me"),
  changePassword: (current_password: string, new_password: string) =>
    request<{ token: string }>("/api/v1/me/password", json("POST", { current_password, new_password })),
  users: () => request<User[]>("/api/v1/users"),
  createUser: (body: Record<string, unknown>) => request<User>("/api/v1/users", json("POST", body)),
  updateUser: (id: number, body: Record<string, unknown>) => request<User>(`/api/v1/users/${id}`, json("PATCH", body)),
  audit: (params = "") => request<AuditEntry[]>(`/api/v1/audit${params}`),

  incidents: (params = "") => request<Incident[]>(`/api/v1/incidents${params}`),
  incident: (id: number) => request<Incident>(`/api/v1/incidents/${id}`),
  incidentCounts: (systemId?: number) => request<IncidentCounts>(`/api/v1/incidents/counts${systemId ? `?system_id=${systemId}` : ""}`),
  incidentAction: (id: number, action: string, body: { message?: string; assignee?: string | null }) =>
    request<Incident>(`/api/v1/incidents/${id}/${action}`, json("POST", body)),
  rules: () => request<Rule[]>("/api/v1/rules"),
  ruleMetrics: () => request<RuleMetric[]>("/api/v1/rules/metrics"),
  createRule: (body: Record<string, unknown>) => request<Rule>("/api/v1/rules", json("POST", body)),
  updateRule: (id: number, body: Record<string, unknown>) => request<Rule>(`/api/v1/rules/${id}`, json("PATCH", body)),
  deleteRule: (id: number) => request<void>(`/api/v1/rules/${id}`, { method: "DELETE" }),
  maintenance: (includePast = false) => request<MaintenanceWindow[]>(`/api/v1/maintenance${includePast ? "?include_past=true" : ""}`),
  createMaintenance: (body: Record<string, unknown>) => request<MaintenanceWindow>("/api/v1/maintenance", json("POST", body)),
  endMaintenance: (id: number) => request(`/api/v1/maintenance/${id}/end`, { method: "POST" }),

  collectors: (systemId: number) => request<Collector[]>(`/api/v1/systems/${systemId}/collectors`),
  createCollector: (systemId: number, body: Record<string, unknown>) =>
    request<Collector>(`/api/v1/systems/${systemId}/collectors`, json("POST", body)),
  testCollector: (systemId: number, body: Record<string, unknown>) =>
    request<{ ok: boolean; error?: string; duration_ms: number; summary?: Record<string, unknown>; diagnostics?: { call: string; ok: boolean; error?: string; instance?: string }[] }>(
      `/api/v1/systems/${systemId}/collectors/test`, json("POST", body)),
  updateCollector: (id: number, body: Record<string, unknown>) => request<Collector>(`/api/v1/collectors/${id}`, json("PATCH", body)),
  deleteCollector: (id: number) => request<void>(`/api/v1/collectors/${id}`, { method: "DELETE" }),
  runCollector: (id: number) => request<{ status: string; error: string | null; duration_ms: number }>(`/api/v1/collectors/${id}/run`, { method: "POST" }),
  monitors: (systemId: number) => request<MonitorsResponse>(`/api/v1/systems/${systemId}/monitors`),
  monitor: (systemId: number, monitor: string, hours = 24) =>
    request<MonitorDetail>(`/api/v1/systems/${systemId}/monitors/${monitor}?hours=${hours}`),
  uploadMonitor: (systemId: number, monitor: string, csv: string) =>
    request<{ health: string; metrics: Record<string, number> }>(`/api/v1/systems/${systemId}/monitors/${monitor}/upload`, { method: "POST", body: csv }),

  discoveryScan: (body: Record<string, unknown>) => request<ScanResult>("/api/v1/discovery/scan", json("POST", body)),
  discoveryRegister: (body: Record<string, unknown>) => request<RegisterResult>("/api/v1/discovery/register", json("POST", body)),
  instances: (systemId: number) => request<SapInstance[]>(`/api/v1/systems/${systemId}/instances`),
  refreshInstances: (systemId: number) => request<SapInstance[]>(`/api/v1/systems/${systemId}/instances/refresh`, { method: "POST" }),
  setInstanceRole: (systemId: number, instanceId: number, role: string) =>
    request<SapInstance>(`/api/v1/systems/${systemId}/instances/${instanceId}`, json("PATCH", { role })),
  instanceRoles: () => request<{ role: string; label: string }[]>("/api/v1/meta/instance-roles"),
  controlStatus: (systemId: number) => request<ControlStatus>(`/api/v1/systems/${systemId}/control`),
  control: (systemId: number, body: Record<string, unknown>) => request<Operation>(`/api/v1/systems/${systemId}/control`, json("POST", body)),
  operations: (systemId: number) => request<Operation[]>(`/api/v1/systems/${systemId}/operations`),
  operation: (id: number) => request<Operation>(`/api/v1/operations/${id}`),
  health: () => fetch("/health").then((r) => r.ok),
  catalogue: () => request<Catalogue>("/api/v1/meta/catalogue"),
  overview: (landscape?: string) =>
    request<Overview>(`/api/v1/overview${landscape ? `?landscape=${encodeURIComponent(landscape)}` : ""}`),
  landscapes: () => request<{ landscape: string; systems: number }[]>("/api/v1/landscapes"),
  systems: () => request<SapSystem[]>("/api/v1/systems"),
  system: (id: number) => request<SystemDetail>(`/api/v1/systems/${id}`),
  createSystem: (body: Partial<SystemInput>) => request<SapSystem>("/api/v1/systems", json("POST", body)),
  updateSystem: (id: number, body: Partial<SystemInput>) => request<SapSystem>(`/api/v1/systems/${id}`, json("PATCH", body)),
  deleteSystem: (id: number) => request<void>(`/api/v1/systems/${id}`, { method: "DELETE" }),
  systemOverview: (id: number) => request<SystemOverviewData>(`/api/v1/systems/${id}/overview`),
  runs: (id: number) => request<Run[]>(`/api/v1/systems/${id}/runs`),

  certificates: (id?: number) =>
    request<CertificateSummary>(id ? `/api/v1/systems/${id}/certificates` : "/api/v1/certificates/summary"),
  addEndpoint: (body: { name: string; hostname: string; port: number; system_id: number }) =>
    request("/api/v1/targets", json("POST", body)),
  checkEndpoint: (targetId: number) => request(`/api/v1/targets/${targetId}/check`, { method: "POST" }),
  disableEndpoint: (targetId: number) => request(`/api/v1/targets/${targetId}/disable`, { method: "PATCH" }),
  importCertificate: (id: number, pse: string, body: ArrayBuffer) =>
    request<{ imported: number; duplicates_skipped: number }>(
      `/api/v1/systems/${id}/certificates/import?pse_name=${encodeURIComponent(pse)}`,
      { method: "POST", body },
    ),
  deleteCertificate: (id: number, certId: number) =>
    request<void>(`/api/v1/systems/${id}/certificates/${certId}`, { method: "DELETE" }),

  growth: (id: number, windowDays: number) =>
    request<GrowthReport>(`/api/v1/systems/${id}/growth/report?window_days=${windowDays}&top=25`),
  uploadSnapshot: (id: number, csv: string, capturedAt?: string) =>
    request<{ tables: number; total_size_mb: number }>(
      `/api/v1/systems/${id}/growth/snapshots${capturedAt ? `?captured_at=${encodeURIComponent(capturedAt)}` : ""}`,
      { method: "POST", body: csv },
    ),
  recordCapacity: (id: number, body: Record<string, number | string | undefined>) =>
    request(`/api/v1/systems/${id}/growth/capacity`, json("POST", body)),

  analyzeUsers: (id: number, body: { usr02_csv: string; usage_csv?: string | null; dormant_days?: number }) =>
    request<UserReport>(`/api/v1/systems/${id}/users/analyze`, json("POST", body)),
  lockListCsv: (id: number, body: { usr02_csv: string; usage_csv?: string | null; dormant_days?: number }) =>
    request<string>(`/api/v1/systems/${id}/users/analyze?format=csv`, json("POST", body)),

  precheck: (id: number, body: Record<string, unknown>) =>
    request<PrecheckResult>(`/api/v1/systems/${id}/transports/precheck`, json("POST", body)),
  postcheck: (id: number, log: string) =>
    request<PostcheckResult>(`/api/v1/systems/${id}/transports/postcheck`, json("POST", { log })),

  topology: (landscape: string) => request<Topology>(`/api/v1/landscapes/${encodeURIComponent(landscape)}/topology`),
  suggestRoutes: (landscape: string, apply = false) =>
    request<{ proposals: RouteProposal[]; created: TransportRoute[] }>(
      `/api/v1/landscapes/${encodeURIComponent(landscape)}/routes/suggest${apply ? "?apply=true" : ""}`, { method: "POST" }),
  createRoute: (body: { source_system_id: number; target_system_id: number; route_type: string; transport_layer?: string | null; description?: string | null }) =>
    request<TransportRoute>("/api/v1/routes", json("POST", body)),
  deleteRoute: (id: number) => request<void>(`/api/v1/routes/${id}`, { method: "DELETE" }),
  systemRoutes: (id: number) => request<SystemRoutes>(`/api/v1/systems/${id}/routes`),

  transportHistory: (id: number, days = 90, result?: string) =>
    request<ImportHistory>(`/api/v1/systems/${id}/transports/history?days=${days}${result ? `&result=${result}` : ""}`),
  uploadJobs: (id: number, csv: string) =>
    request<{ rows: number; inserted: number; updated: number; new_failures: number }>(`/api/v1/systems/${id}/jobs/snapshots`, { method: "POST", body: csv }),
  jobsReport: (id: number, days = 7) => request<JobReport>(`/api/v1/systems/${id}/jobs/report?days=${days}`),
  uploadLocks: (id: number, csv: string) =>
    request<{ locks: number; long_running: number; captured_at: string }>(`/api/v1/systems/${id}/locks/snapshots`, { method: "POST", body: csv }),
  locksReport: (id: number, historyDays = 7) => request<LockReport>(`/api/v1/systems/${id}/locks/report?history_days=${historyDays}`),

  alerts: (systemId?: number) => request<AlertEvent[]>(`/api/v1/alerts${systemId ? `?system_id=${systemId}` : ""}`),
};
