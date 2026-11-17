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
  const { data, error, reload } = useAsync(
    () => api.incidents(`?status=${status}${severity ? `&severity=${severity}` : ""}`), [status, severity]);
  const counts = useAsync(() => api.incidentCounts(), [data]);
  return (
    <>
      <div className="page-head">
        <div><h1>Incidents</h1><p>Rule breaches from live collectors and alerts from every module, across all systems.</p></div>
        <div className="actions">
          <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="active">Active (open + acknowledged)</option><option value="open">Open</option>
            <option value="acknowledged">Acknowledged</option><option value="resolved">Resolved</option><option value="all">All</option>
          </select>
          <select value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity">
            <option value="">All severities</option><option value="critical">Critical</option><option value="warning">Warning</option>
          </select>
          <button className="btn" onClick={reload}>Refresh</button>
        </div>
      </div>
      <ErrorBanner error={error} />
      {counts.data && (
        <div className="tiles">
          <Tile label="Active" value={counts.data.total} />
          <Tile label="Critical" value={counts.data.critical} />
          <Tile label="Warning" value={counts.data.warning} />
          <Tile label="Unacknowledged" value={counts.data.open} />
          <Tile label="Acknowledged" value={counts.data.acknowledged} />
        </div>
      )}
      <Card><IncidentTable rows={data ?? []} /></Card>
    </>
  );
}
