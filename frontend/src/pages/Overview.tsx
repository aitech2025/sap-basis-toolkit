import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Empty, ErrorBanner, Pill, Tile, useAsync } from "../components/ui";
import type { OverviewSystem } from "../types";

const ENV_ORDER = ["DEV", "SBX", "QAS", "TRN", "PRE", "PRD", "OTHER"];

function SystemCard({ s }: { s: OverviewSystem }) {
  const users = s.users?.summary;
  const post = s.transports.postcheck;
  return (
    <Link to={`/systems/${s.id}`} className="system-card">
      <div className="top">
        <div>
          <div className="sid">{s.sid}</div>
          <div className="muted">{s.product_label}</div>
        </div>
        <Pill value={s.health} />
      </div>
      <dl>
        <dt>Stack / DB</dt><dd><span className="tag">{s.environment}</span>{s.stack} · {s.database}</dd>
        <dt>Availability</dt>
        <dd>{s.availability ? <><Pill value={s.availability.system_up ? (s.availability.down || s.availability.degraded ? "warning" : "healthy") : "critical"} />
          <span className="muted">{s.availability.system_up ? `${s.availability.running}/${s.availability.instances} up` : "DOWN"}</span></> : <span className="muted">no live data</span>}</dd>
        <dt>Incidents</dt>
        <dd>{s.incidents?.total ? <><Pill value={s.incidents.critical ? "critical" : "warning"} /><span className="muted">{s.incidents.total} active</span></> : <span className="muted">none</span>}</dd>
        <dt>Certificates</dt>
        <dd>{s.certificates.count ? <><Pill value={s.certificates.health} />
          {s.certificates.soonest_expiry_days !== null && <span className="muted">{s.certificates.soonest_expiry_days} d</span>}</> : <span className="muted">none</span>}</dd>
        <dt>Capacity</dt>
        <dd>{s.growth.disk_used_pct !== null ? <><Pill value={s.growth.health} />
          <span className="muted">{s.growth.disk_used_pct}% used{s.growth.days_until_threshold !== null ? ` · ${s.growth.days_until_threshold} d left` : ""}</span></> : <span className="muted">no data</span>}</dd>
        <dt>Users</dt>
        <dd>{users ? <span className="muted">{users.lock_candidates} to lock · {users.license_downgrades} downgrades</span> : <span className="muted">not analysed</span>}</dd>
        <dt>Last import</dt>
        <dd>{post ? <><Pill value={post.status} /><span className="muted">{post.summary.failed} failed</span></> : <span className="muted">–</span>}</dd>
      </dl>
    </Link>
  );
}

export default function OverviewPage() {
  const [landscape, setLandscape] = useState("");
  const landscapes = useAsync(() => api.landscapes(), []);
  const { data, error, loading } = useAsync(() => api.overview(landscape || undefined), [landscape]);

  const groups = new Map<string, OverviewSystem[]>();
  for (const s of data?.systems ?? []) groups.set(s.landscape, [...(groups.get(s.landscape) ?? []), s]);
  for (const list of groups.values()) list.sort((a, b) => ENV_ORDER.indexOf(a.environment) - ENV_ORDER.indexOf(b.environment) || a.sid.localeCompare(b.sid));

  return (
    <>
      <div className="page-head">
        <div><h1>Landscape overview</h1><p>Health of every enabled SAP system across all modules.</p></div>
        <div className="actions">
          <select value={landscape} onChange={(e) => setLandscape(e.target.value)} aria-label="Landscape">
            <option value="">All landscapes</option>
            {landscapes.data?.map((l) => <option key={l.landscape} value={l.landscape}>{l.landscape} ({l.systems})</option>)}
          </select>
          <Link to="/discover" className="btn primary">Discover system</Link>
          <Link to="/systems?new=1" className="btn">Add manually</Link>
        </div>
      </div>
      <ErrorBanner error={error} />
      {data && (
        <div className="tiles">
          <Tile label="Systems" value={data.systems.length} />
          <Tile label="Critical" value={data.totals.critical ?? 0} />
          <Tile label="Warning" value={data.totals.warning ?? 0} />
          <Tile label="Healthy" value={data.totals.healthy ?? 0} />
          <Tile label="No data yet" value={data.totals.unknown ?? 0} />
        </div>
      )}
      {!loading && data && !data.systems.length && (
        <Empty>No systems registered yet. <Link to="/discover">Discover your first SAP system</Link> from its host and &lt;sid&gt;adm login, or <Link to="/systems?new=1">add one manually</Link> — ECC, S/4HANA, BW, NetWeaver ABAP or Java.</Empty>
      )}
      {[...groups.entries()].map(([name, list]) => (
        <section className="landscape" key={name}>
          <h2>{name} <span className="muted">· {list.length} system{list.length === 1 ? "" : "s"} · </span>
            <Link to={`/landscapes/${encodeURIComponent(name)}`} style={{ fontSize: 13, fontWeight: 500 }}>transport path →</Link></h2>
          <div className="system-grid">{list.map((s) => <SystemCard key={s.id} s={s} />)}</div>
        </section>
      ))}
    </>
  );
}
