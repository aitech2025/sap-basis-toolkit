import type {
  AlertEvent, Catalogue, CertificateSummary, GrowthReport, Overview, PostcheckResult, PrecheckResult, Run,
  ImportHistory, JobReport, LockReport, RouteProposal, SapSystem, SystemDetail, SystemInput, SystemOverviewData, SystemRoutes, Topology, TransportRoute, UserReport,
} from "./types";

const KEY_STORAGE = "sapBasisToolkit.apiKey";

export function getApiKey(): string {
  try {
    return sessionStorage.getItem(KEY_STORAGE) ?? "";
  } catch {
    return "";
  }
}

export function setApiKey(value: string): void {
  try {
    if (value) sessionStorage.setItem(KEY_STORAGE, value);
    else sessionStorage.removeItem(KEY_STORAGE);
  } catch {
    /* storage unavailable: key lives for this page only */
  }
  window.dispatchEvent(new Event("apikey-changed"));
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
  headers.set("X-API-Key", getApiKey());
  const response = await fetch(path, { ...init, headers });
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
