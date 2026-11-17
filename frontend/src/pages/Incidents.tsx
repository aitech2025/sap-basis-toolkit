import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Card, DataTable, ErrorBanner, Pill, Tile, fmtDate, useAsync } from "../components/ui";
import type { Incident } from "../types";

export const ago = (iso: string | null | undefined) => {
  if (!iso) return "–";
  const mins = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)} h ago`;
  return `${Math.round(mins / 1440)} d ago`;
};

export function IncidentTable({ rows, showSystem = true }: { rows: Incident[]; showSystem?: boolean }) {
  const navigate = useNavigate();
  return (
    <DataTable<Incident> rows={rows} rowKey={(i) => i.id} onRowClick={(i) => navigate(`/incidents/${i.id}`)}
      empty="No incidents match. 🎉"
      columns={[
        { key: "severity", label: "Severity", render: (i) => <Pill value={i.severity} /> },
        { key: "status", label: "Status", render: (i) => <><Pill value={i.status === "acknowledged" ? "acknowledged" : i.status === "resolved" ? "resolved" : "open"} />{i.in_maintenance && <span className="tag" title="Opened during a maintenance window">maint.</span>}</> },
        ...(showSystem ? [{ key: "sid", label: "System", render: (i: Incident) => i.system_id ? <Link to={`/systems/${i.system_id}`}>{i.sid}</Link> : "–" }] : []),
        { key: "title", label: "Incident", render: (i) => <Link to={`/incidents/${i.id}`}>{i.title}</Link> },
        { key: "source", label: "Source", render: (i) => i.source === "rule" ? (i.metric ?? "rule") : "module alert" },
        { key: "opened_at", label: "Opened", render: (i) => <span title={fmtDate(i.opened_at)}>{ago(i.opened_at)}</span> },
        { key: "last_seen_at", label: "Last seen", render: (i) => <span title={fmtDate(i.last_seen_at)}>{ago(i.last_seen_at)}</span> },
        { key: "occurrences", label: "×", numeric: true },
        { key: "assignee", label: "Owner", render: (i) => i.assignee ?? <span className="muted">–</span> },
      ]} />
  );
}

export default function Incidents() {
  const [status, setStatus] = useState("active");
  const [severity, setSeverity] = useState("");
  const [query, setQuery] = useState("");
  const { data, error, reload, loading } = useAsync(
    () => api.incidents(`?status=${status}${severity ? `&severity=${severity}` : ""}`), [status, severity]);
  const counts = useAsync(() => api.incidentCounts(), [data]);
  const rows = (data ?? []).filter((incident) => {
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    return [incident.title, incident.sid, incident.landscape, incident.module, incident.metric, incident.assignee]
      .some((value) => String(value ?? "").toLowerCase().includes(needle));
  }).sort((a, b) => {
    const rank: Record<string, number> = { critical: 0, warning: 1, info: 2 };
    return (rank[a.severity] ?? 3) - (rank[b.severity] ?? 3) || Date.parse(b.last_seen_at) - Date.parse(a.last_seen_at);
  });

  return (
    <div className="incident-workspace">
      <div className="incident-heading">
        <div>
          <span className="eyebrow">OPERATIONS · EVENT RESPONSE</span>
          <h1>Incident command</h1>
          <p>Prioritize service-impacting events, coordinate response and track resolution across your SAP estate.</p>
        </div>
        <div className="incident-heading-actions">
          <span className="incident-refresh-state"><i className="live-dot" /> Live incident queue</span>
          <button className="btn" onClick={reload} disabled={loading}>↻ Refresh</button>
        </div>
      </div>
      <ErrorBanner error={error} />
      {counts.data && (
        <div className="incident-summary-grid">
          <div className="incident-summary primary"><span>Active incidents</span><strong>{counts.data.total}</strong><small>Open + acknowledged</small></div>
          <div className="incident-summary critical"><span>Critical</span><strong>{counts.data.critical}</strong><small>Immediate response</small></div>
          <div className="incident-summary warning"><span>Warning</span><strong>{counts.data.warning}</strong><small>Investigate and monitor</small></div>
          <div className="incident-summary"><span>Awaiting acknowledgement</span><strong>{counts.data.open}</strong><small>Unassigned response</small></div>
          <div className="incident-summary"><span>Acknowledged</span><strong>{counts.data.acknowledged}</strong><small>Owned by operators</small></div>
        </div>
      )}
      <section className="incident-queue-panel">
        <div className="incident-queue-heading">
          <div><span className="eyebrow">RESPONSE QUEUE</span><h2>Prioritized incidents <span>{rows.length}</span></h2></div>
          <div className="incident-queue-controls">
            <label className="incident-search"><span>⌕</span><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search SID, incident, owner…" aria-label="Search incidents" /></label>
            <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
              <option value="active">Active incidents</option><option value="open">Open</option>
              <option value="acknowledged">Acknowledged</option><option value="resolved">Resolved</option><option value="all">All incidents</option>
            </select>
            <select value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity">
              <option value="">All severities</option><option value="critical">Critical</option><option value="warning">Warning</option>
            </select>
          </div>
        </div>
        <div className="incident-queue-note"><span><i className="priority-dot critical" /> Critical first</span><span>Sorted by severity, then most recently observed</span></div>
        <IncidentTable rows={rows} />
      </section>
    </div>
  );
}
