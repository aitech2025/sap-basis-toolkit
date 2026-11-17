export interface ObservedAvailability {
  system_id: number;
  sid: string;
  name: string;
  landscape: string;
  samples: number;
  up_samples: number;
  down_samples: number;
  observed_availability_pct: number | null;
  first_observed_at: string | null;
  last_observed_at: string | null;
  maintenance_windows_overlapping_period: number;
  measurement: string;
}
export interface AvailabilityResponse {
  window_hours: number;
  systems: ObservedAvailability[];
  truncated: boolean;
  method: string;
}
export interface Anomaly {
  system_id: number;
  sid: string;
  name: string;
  landscape: string;
  metric: string;
  baseline_median: number;
  latest_value: number;
  robust_z_score: number;
  direction: "high" | "low";
  baseline_samples: number;
  recent_samples: number;
  observed_at: string;
  severity: "warning" | "critical";
  method: string;
}
export interface AnomaliesResponse {
  baseline_days: number;
  recent_hours: number;
  anomalies: Anomaly[];
  truncated: boolean;
}
export interface CapacityForecast {
  label: string;
  status: "insufficient_data" | "stable_or_declining" | "projected" | "threshold_projected";
  points: number;
  reason?: string;
  current_used_mb?: number;
  daily_change_mb?: number;
  horizon_days?: number;
  projected_used_mb?: number;
  total_mb?: number | null;
  projected_utilisation_pct?: number | null;
  threshold_pct?: number;
  threshold_in_days?: number | null;
}
export interface CapacityForecastSystem {
  system_id: number;
  sid: string;
  name: string;
  landscape: string;
  environment: string;
  forecasts: { disk: CapacityForecast; memory: CapacityForecast };
}
export interface CapacityForecastResponse {
  lookback_days: number;
  horizon_days: number;
  threshold_pct: number;
  truncated: boolean;
  systems: CapacityForecastSystem[];
}
export interface IncidentCorrelation {
  incident_id: number;
  system_id: number | null;
  sid: string | null;
  landscape: string | null;
  title: string;
  severity: string;
  status: string;
  metric: string | null;
  opened_at: string;
  last_seen_at: string;
  source: string;
  evidence_window_minutes: number;
  related_metrics: { metric: string; before_samples: number; after_samples: number; before_avg: number | null; after_avg: number | null; change: number | null }[];
  interpretation: string;
}
export interface IncidentCorrelationResponse {
  window_hours: number;
  incidents: IncidentCorrelation[];
}

export interface RootCauseSignal {
  metric: string; label: string; domain: string; latest_value: number; threshold: number;
  direction: "high" | "low"; triggered: boolean; before_avg: number | null; after_avg: number | null;
  change: number | null; before_samples: number; after_samples: number; observed_at: string;
  primary_incident_metric: boolean; corroboration_points: number;
}
export interface RootCauseHypothesis {
  domain: string; title: string; score: number; confidence: "low" | "medium" | "high";
  supporting_signals: { metric: string; label: string; latest_value: number; threshold: number;
    change: number | null; observed_at: string }[];
  recommended_checks: string[]; reason: string;
}
export interface RootCauseResponse {
  incident: { id: number; title: string; severity?: string; status?: string; sid: string | null;
    system_name?: string | null; landscape?: string | null; metric?: string | null;
    value?: number | null; opened_at: string };
  analysis_window: { starts_at: string; ends_at: string; minutes_each_side: number };
  hypotheses: RootCauseHypothesis[];
  evidence: RootCauseSignal[];
  snapshot_evidence?: { monitor: string; source: string; health: string; captured_at: string;
    summary: Record<string, string | number | boolean | null> }[];
  coverage: { system_metrics: boolean; metrics_observed: number; expected_domains?: string[];
    domains_with_supporting_signals?: string[]; monitor_snapshots_in_window?: number;
    monitors_observed?: string[]; metric_query_truncated?: boolean };
  limitations: string[];
  interpretation: string;
}
