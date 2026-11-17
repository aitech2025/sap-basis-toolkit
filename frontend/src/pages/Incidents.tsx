import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { RefreshCw, Search } from "lucide-react";
import { Card, DataTable, ErrorBanner, PageHeader, Pill, Tile, fmtDate, useAsync } from "../components/ui";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { cn } from "../lib/utils";
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
      empty="No incidents match these filters."
      columns={[
        { key: "severity", label: "Severity", render: (i) => <Pill value={i.severity} /> },
        { key: "status", label: "Status", render: (i) => <><Pill value={i.status === "acknowledged" ? "acknowledged" : i.status === "resolved" ? "resolved" : "open"} />{i.in_maintenance && <span className="tag" title="Opened during a maintenance window">maint.</span>}</> },
        ...(showSystem ? [{ key: "sid", label: "System", render: (i: Incident) => i.system_id ? <Link to={`/systems/${i.system_id}`} className="font-mono font-medium">{i.sid}</Link> : "–" }] : []),
        { key: "title", label: "Incident", render: (i) => <Link to={`/incidents/${i.id}`} className="font-medium text-foreground">{i.title}</Link> },
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
    return [incident.title, incident.sid, incident.landscape, incident.metric, incident.assignee]
      .some((value) => String(value ?? "").toLowerCase().includes(needle));
  }).sort((a, b) => {
    const rank: Record<string, number> = { critical: 0, warning: 1, info: 2 };
    return (rank[a.severity] ?? 3) - (rank[b.severity] ?? 3) || Date.parse(b.last_seen_at) - Date.parse(a.last_seen_at);
  });

  const c = counts.data;
  return (
    <>
      <PageHeader title="Incidents" description="Service-impacting events across the SAP estate, critical first, then most recently seen."
        actions={<Button variant="outline" onClick={reload} disabled={loading}><RefreshCw className={cn(loading && "animate-spin")} />Refresh</Button>} />
      <ErrorBanner error={error} />
      {c && (
        <div className="tiles !grid-cols-2 md:!grid-cols-5">
          <Tile label="Active" value={c.total} tone={c.total ? "warn" : "good"} hint="Open and acknowledged" />
          <Tile label="Critical" value={c.critical} tone={c.critical ? "bad" : undefined} hint="Respond immediately" />
          <Tile label="Warning" value={c.warning} tone={c.warning ? "warn" : undefined} hint="Investigate" />
          <Tile label="Not acknowledged" value={c.open} hint="Nobody owns these yet" />
          <Tile label="Acknowledged" value={c.acknowledged} hint="Being worked on" />
        </div>
      )}
      <Card>
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <div className="relative w-full sm:w-80">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input className="pl-9" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search SID, incident or owner" aria-label="Search incidents" />
          </div>
          <select className="filter-select !w-auto" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="active">Active</option><option value="open">Not acknowledged</option>
            <option value="acknowledged">Acknowledged</option><option value="resolved">Resolved</option><option value="all">All</option>
          </select>
          <select className="filter-select !w-auto" value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity">
            <option value="">All severities</option><option value="critical">Critical</option><option value="warning">Warning</option>
          </select>
          <span className="ml-auto text-xs text-muted-foreground">{rows.length} incident{rows.length === 1 ? "" : "s"}</span>
        </div>
        <IncidentTable rows={rows} />
      </Card>
    </>
  );
}
