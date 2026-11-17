import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Empty, ErrorBanner, Pill, useAsync } from "../components/ui";
import type { OverviewSystem } from "../types";

const ENV_ORDER = ["DEV", "SBX", "QAS", "TRN", "PRE", "PRD", "OTHER"];
const pct = (value: number | null | undefined) => value == null ? 0 : Math.max(0, Math.min(100, value));

function MetricCard({ label, value, detail, tone, icon }: { label: string; value: number | string; detail: string; tone: string; icon: string }) {
  return <div className={`metric-card metric-${tone}`}><div className="metric-top"><span>{label}</span><span className="metric-icon">{icon}</span></div><div className="metric-value">{value}</div><div className="metric-detail">{detail}</div></div>;
}

function HealthBar({ healthy, warning, critical, unknown }: { healthy: number; warning: number; critical: number; unknown: number }) {
  const total = healthy + warning + critical + unknown || 1;
  return <div className="health-bar" aria-label={`${healthy} healthy, ${warning} warning, ${critical} critical, ${unknown} unknown`}>
    {healthy > 0 && <span className="health-segment good" style={{ width: `${healthy / total * 100}%` }} />}
    {warning > 0 && <span className="health-segment warn" style={{ width: `${warning / total * 100}%` }} />}
    {critical > 0 && <span className="health-segment bad" style={{ width: `${critical / total * 100}%` }} />}
    {unknown > 0 && <span className="health-segment unknown" style={{ width: `${unknown / total * 100}%` }} />}
  </div>;
}

function SystemCard({ s }: { s: OverviewSystem }) {
  const running = s.availability ? `${s.availability.running}/${s.availability.instances} instances running` : "Availability data not collected";
  const incidentCount = s.incidents?.total ?? 0;
  const disk = s.growth.disk_used_pct;
  return <Link to={`/systems/${s.id}`} className="landscape-system-row">
    <div className="system-identity"><span className={`system-status-dot ${s.health === "healthy" ? "is-good" : s.health === "critical" ? "is-bad" : s.health === "warning" ? "is-warn" : ""}`} /><span><strong>{s.sid}</strong><small>{s.product_label} · {s.environment}</small></span></div>
    <div className="system-runtime"><Pill value={s.health} /><small>{running}</small></div>
    <div className="system-signal"><span className="signal-label">Incidents</span><strong className={incidentCount ? "text-bad" : ""}>{incidentCount || "—"}</strong></div>
    <div className="system-signal"><span className="signal-label">Disk used</span><strong>{disk == null ? "—" : `${disk}%`}</strong>{disk != null && <span className="mini-track"><i style={{ width: `${pct(disk)}%` }} /></span>}</div>
    <div className="system-row-arrow">↗</div>
  </Link>;
}

function SystemHealth({ systems }: { systems: OverviewSystem[] }) {
  const counts = systems.reduce((a, s) => { a[s.health] = (a[s.health] || 0) + 1; return a; }, {} as Record<string, number>);
  return <section className="panel health-panel">
    <div className="panel-heading"><div><span className="eyebrow">LANDSCAPE HEALTH</span><h2>System health distribution</h2></div><span className="panel-note">{systems.length} monitored systems</span></div>
    <HealthBar healthy={counts.healthy || 0} warning={counts.warning || 0} critical={counts.critical || 0} unknown={counts.unknown || 0} />
    <div className="health-legend">
      <span><i className="legend-dot good" /><strong>{counts.healthy || 0}</strong> Healthy</span>
      <span><i className="legend-dot warn" /><strong>{counts.warning || 0}</strong> Warning</span>
      <span><i className="legend-dot bad" /><strong>{counts.critical || 0}</strong> Critical</span>
      <span><i className="legend-dot unknown" /><strong>{counts.unknown || 0}</strong> No data</span>
    </div>
  </section>;
}

export default function OverviewPage() {
  const [landscape, setLandscape] = useState("");
  const landscapes = useAsync(() => api.landscapes(), []);
  const { data, error, loading, reload } = useAsync(() => api.overview(landscape || undefined), [landscape]);
  const systems = data?.systems ?? [];
  const groups = useMemo(() => {
    const map = new Map<string, OverviewSystem[]>();
    for (const s of systems) map.set(s.landscape, [...(map.get(s.landscape) ?? []), s]);
    for (const list of map.values()) list.sort((a, b) => ENV_ORDER.indexOf(a.environment) - ENV_ORDER.indexOf(b.environment) || a.sid.localeCompare(b.sid));
    return [...map.entries()];
  }, [systems]);
  const activeIncidents = systems.reduce((n, s) => n + (s.incidents?.total ?? 0), 0);
  const criticalIncidents = systems.reduce((n, s) => n + (s.incidents?.critical ?? 0), 0);
  const production = systems.filter((s) => s.environment === "PRD");
  const prodHealthy = production.filter((s) => s.health === "healthy").length;

  return <>
    <div className="dashboard-heading">
      <div><div className="eyebrow">SAP OPERATIONS CENTER <span className="eyebrow-dot" /> REAL-TIME VIEW</div><h1>Landscape overview</h1><p>One view of availability, risk and operational health across your SAP estate.</p></div>
      <div className="dashboard-actions">
        <label className="filter-select"><span>LANDSCAPE</span><select value={landscape} onChange={(e) => setLandscape(e.target.value)}><option value="">All landscapes</option>{landscapes.data?.map((l) => <option key={l.landscape} value={l.landscape}>{l.landscape} ({l.systems})</option>)}</select></label>
        <button className="btn refresh-btn" onClick={reload} disabled={loading}>↻ <span>Refresh</span></button>
        <Link to="/discover" className="btn primary">＋ Add SAP system</Link>
      </div>
    </div>
    <ErrorBanner error={error} />
    {data && <>
      <div className="metric-grid">
        <MetricCard label="Total systems" value={systems.length} detail={`${production.length} production systems`} tone="blue" icon="▦" />
        <MetricCard label="Healthy systems" value={data.totals.healthy ?? 0} detail={`${prodHealthy} production healthy`} tone="green" icon="✓" />
        <MetricCard label="Active incidents" value={activeIncidents} detail={`${criticalIncidents} critical priority`} tone="red" icon="⚑" />
        <MetricCard label="Needs attention" value={(data.totals.warning ?? 0) + (data.totals.critical ?? 0)} detail={`${data.totals.unknown ?? 0} systems without data`} tone="amber" icon="!" />
      </div>
      <div className="dashboard-grid">
        <SystemHealth systems={systems} />
        <section className="panel production-panel">
          <div className="panel-heading"><div><span className="eyebrow">PRODUCTION LANDSCAPE</span><h2>Production readiness</h2></div><span className="prod-indicator"><i /> LIVE</span></div>
          <div className="prod-score"><strong>{production.length ? Math.round(prodHealthy / production.length * 100) : 0}<small>%</small></strong><span>production systems healthy</span></div>
          <div className="prod-track"><i style={{ width: `${production.length ? prodHealthy / production.length * 100 : 0}%` }} /></div>
          <div className="prod-meta"><span>{prodHealthy} healthy</span><span>{production.length - prodHealthy} require review</span></div>
          <Link to="/systems" className="text-link">View production systems <span>→</span></Link>
        </section>
      </div>
      {!systems.length && !loading ? <Empty>No SAP systems are registered yet. <Link to="/discover">Discover your first system</Link> to start monitoring.</Empty> : <div className="landscape-list">
        <div className="section-toolbar"><div><span className="eyebrow">YOUR SAP ESTATE</span><h2>Systems by landscape</h2></div><Link to="/systems" className="text-link">All systems <span>→</span></Link></div>
        {groups.map(([name, list]) => <section className="landscape-panel" key={name}>
          <div className="landscape-panel-head"><div><span className="landscape-glyph">⌘</span><strong>{name}</strong><small>{list.length} system{list.length === 1 ? "" : "s"}</small></div><Link to={`/landscapes/${encodeURIComponent(name)}`} className="text-link">Transport topology →</Link></div>
          <div className="system-table-head"><span>SYSTEM</span><span>HEALTH & AVAILABILITY</span><span>INCIDENTS</span><span>DISK CAPACITY</span><span /></div>
          {list.map((s) => <SystemCard key={s.id} s={s} />)}
        </section>)}
      </div>}
    </>}
    {loading && !data && <div className="loading-panel"><span className="loading-spinner" /> Loading landscape telemetry…</div>}
    <div className="dashboard-footer"><span>Sentinel · SAP Basis Toolkit</span><span>Health is based on the latest collected telemetry. Missing data is shown as unknown.</span></div>
  </>;
}
