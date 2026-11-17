export interface AnalyticsMetric {
  key: string;
  label: string;
  category: string;
  unit: string;
  description: string;
  format: "percent" | "number" | "hours" | "status";
}

export interface AnalyticsSummary {
  window_hours: number;
  systems_total: number;
  systems_reporting: number;
  samples: number;
  metrics_reporting: number;
  last_sample_at: string | null;
  metrics: string[];
}

export interface AnalyticsSeries {
  metric: AnalyticsMetric;
  window_hours: number;
  bucket_seconds: number;
  aggregation: "avg" | "min" | "max" | "last";
  truncated: boolean;
  series: {
    system_id: number;
    sid: string;
    name: string;
    landscape: string;
    environment: string;
    points: { ts: string; value: number; samples: number }[];
  }[];
}
