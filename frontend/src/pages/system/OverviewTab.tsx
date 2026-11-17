import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { Card, DataTable, Empty, ErrorBanner, Pill, fmtDate, fmtNumber, useAsync } from "../../components/ui";
import type { SystemDetail } from "../../types";

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

const RUN_LABEL: Record<string, string> = {
  users: "User & licence analysis", "transports-precheck": "Transport pre-check", "transports-postcheck": "Import post-check",
};

export default function OverviewTab({ system }: { system: SystemDetail }) {
  const { data, error } = useAsync(() => api.systemOverview(system.id), [system.id]);
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
        <span className="muted"> · combines certificates, capacity forecast and the last transport import</span>
      </div>

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
