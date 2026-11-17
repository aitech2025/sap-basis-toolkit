import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { Card, DataTable, Empty, ErrorBanner, Pill, fmtDate, fmtNumber, useAsync } from "../../components/ui";
import type { MonitorsResponse, SystemDetail } from "../../types";
import { IncidentTable } from "../Incidents";

function ModuleCard({ to, title, status, children, cta }: {
  to: string; title: string; status: string; children: ReactNode; cta: string;
}) {
  return (
    <Link to={to} className="module-card">
      <div className="module-head"><h3>{title}</h3><Pill value={status} /></div>
      <div className="module-body">{children}</div>
      <span className="module-cta">{cta} →</span>
    </Link>
  );
}

function LiveRow({ system, base, live }: { system: SystemDetail; base: string; live: MonitorsResponse | null }) {
  if (!live) return null;
  const m = live.monitors;
  const has = Object.keys(m).length > 0;
  const age = (k: string) => (m[k] ? `${fmtDate(m[k].captured_at)}${m[k].stale ? " (stale)" : ""}` : null);
  const av = m.availability?.data.summary, wp = m.workprocesses?.data.summary;
  const dumps = m.dumps?.data.summary, sys = m.syslog?.data.summary, upd = m.updates?.data.summary;
  const trfc = m.trfc?.data.summary, qrfc = m.qrfc?.data.summary, idoc = m.idocs?.data.summary;
  const h = m.hana?.data;
  const worst = (...vals: (string | undefined)[]) => ["critical", "warning", "healthy"].find((x) => vals.includes(x)) ?? "unknown";
  const collectorErrors = live.collectors.filter((c) => c.last_status === "error");
  return (
    <>
      <h2 className="section-title">Live status {!live.collectors.length && <Link className="small" to={`${base}/setup`} style={{ fontWeight: 500 }}>· set up a live collector →</Link>}</h2>
      {collectorErrors.map((c) => <div key={c.id} className="banner error">Collector {c.type} ({c.host}) failed: {c.last_error}</div>)}
      {!has && live.collectors.length > 0 && <div className="banner info">Collectors are configured; the first results appear after their next run.</div>}
      <div className="module-grid live">
        <ModuleCard to={`${base}/availability`} title="Availability" status={m.availability ? (av?.system_up ? m.availability.health : "critical") : "unknown"} cta="Instances & work processes">
          {av ? (
            <>
              <p><strong>{av.system_up ? "Available" : "NOT available"}</strong> · {av.running}/{av.instances} instances running</p>
              {wp && <p>DIA {wp.by_type?.DIA?.busy_pct ?? "–"}% busy · BTC {wp.by_type?.BTC?.busy_pct ?? "–"}% · {wp.long_running} long-running</p>}
              <p className="muted small">{age("availability")}</p>
            </>
          ) : <p className="muted">From the SAP Start Service collector.</p>}
        </ModuleCard>
        {system.stack !== "JAVA" && (
          <ModuleCard to={`${base}/errors`} title="Errors" status={worst(m.dumps?.health, m.syslog?.health, m.updates?.health)} cta="Dumps, syslog, updates">
            {dumps || sys || upd ? (
              <>
                {dumps && <p><strong>{dumps.last_hour}</strong> dumps in the last hour · {dumps.last_24h} in 24 h</p>}
                {dumps?.top_errors?.[0] && <p className="small">Top: <code>{dumps.top_errors[0].error}</code> ×{dumps.top_errors[0].count}</p>}
                {sys && <p>{sys.errors_last_hour} syslog errors (1 h)</p>}
                {upd && <p>{upd.errors} update errors</p>}
              </>
            ) : <p className="muted">HANA collector with ABAP schema, sapcontrol syslog or uploads.</p>}
          </ModuleCard>
        )}
        {system.stack !== "JAVA" && (
          <ModuleCard to={`${base}/interfaces`} title="Interfaces" status={worst(m.trfc?.health, m.qrfc?.health, m.idocs?.health)} cta="tRFC, qRFC, IDocs">
            {trfc || qrfc || idoc ? (
              <>
                {trfc && <p><strong>{trfc.errors}</strong> tRFC errors</p>}
                {qrfc && <p><strong>{qrfc.blocked_queues}</strong> blocked qRFC queues ({qrfc.blocked_entries} entries)</p>}
                {idoc && <p><strong>{idoc.errors_24h}</strong> IDoc errors in 24 h{idoc.total_24h != null ? ` of ${idoc.total_24h}` : ""}</p>}
              </>
            ) : <p className="muted">HANA collector with ABAP schema, or uploads.</p>}
          </ModuleCard>
        )}
        {system.database === "HANA" && (
          <ModuleCard to={`${base}/hana`} title="SAP HANA" status={m.hana?.health ?? "unknown"} cta="Database health">
            {h ? (
              <>
                <p>Memory <strong>{h.memory?.pct ?? "–"}%</strong> · CPU {h.cpu_pct ?? "–"}% · data volume {h.disk?.pct ?? "–"}%</p>
                <p>Data backup {h.backups?.["complete data backup"]?.age_h ?? "–"} h ago · log backup {h.backups?.["log backup"]?.age_h != null ? `${Math.round(h.backups["log backup"].age_h * 60)} min` : "–"} ago</p>
                <p>Replication: {h.replication?.length ? h.replication.every((r: { status: string }) => r.status === "ACTIVE") ? "active" : "NOT active" : "not configured"} · {h.alerts?.filter((a: { rating: number }) => a.rating >= 4).length ?? 0} high alerts</p>
              </>
            ) : <p className="muted">From the HANA SQL collector.</p>}
          </ModuleCard>
        )}
      </div>
    </>
  );
}

const RUN_LABEL: Record<string, string> = {
  users: "User & licence analysis", jobs: "Background job upload", "transports-precheck": "Transport pre-check", "transports-postcheck": "Import post-check",
};

export default function OverviewTab({ system }: { system: SystemDetail }) {
  const { data, error } = useAsync(() => api.systemOverview(system.id), [system.id]);
  const live = useAsync(() => api.monitors(system.id), [system.id]);
  const active = useAsync(() => api.incidents(`?status=active&system_id=${system.id}`), [system.id]);
  const base = `/systems/${system.id}`;
  if (error) return <ErrorBanner error={error} />;
  if (!data) return <p className="muted">Loading {system.sid}…</p>;

  const caps = data.system.capabilities;
  const c = data.certificates, g = data.growth, u = data.users?.summary, pre = data.transports.precheck, post = data.transports.postcheck;
  // Worst of the last pre-check and the last import decides the transport badge.
  const RANK: Record<string, number> = { pass: 0, review: 1, block: 2, fail: 2 };
  const transportStatus = [pre?.status, post?.status].filter((x): x is string => !!x)
    .sort((a, b) => (RANK[b] ?? 0) - (RANK[a] ?? 0))[0] ?? "unknown";

  return (
    <>
      <div className={`banner ${data.health === "critical" ? "error" : data.health === "healthy" ? "ok" : "info"}`}>
        <strong>{system.sid}</strong> overall health: <Pill value={data.health} />
        <span className="muted"> · worst of live availability, open incidents, certificates, capacity forecast, jobs, locks and the last import</span>
      </div>

      <LiveRow system={system} base={base} live={live.data} />
      {!!active.data?.length && (
        <Card title={`Active incidents (${active.data.length})`} actions={<Link className="btn small" to="/incidents">All incidents</Link>}>
          <IncidentTable rows={active.data.slice(0, 8)} showSystem={false} />
        </Card>
      )}
      <h2 className="section-title">Operations</h2>
      <div className="module-grid">
        <ModuleCard to={`${base}/certificates`} title="Certificates" status={c.count ? c.health : "unknown"} cta="Manage certificates">
          {c.count ? (
            <>
              <p><strong>{c.endpoints}</strong> endpoint{c.endpoints === 1 ? "" : "s"} · <strong>{c.pse_certificates}</strong> {system.stack === "JAVA" ? "key storage" : "PSE"} certificate{c.pse_certificates === 1 ? "" : "s"}</p>
              <p>Soonest expiry: <strong>{c.soonest_expiry_days ?? "–"}</strong>{c.soonest_expiry_days != null && " days"}</p>
              {c.expiring.slice(0, 3).map((e, i) => (
                <p key={i} className="muted small"><Pill value={e.status} /> {e.name ?? e.pse_name} {e.days_remaining != null && `· ${e.days_remaining} d`}</p>
              ))}
            </>
          ) : <p className="muted">No endpoints or PSE certificates yet.</p>}
        </ModuleCard>

        <ModuleCard to={`${base}/growth`} title="Table growth & capacity" status={g.health} cta="Open growth report">
          {g.snapshots || g.disk_used_pct != null ? (
            <>
              <p>Disk used <strong>{g.disk_used_pct ?? "–"}%</strong>{g.days_until_threshold != null && <> · <strong>{g.days_until_threshold}</strong> days to threshold</>}</p>
              {g.memory_used_pct != null && <p>HANA memory used <strong>{g.memory_used_pct}%</strong></p>}
              <p>{g.tracked_tables} tables tracked · {fmtNumber(g.tracked_growth_mb_per_day)} MB/day</p>
              {g.top_tables.length > 0 && <p className="muted small">Fastest: {g.top_tables.map((t) => `${t.table_name} (${fmtNumber(t.growth_mb_per_day)} MB/d)`).join(", ")}</p>}
            </>
          ) : <p className="muted">No size snapshots yet. Upload a {system.database} table-size export.</p>}
        </ModuleCard>

        <ModuleCard to={`${base}/users`} title="Users & licences" status={data.users ? data.users.status : "unknown"} cta="Analyse users">
          {u ? (
            <>
              <p><strong>{u.lock_candidates}</strong> lock candidates · <strong>{u.license_downgrades}</strong> licence downgrades</p>
              <p>Estimated saving <strong>{u.estimated_saving}</strong> {u.saving_unit}</p>
              <p className="muted small">{u.users_analysed} users analysed · {fmtDate(data.users!.created_at)}</p>
            </>
          ) : <p className="muted">Not analysed yet. Licensing: {caps.license_model.label}.</p>}
        </ModuleCard>

        <ModuleCard to={`${base}/transports`} title="Transports" status={!caps.transports.supported ? "disabled" : transportStatus}
                    cta={caps.transports.supported ? "Run transport checks" : "Details"}>
          {!caps.transports.supported ? <p className="muted">Java system: transports run through CTS+ / NWDI.</p> : (
            <>
              {data.routes.hops.length ? (
                <div className="route-path compact">
                  {data.routes.hops.map((layer, i) => (
                    <Fragment key={i}>
                      {i > 0 && <span className="muted">→</span>}
                      <span className="hop-group">{layer.map((h) => (
                        <span key={h.system_id} className={`hop ${h.system_id === system.id ? "current" : ""}`}>{h.sid}</span>))}</span>
                    </Fragment>
                  ))}
                </div>
              ) : <p className="muted">No transport routes defined.</p>}
              {pre && <p>Last pre-check: <Pill value={pre.status} /> <span className="muted small">{fmtDate(pre.created_at)}</span></p>}
              {post && <p>Last import: <Pill value={post.status} /> {post.summary.failed} failed of {post.summary.requests}</p>}
            </>
          )}
        </ModuleCard>

        <ModuleCard to={`${base}/transports`} title="Transport history" cta="Full import history"
                    status={!data.transport_history ? "disabled" : data.transport_history.stats.failed ? "fail"
                      : data.transport_history.stats.warning ? "review" : data.transport_history.stats.imports ? "pass" : "unknown"}>
          {!data.transport_history ? <p className="muted">Not applicable for Java systems.</p>
            : data.transport_history.imports.length ? (
              <>
                <p>Last 30 days: <strong>{data.transport_history.stats.imports}</strong> imports · <strong>{data.transport_history.stats.failed}</strong> failed · {data.transport_history.stats.warning} with warnings</p>
                <ul className="mini-list">
                  {data.transport_history.imports.map((i) => (
                    <li key={i.id}><Pill value={i.result} /> <code>{i.trkorr}</code> <span className="muted small">RC {i.rc} · {fmtDate(i.imported_at)}</span></li>
                  ))}
                </ul>
              </>
            ) : <p className="muted">No imports recorded in the last 30 days. Upload the STMS import history.</p>}
        </ModuleCard>

        <ModuleCard to={`${base}/jobs`} title="Background jobs" cta="Job failures"
                    status={!caps.jobs.supported ? "disabled" : data.jobs.health}>
          {!caps.jobs.supported ? <p className="muted">Java Scheduler jobs are not covered.</p>
            : data.jobs.report?.last_capture ? (
              <>
                <p><strong>{data.jobs.report.summary.cancelled_24h}</strong> cancelled in 24 h · <strong>{data.jobs.report.summary.cancelled}</strong> in 7 days</p>
                <p>Failure rate {data.jobs.report.summary.failure_rate_pct ?? "–"}% · <strong>{data.jobs.report.summary.long_running}</strong> long-running</p>
                {data.jobs.report.failing_jobs.slice(0, 3).map((j) => (
                  <p key={j.job_name} className="small"><Pill value={j.still_failing ? "failed" : "recovered"} /> {j.job_name} <span className="muted">×{j.cancelled}</span></p>
                ))}
                {data.jobs.report.stale && <p className="muted small">Data from {fmtDate(data.jobs.report.last_capture)}</p>}
              </>
            ) : <p className="muted">No SM37 data yet.</p>}
        </ModuleCard>

        <ModuleCard to={`${base}/locks`} title="SAP locks" cta="Lock analysis"
                    status={!caps.locks.supported ? "disabled" : data.locks.health}>
          {!caps.locks.supported ? <p className="muted">Java locking adapter is not covered.</p>
            : data.locks.report?.summary ? (
              <>
                <p><strong>{data.locks.report.summary.locks}</strong> locks · <strong>{data.locks.report.summary.long_running}</strong> long-running · {data.locks.report.summary.critical} critical</p>
                <p>Oldest: <strong>{data.locks.report.summary.oldest_minutes ?? "–"}</strong> min</p>
                {data.locks.report.long_running.slice(0, 3).map((l, i) => (
                  <p key={i} className="small"><Pill value={l.severity} /> {l.table} · {l.user} <span className="muted">{l.age_minutes} min</span></p>
                ))}
                <p className="muted small">Snapshot {fmtDate(data.locks.report.latest)}{data.locks.report.stale ? " (stale)" : ""}</p>
              </>
            ) : <p className="muted">No SM12 snapshot yet.</p>}
        </ModuleCard>
      </div>

      <div className="cols">
        <Card title="Recent alerts" actions={<Link className="btn small" to={`${base}/history`}>All history</Link>}>
          {data.recent_alerts.length ? (
            <DataTable rows={data.recent_alerts} rowKey={(a) => a.id}
              columns={[
                { key: "sent_at", label: "When", render: (a) => fmtDate(a.sent_at) },
                { key: "severity", label: "Severity", render: (a) => <Pill value={a.severity} /> },
                { key: "summary", label: "Alert" },
              ]} />
          ) : <Empty>No alerts for {system.sid}.</Empty>}
        </Card>
        <Card title="Recent analyses">
          {data.recent_runs.length ? (
            <DataTable rows={data.recent_runs} rowKey={(r) => r.id}
              columns={[
                { key: "created_at", label: "When", render: (r) => fmtDate(r.created_at) },
                { key: "module", label: "Analysis", render: (r) => RUN_LABEL[r.module] ?? r.module },
                { key: "status", label: "Result", render: (r) => <Pill value={r.status} /> },
              ]} />
          ) : <Empty>No analyses run for {system.sid} yet.</Empty>}
        </Card>
      </div>
    </>
  );
}
