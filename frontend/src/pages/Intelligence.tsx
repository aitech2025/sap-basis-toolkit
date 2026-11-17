import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ErrorBanner } from "../components/ui";
import type { AnomaliesResponse, AvailabilityResponse, CapacityForecastResponse, IncidentCorrelationResponse } from "../types/intelligence";

const WINDOWS = [{ label: "24 hours", value: 24 }, { label: "7 days", value: 168 }, { label: "30 days", value: 720 }];
const fmt = (value: number | null | undefined, suffix = "") => value == null ? "—" : `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}${suffix}`;
const date = (value: string | null | undefined) => value ? new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "Not observed";

function ForecastValue({ label, data }: { label: string; data: CapacityForecastResponse["systems"][number]["forecasts"]["disk"] }) {
  const state = data.status === "threshold_projected" ? "warning" : data.status === "insufficient_data" ? "muted" : "normal";
  return <div className="intelligence-forecast-cell">
    <span>{label}</span>
    <strong className={state}>{data.status === "insufficient_data" ? "Insufficient history" : data.status === "stable_or_declining" ? "Stable / declining" : data.status === "threshold_projected" ? "Threshold approaching" : "Projected"}</strong>
    {data.projected_utilisation_pct != null && <small>{fmt(data.projected_utilisation_pct, "%")} in {data.horizon_days} days</small>}
    {data.threshold_in_days != null && <small>{data.threshold_in_days === 0 ? "Threshold already reached" : `85% threshold in ~${data.threshold_in_days} days`}</small>}
    {data.reason && <small>{data.reason}</small>}
    {data.daily_change_mb != null && <small>{fmt(data.daily_change_mb, " MB/day")} trend</small>}
  </div>;
}

export default function Intelligence() {
  const [hours, setHours] = useState(24);
  const [availability, setAvailability] = useState<AvailabilityResponse | null>(null);
  const [anomalies, setAnomalies] = useState<AnomaliesResponse | null>(null);
  const [capacity, setCapacity] = useState<CapacityForecastResponse | null>(null);
  const [correlations, setCorrelations] = useState<IncidentCorrelationResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    Promise.all([
      api.intelligenceAvailability(hours),
      api.intelligenceAnomalies(Math.min(hours, 24)),
      api.intelligenceCapacity(30, 85),
      api.intelligenceCorrelations(hours),
    ]).then(([a, n, c, i]) => {
      if (!active) return;
      setAvailability(a); setAnomalies(n); setCapacity(c); setCorrelations(i);
    }).catch((e) => active && setError(e instanceof Error ? e.message : "Unable to load operational intelligence"))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [hours, refreshKey]);

  const totalSamples = availability?.systems.reduce((n, s) => n + s.samples, 0) ?? 0;
  const anomaliesList = anomalies?.anomalies ?? [];
  const forecastCount = capacity?.systems.length ?? 0;
  const thresholdCount = capacity?.systems.reduce((n, s) => n + Number(s.forecasts.disk.status === "threshold_projected" || s.forecasts.memory.status === "threshold_projected"), 0) ?? 0;
  const correlationCount = correlations?.incidents.length ?? 0;

  return <div className="intelligence-page">
    <div className="dashboard-heading">
      <div><div className="eyebrow">OBSERVABILITY <span className="eyebrow-dot" /> INTELLIGENT OPERATIONS</div>
        <h1>Intelligent operations</h1>
        <p>Explainable anomaly signals, observed availability, capacity projections and incident context.</p>
      </div>
      <div className="dashboard-actions">
        <label className="filter-select"><span>ANALYSIS WINDOW</span><select value={hours} onChange={(e) => setHours(Number(e.target.value))}>{WINDOWS.map((w) => <option key={w.value} value={w.value}>{w.label}</option>)}</select></label>
        <button className="btn refresh-btn" disabled={loading} onClick={() => setRefreshKey((v) => v + 1)}>↻ <span>Refresh</span></button>
      </div>
    </div>
    <ErrorBanner error={error} />
    <div className="analytics-kpis">
      <div className="analytics-kpi"><span>Observed systems</span><strong>{availability?.systems.filter((s) => s.samples > 0).length ?? "—"}/{availability?.systems.length ?? "—"}</strong><small>{totalSamples.toLocaleString()} availability samples</small></div>
      <div className="analytics-kpi"><span>Anomaly signals</span><strong>{anomaliesList.length}</strong><small>Robust baseline deviations</small></div>
      <div className="analytics-kpi"><span>Capacity forecasts</span><strong>{forecastCount}</strong><small>{thresholdCount} systems approaching 85%</small></div>
      <div className="analytics-kpi"><span>Incident context</span><strong>{correlationCount}</strong><small>Incidents with nearby metric evidence</small></div>
    </div>

    <section className="panel intelligence-section">
      <div className="panel-heading"><div><span className="eyebrow">AVAILABILITY OBSERVATION</span><h2>System availability</h2></div><span className="panel-note">{WINDOWS.find((w) => w.value === hours)?.label}</span></div>
      <p className="intelligence-disclaimer">Observed sample ratio only—not wall-clock SLA. Missing collection intervals are excluded, not assumed available. Maintenance overlaps are shown separately and not subtracted.</p>
      {availability?.systems.length ? <div className="intelligence-table-wrap"><table className="intelligence-table"><thead><tr><th>System</th><th>Observed availability</th><th>Up / down samples</th><th>Maintenance overlaps</th><th>Last observation</th></tr></thead><tbody>
        {availability.systems.map((s) => <tr key={s.system_id}><td><Link to={`/systems/${s.system_id}`}><strong>{s.sid}</strong></Link><small>{s.name} · {s.landscape}</small></td><td>{s.observed_availability_pct == null ? <span className="muted">No data</span> : <strong>{fmt(s.observed_availability_pct, "%")}</strong>}</td><td>{s.up_samples} / {s.down_samples} <small>({s.samples} total)</small></td><td>{s.maintenance_windows_overlapping_period}</td><td>{date(s.last_observed_at)}</td></tr>)}
      </tbody></table></div> : <div className="analytics-empty"><strong>No availability observations</strong><p>Enable a collector and wait for actual system availability samples.</p></div>}
    </section>

    <section className="panel intelligence-section">
      <div className="panel-heading"><div><span className="eyebrow">STATISTICAL SIGNALS</span><h2>Anomaly detection</h2></div><span className="panel-note">7-day baseline · last {Math.min(hours, 24)}h recent window</span></div>
      <p className="intelligence-disclaimer">Median/MAD robust scoring. A signal requires at least 12 baseline observations and a robust score of 3.5 or more. These are investigation leads, not confirmed incidents.</p>
      {anomaliesList.length ? <div className="intelligence-anomaly-list">{anomaliesList.map((a) => <article className="intelligence-anomaly" key={`${a.system_id}-${a.metric}`}>
        <div><span className={`severity-pill ${a.severity}`}>{a.severity}</span><Link to={`/systems/${a.system_id}`}><strong>{a.sid}</strong></Link><span>{a.metric}</span></div>
        <div><strong>{fmt(a.latest_value)}</strong><small>Latest · baseline median {fmt(a.baseline_median)}</small></div>
        <div><strong>{a.robust_z_score > 0 ? "+" : ""}{a.robust_z_score} robust z</strong><small>{a.direction === "high" ? "Above" : "Below"} baseline · {a.recent_samples} recent samples</small></div>
        <small>{date(a.observed_at)}</small>
      </article>)}</div> : <div className="analytics-empty"><strong>{loading ? "Evaluating telemetry…" : "No anomaly signals detected"}</strong><p>Signals appear only when enough historical baseline observations exist and the latest recent value deviates materially.</p></div>}
    </section>

    <section className="panel intelligence-section">
      <div className="panel-heading"><div><span className="eyebrow">CAPACITY PLANNING</span><h2>30-day capacity outlook</h2></div><span className="panel-note">90-day history · 85% threshold</span></div>
      <p className="intelligence-disclaimer">Linear trend projection from recorded capacity snapshots. At least three snapshots spanning time are required; projections are directional estimates, not guaranteed exhaustion dates.</p>
      {capacity?.systems.length ? <div className="intelligence-forecast-list">{capacity.systems.map((s) => <article className="intelligence-forecast" key={s.system_id}>
        <div className="intelligence-forecast-system"><Link to={`/systems/${s.system_id}`}><strong>{s.sid}</strong></Link><span>{s.name}</span><small>{s.landscape} · {s.environment}</small></div>
        <ForecastValue label="Disk" data={s.forecasts.disk} />
        <ForecastValue label="Memory" data={s.forecasts.memory} />
      </article>)}</div> : <div className="analytics-empty"><strong>No capacity snapshots available</strong><p>Capacity forecasts will appear after the host capacity collector records historical disk and memory snapshots.</p></div>}
    </section>

    <section className="panel intelligence-section">
      <div className="panel-heading"><div><span className="eyebrow">INCIDENT CONTEXT</span><h2>Related telemetry around incidents</h2></div><span className="panel-note">±30 minutes around opening</span></div>
      <p className="intelligence-disclaimer">Shows metric changes temporally near incident creation. Temporal association is not proof of causation or automated root-cause determination.</p>
      {correlations?.incidents.length ? <div className="intelligence-correlation-list">{correlations.incidents.map((i) => <article className="intelligence-correlation" key={i.incident_id}>
        <div className="intelligence-correlation-head"><span className={`severity-pill ${i.severity}`}>{i.severity}</span><Link to={`/incidents/${i.incident_id}`}><strong>#{i.incident_id} · {i.title}</strong></Link><small>{i.sid ?? "Platform"} · {date(i.opened_at)}</small></div>
        {i.related_metrics.length ? <div className="intelligence-evidence">{i.related_metrics.map((m) => <div key={m.metric}><span>{m.metric}</span><strong>{m.change == null ? "Not enough paired samples" : `${m.change > 0 ? "+" : ""}${fmt(m.change)}`}</strong><small>{m.before_samples} before / {m.after_samples} after</small></div>)}</div> : <p className="muted">No metric observations found within the correlation window.</p>}
      </article>)}</div> : <div className="analytics-empty"><strong>No incidents in this analysis window</strong><p>Incident context will appear when incidents have been recorded alongside metric observations.</p></div>}
    </section>
    <div className="dashboard-footer"><span>Sentinel · Intelligent operations</span><span>All insights are computed from recorded telemetry; no missing values are fabricated.</span></div>
  </div>;
}
