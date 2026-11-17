import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { LineChart } from "../components/LineChart";
import { ErrorBanner } from "../components/ui";
import type { SapSystem } from "../types";
import type { AnalyticsMetric, AnalyticsSeries, AnalyticsSummary } from "../types/analytics";
import type { IncidentCorrelationResponse } from "../types/intelligence";

const RANGES = [{ label: "Last 6 hours", hours: 6 }, { label: "Last 24 hours", hours: 24 }, { label: "Last 7 days", hours: 168 }, { label: "Last 30 days", hours: 720 }];
const BUCKETS = [{ label: "1 minute", seconds: 60 }, { label: "5 minutes", seconds: 300 }, { label: "15 minutes", seconds: 900 }, { label: "30 minutes", seconds: 1800 }, { label: "1 hour", seconds: 3600 }, { label: "6 hours", seconds: 21600 }, { label: "1 day", seconds: 86400 }];

function valueFor(metric: AnalyticsMetric, value: number): number {
  return metric.key === "availability.system_up" ? value * 100 : value;
}
function formatValue(metric: AnalyticsMetric, value: number): string {
  const shown = valueFor(metric, value);
  return `${shown.toLocaleString(undefined, { maximumFractionDigits: 1 })}${metric.unit === "%" ? "%" : ` ${metric.unit}`}`;
}
function ageLabel(value: string | null): string {
  if (!value) return "No telemetry received";
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} hr ago`;
  return `${Math.floor(hours / 24)} days ago`;
}

export default function Analytics() {
  const [metrics, setMetrics] = useState<AnalyticsMetric[]>([]);
  const [systems, setSystems] = useState<SapSystem[]>([]);
  const [metricKey, setMetricKey] = useState("hana.cpu_pct");
  const [hours, setHours] = useState(24);
  const [bucketSeconds, setBucketSeconds] = useState(300);
  const [aggregation, setAggregation] = useState<"avg" | "min" | "max" | "last">("avg");
  const [landscape, setLandscape] = useState("");
  const [systemId, setSystemId] = useState("");
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [result, setResult] = useState<AnalyticsSeries | null>(null);
  const [correlations, setCorrelations] = useState<IncidentCorrelationResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);\n  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let active = true;
    Promise.all([api.analyticsMetrics(), api.systems()]).then(([catalogue, registry]) => {
      if (!active) return;
      setMetrics(catalogue);
      setSystems(registry);
      if (!catalogue.some((m) => m.key === metricKey)) setMetricKey(catalogue[0]?.key ?? "");
    }).catch((e) => active && setError(e instanceof Error ? e.message : "Unable to load analytics catalogue"));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    Promise.all([
      api.analyticsSummary(hours, landscape || undefined),
      api.intelligenceCorrelations(hours, landscape || undefined),
      metricKey ? api.analyticsSeries(metricKey, {
        hours, bucketSeconds, aggregation,
        landscape: landscape || undefined,
        systemId: systemId ? Number(systemId) : undefined,
      }) : Promise.resolve(null),
    ]).then(([overview, incidents, series]) => {
      if (!active) return;
      setSummary(overview);
      setResult(series);
    }).catch((e) => active && setError(e instanceof Error ? e.message : "Unable to load historical telemetry"))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [metricKey, hours, bucketSeconds, aggregation, landscape, systemId, refreshKey]);

  const visibleSystems = useMemo(() => systems.filter((s) => !landscape || s.landscape === landscape), [systems, landscape]);
  const groupedMetrics = useMemo(() => {
    const groups = new Map<string, AnalyticsMetric[]>();
    metrics.forEach((m) => groups.set(m.category, [...(groups.get(m.category) ?? []), m]));
    return [...groups.entries()];
  }, [metrics]);
  const selectedMetric = metrics.find((m) => m.key === metricKey);
  const series = result?.series ?? [];
  const pointsCount = series.reduce((n, s) => n + s.points.reduce((sum, p) => sum + p.samples, 0), 0);

  return (
    <div className="analytics-page">
      <div className="dashboard-heading">
        <div>
          <div className="eyebrow">OBSERVABILITY <span className="eyebrow-dot" /> HISTORICAL TELEMETRY</div>
          <h1>Monitoring analytics</h1>
          <p>Explore SAP and HANA performance trends across your landscape.</p>
        </div>
        <div className="dashboard-actions">
          <label className="filter-select"><span>LANDSCAPE</span><select value={landscape} onChange={(e) => { setLandscape(e.target.value); setSystemId(""); }}><option value="">All landscapes</option>{[...new Set(systems.map((s) => s.landscape))].sort().map((name) => <option key={name} value={name}>{name}</option>)}</select></label>
          <label className="filter-select"><span>TIME RANGE</span><select value={hours} onChange={(e) => setHours(Number(e.target.value))}>{RANGES.map((r) => <option key={r.hours} value={r.hours}>{r.label}</option>)}</select></label>
          <button className="btn refresh-btn" onClick={() => setRefreshKey((v) => v + 1)} disabled={loading}>↻ <span>Refresh</span></button>
        </div>
      </div>
      <ErrorBanner error={error} />
      <div className="analytics-kpis">
        <div className="analytics-kpi"><span>Systems reporting</span><strong>{summary ? `${summary.systems_reporting}/${summary.systems_total}` : "—"}</strong><small>With telemetry in selected window</small></div>
        <div className="analytics-kpi"><span>Metric observations</span><strong>{summary ? summary.samples.toLocaleString() : "—"}</strong><small>Across all collected metrics</small></div>
        <div className="analytics-kpi"><span>Metrics reporting</span><strong>{summary?.metrics_reporting ?? "—"}</strong><small>Distinct metrics observed</small></div>
        <div className="analytics-kpi"><span>Last telemetry</span><strong className="analytics-last">{ageLabel(summary?.last_sample_at ?? null)}</strong><small>Latest recorded observation</small></div>
      </div>

      <section className="panel analytics-controls">
        <div className="panel-heading"><div><span className="eyebrow">TREND EXPLORER</span><h2>Metric performance</h2></div><span className="panel-note">{pointsCount.toLocaleString()} observations in chart</span></div>
        <div className="analytics-filter-grid">
          <label className="field"><span>METRIC</span><select value={metricKey} onChange={(e) => setMetricKey(e.target.value)}>{groupedMetrics.map(([category, items]) => <optgroup key={category} label={category}>{items.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}</optgroup>)}</select></label>
          <label className="field"><span>SYSTEM</span><select value={systemId} onChange={(e) => setSystemId(e.target.value)}><option value="">All systems</option>{visibleSystems.map((s) => <option key={s.id} value={s.id}>{s.sid} · {s.environment}</option>)}</select></label>
          <label className="field"><span>AGGREGATION</span><select value={aggregation} onChange={(e) => setAggregation(e.target.value as typeof aggregation)}><option value="avg">Average</option><option value="max">Maximum</option><option value="min">Minimum</option><option value="last">Latest in bucket</option></select></label>
          <label className="field"><span>BUCKET SIZE</span><select value={bucketSeconds} onChange={(e) => setBucketSeconds(Number(e.target.value))}>{BUCKETS.map((b) => <option key={b.seconds} value={b.seconds}>{b.label}</option>)}</select></label>
        </div>
        {selectedMetric && <p className="analytics-description">{selectedMetric.description}. Values are aggregated per time bucket; missing intervals are left blank rather than interpolated.</p>}
      </section>

      <section className="analytics-chart-section">
        <div className="analytics-section-head"><div><span className="eyebrow">TIME SERIES</span><h2>{selectedMetric?.label ?? "Metric trend"}</h2></div><span className="panel-note">{RANGES.find((r) => r.hours === hours)?.label} · {BUCKETS.find((b) => b.seconds === bucketSeconds)?.label} buckets</span></div>
        {loading && !result ? <div className="loading-panel">Loading historical telemetry…</div> : !series.length || !series.some((s) => s.points.length) ? (
          <div className="analytics-empty"><strong>No historical data for this selection</strong><p>Check that a collector is configured and has completed runs during this time range. Sentinel does not generate sample telemetry.</p><Link to="/systems">Review SAP system collectors →</Link></div>
        ) : (
          <div className="analytics-series-grid">
            {series.filter((s) => s.points.length).map((s) => {
              const chartPoints = s.points.map((p) => ({ t: new Date(p.ts).getTime(), v: valueFor(selectedMetric!, p.value) }));
              const values = s.points.map((p) => valueFor(selectedMetric!, p.value));
              const latest = values[values.length - 1];
              const average = values.reduce((a, b) => a + b, 0) / values.length;
              const peak = Math.max(...values);
              return <article className="analytics-series-card" key={s.system_id}>
                <div className="analytics-series-heading"><div><Link to={`/systems/${s.system_id}`}><strong>{s.sid}</strong></Link><span>{s.name} · {s.landscape}</span></div><span className="analytics-env">{s.environment}</span></div>
                <div className="analytics-series-stats"><span>Latest <strong>{formatValue(selectedMetric!, latest)}</strong></span><span>Average <strong>{formatValue(selectedMetric!, average)}</strong></span><span>Peak <strong>{formatValue(selectedMetric!, peak)}</strong></span></div>
                <LineChart points={chartPoints} unit={selectedMetric!.unit} label={`${s.sid} ${selectedMetric!.label}`} height={190} markers={(correlations?.incidents ?? []).filter((i) => i.system_id === s.system_id && i.metric === metricKey).map((i) => ({ t: new Date(i.opened_at).getTime(), label: `#${i.incident_id} ${i.title}`, severity: i.severity }))} />
                <div className="analytics-series-foot"><span>{s.points.length} time buckets</span><span>{s.points.reduce((n, p) => n + p.samples, 0)} raw samples</span></div>
              </article>;
            })}
          </div>
        )}
        {result?.truncated && <div className="banner info">The query reached the 100,000-observation safety limit. Narrow the time range or select a system for a complete view.</div>}
      </section>
      <div className="dashboard-footer"><span>Sentinel · TimescaleDB telemetry</span><span>Only recorded collector observations are shown. Aggregation does not fill gaps or infer missing values.</span></div>
    </div>
  );
}
